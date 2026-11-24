"""Read-only thermal monitoring of circuit C.

The Diematic controls the mixing valve. This observer only reads Shelly
measurements and publishes MQTT diagnostics; it never issues boiler commands.
"""

import json
import logging
import math
import statistics
import time
from collections import deque
from datetime import datetime

LOG = logging.getLogger("chauffage_c.thermal")
DEPARTURE = "sensor.shellyplusuni_c88a7beb1558_temperature_2"  # DS18B20 101
RETURN = "sensor.shellyplusuni_c88a7beb1558_temperature"  # DS18B20 100
DIEMATIC = "sensor.zone_c_departure_temp"
TARGET = "sensor.zone_c_target_temp"
BASE_TOPIC = "chauffage_c/thermique"
DISCOVERY_INTERVAL = 3600
PUBLISH_INTERVAL = 60
MAX_AGE_SECONDS = 600
MAX_WINDOW_SECONDS = 1800

NUMERIC = {
    "cible_diematic": ("Température cible départ Diematic", "°C"),
    "ecart_cible_diematic": ("Écart départ réel - cible Diematic", "°C"),
    "delta_t": ("Delta T signé départ-retour", "°C"),
    "delta_t_5min": ("Delta T médian 5 min", "°C"),
    "delta_t_15min": ("Delta T médian 15 min", "°C"),
    "delta_t_30min": ("Delta T médian 30 min", "°C"),
    "ecart_depart_diematic": ("Écart Shelly-Diematic départ", "°C"),
}


def _temperature(record, now):
    """Reject non-numeric, implausible, unavailable or stale temperatures."""
    if not isinstance(record, dict):
        return None, "sonde_indisponible"
    try:
        value = float(record.get("state"))
    except (TypeError, ValueError):
        return None, "sonde_indisponible"
    if not math.isfinite(value) or not 5 <= value <= 90:
        return None, "temperature_incoherente"
    timestamp = record.get("last_reported") or record.get("last_updated")
    if not timestamp:
        return None, "date_mesure_absente"
    try:
        instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            return None, "date_mesure_absente"
        age = now - instant.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None, "date_mesure_absente"
    if not -120 <= age <= MAX_AGE_SECONDS:
        return None, "mesure_perimee"
    return value, None


class ThermalMonitor:
    """Read-only diagnostic; publisher and state reader are injected."""

    def __init__(self, read_state, publish, clock=time.time):
        self.read_state = read_state
        self.publish = publish
        self.clock = clock
        self.samples = deque()  # (epoch, signed delta)
        self.last_discovery = 0
        self.last_publish = 0
        self.last_status = None
        self.status = "demarrage"
        self.current = {}
        self.snapshot = None  # atomic replacement; never mutate published values

    def _discover(self, now):
        if self.last_discovery and now - self.last_discovery < DISCOVERY_INTERVAL:
            return
        device = {
            "identifiers": ["chauffage_c_regulateur"],
            "name": "Régulation intelligente - Circuit C",
            "manufacturer": "Régulation locale",
            "model": "Circuit C",
        }
        for key, (name, unit) in NUMERIC.items():
            identity = f"chauffage_c_thermique_{key}"
            config = {
                "name": name,
                "object_id": identity,
                "default_entity_id": "sensor." + identity,
                "unique_id": identity,
                "state_topic": f"{BASE_TOPIC}/{key}",
                "availability_topic": f"{BASE_TOPIC}/availability",
                "payload_available": "online",
                "payload_not_available": "offline",
                "unit_of_measurement": unit,
                "state_class": "measurement",
                "expire_after": 180,
                "device": device,
            }
            self.publish(f"homeassistant/sensor/{identity}/config",
                         json.dumps(config, ensure_ascii=False), True)
        identity = "chauffage_c_thermique_etat"
        self.publish(f"homeassistant/sensor/{identity}/config", json.dumps({
            "name": "Surveillance thermique - état",
            "object_id": identity,
            "default_entity_id": "sensor." + identity,
            "unique_id": identity,
            "state_topic": f"{BASE_TOPIC}/etat",
            "expire_after": 180,
            "device": device,
        }, ensure_ascii=False), True)
        self.last_discovery = now

    def _median(self, now, seconds):
        subset = [(t, d) for t, d in self.samples if now - t <= seconds]
        if len(subset) < 4 or subset[-1][0] - subset[0][0] < max(180, seconds * 0.8):
            return None
        return round(statistics.median(d for _, d in subset), 2)

    def cycle(self):
        """Collect and publish best-effort telemetry; NEVER raise to controller."""
        try:
            self._cycle()
        except Exception as exc:
            self.snapshot = None
            self.samples.clear()
            LOG.warning("Surveillance thermique indisponible (régulation inchangée): %s", exc)

    def _cycle(self):
        now = self.clock()
        departure, d_error = _temperature(self.read_state(DEPARTURE), now)
        returned, r_error = _temperature(self.read_state(RETURN), now)
        diematic, _ = _temperature(self.read_state(DIEMATIC), now)
        target, _ = _temperature(self.read_state(TARGET), now)
        status = d_error or r_error or "ok"
        values = {}
        if status == "ok":
            delta = round(departure - returned, 2)
            if delta < -1.0 or delta > 35.0:
                status = "delta_incoherent"
            else:
                self.samples.append((now, delta))
                while self.samples and now - self.samples[0][0] > MAX_WINDOW_SECONDS:
                    self.samples.popleft()
                values["delta_t"] = delta
                for minutes in (5, 15, 30):
                    median = self._median(now, minutes * 60)
                    if median is not None:
                        values[f"delta_t_{minutes}min"] = median
                if diematic is not None:
                    values["ecart_depart_diematic"] = round(departure - diematic, 2)
        if status != "ok":
            self.samples.clear()
        if target is not None:
            values["cible_diematic"] = target
            if diematic is not None:
                values["ecart_cible_diematic"] = round(diematic - target, 2)
        self.status = status
        self.current = values
        self.snapshot = (time.monotonic(), status, departure, returned, dict(values))
        due = not self.last_publish or now - self.last_publish >= PUBLISH_INTERVAL
        if status != self.last_status or due:
            self._discover(now)
            self.publish(f"{BASE_TOPIC}/availability",
                         "online" if status == "ok" else "offline", True)
            self.publish(f"{BASE_TOPIC}/etat", status, False)
            if status == "ok":
                for key, value in values.items():
                    self.publish(f"{BASE_TOPIC}/{key}", f"{value:.2f}", False)
                for minutes in (5, 15, 30):
                    if f"delta_t_{minutes}min" not in values:
                        self.publish(f"{BASE_TOPIC}/delta_t_{minutes}min", "unknown", False)
                for key in ("ecart_depart_diematic", "cible_diematic", "ecart_cible_diematic"):
                    if key not in values:
                        self.publish(f"{BASE_TOPIC}/{key}", "unknown", False)
            self.last_publish = now
        self.last_status = status
