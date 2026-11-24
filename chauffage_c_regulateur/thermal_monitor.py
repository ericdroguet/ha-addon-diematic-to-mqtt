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
BOILER = "sensor.boiler_temp"
DHW = "sensor.hot_water_temp"
TARGET = "sensor.zone_c_target_temp"
BASE_TOPIC = "chauffage_c/thermique"
DISCOVERY_INTERVAL = 3600
PUBLISH_INTERVAL = 60
MAX_AGE_SECONDS = 600
MAX_WINDOW_SECONDS = 1800

NUMERIC = {
    "delta_t_regulation": ("Delta T régulation", "°C"),
    "variation_chaudiere_5min": ("Variation chaudière 5 min", "°C"),
    "temperature_ecs": ("Température ECS", "°C"),
    "stabilite_30min": ("Stabilité hydraulique 30 min", "%"),
    "inversion_30min": ("Inversions thermiques 30 min", "%"),
    "duree_inversion_30min": ("Durée inversion 30 min", "min"),
    "ecart_cible_moyen_30min": ("Écart absolu moyen cible 30 min", "°C"),
    "disponibilite_30min": ("Disponibilité thermique 30 min", "%"),
    "stable_seconds": ("Durée stabilité hydraulique", "s"),
    "cible_diematic": ("Température cible départ Diematic", "°C"),
    "ecart_cible_diematic": ("Écart départ réel - cible Diematic", "°C"),
    "delta_t": ("Delta T signé départ-retour", "°C"),
    "delta_t_5min": ("Delta T médian 5 min", "°C"),
    "delta_t_15min": ("Delta T médian 15 min", "°C"),
    "delta_t_30min": ("Delta T médian 30 min", "°C"),
    "ecart_depart_diematic": ("Écart Shelly-Diematic départ", "°C"),
}


def _temperature(record, now, max_value=90, check_age=True):
    """Reject non-numeric, implausible, unavailable or stale temperatures."""
    if not isinstance(record, dict):
        return None, "sonde_indisponible"
    try:
        value = float(record.get("state"))
    except (TypeError, ValueError):
        return None, "sonde_indisponible"
    if not math.isfinite(value) or not 5 <= value <= max_value:
        return None, "temperature_incoherente"
    if not check_age:
        return value, None
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
        self.boiler_samples = deque()
        self.quality = deque()
        self.stable_since = None
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
            self.stable_since = None
            self.quality.clear()
            self.boiler_samples.clear()
            LOG.warning("Surveillance thermique indisponible (régulation inchangée): %s", exc)

    def _cycle(self):
        now = self.clock()
        departure, d_error = _temperature(self.read_state(DEPARTURE), now)
        returned, r_error = _temperature(self.read_state(RETURN), now)
        diematic, _ = _temperature(self.read_state(DIEMATIC), now)
        target, _ = _temperature(self.read_state(TARGET), now, check_age=False)
        boiler, _ = _temperature(self.read_state(BOILER), now, max_value=110)
        dhw, _ = _temperature(self.read_state(DHW), now, check_age=False)
        status = d_error or r_error or "ok"
        values = {}
        if status == "ok":
            delta = round(departure - returned, 2)
            if delta < -35.0 or delta > 35.0:
                status = "delta_incoherent"
            else:
                if self.samples and now - self.samples[-1][0] > 180:
                    self.samples.clear()
                self.samples.append((now, max(0.0, delta)))
                while self.samples and now - self.samples[0][0] > MAX_WINDOW_SECONDS:
                    self.samples.popleft()
                values["delta_t"] = delta
                values["delta_t_regulation"] = max(0.0, delta)
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
        if dhw is not None:
            values["temperature_ecs"] = dhw
        if boiler is not None:
            self.boiler_samples.append((now, boiler))
        while self.boiler_samples and now - self.boiler_samples[0][0] > 360:
            self.boiler_samples.popleft()
        if boiler is not None and self.boiler_samples:
            t, old = min(self.boiler_samples, key=lambda x: abs(now - x[0] - 300))
            if abs(now - t - 300) <= 90:
                values["variation_chaudiere_5min"] = round(boiler - old, 2)
        tracking = values.get("ecart_cible_diematic")
        raw = values.get("delta_t")
        discrepancy = values.get("ecart_depart_diematic")
        stable = (status == "ok" and raw is not None and 0 <= raw <= 20
                  and tracking is not None and abs(tracking) <= 3
                  and discrepancy is not None and abs(discrepancy) <= 3
                  and 35 <= departure <= 60)
        if not stable or (self.quality and now - self.quality[-1][0] > 180):
            self.stable_since = None
        if stable and self.stable_since is None:
            self.stable_since = now
        values["stable_seconds"] = max(0, now - self.stable_since) if self.stable_since is not None else 0
        self.quality.append((now, stable, raw is not None and raw < 0, tracking,
                             stable and values["stable_seconds"] >= 300 and "delta_t_30min" in values))
        while len(self.quality) > 1 and self.quality[1][0] <= now - 1800:
            self.quality.popleft()
        spans = [(max(0, min(180, b[0] - max(a[0], now - 1800))), a)
                 for a, b in zip(self.quality, list(self.quality)[1:])]
        duration = sum(dt for dt, _ in spans)
        if duration >= 300:
            values["stabilite_30min"] = 100 * sum(dt for dt, a in spans if a[1]) / duration
            values["inversion_30min"] = 100 * sum(dt for dt, a in spans if a[2]) / duration
            values["duree_inversion_30min"] = sum(dt for dt, a in spans if a[2]) / 60
            values["disponibilite_30min"] = 100 * sum(dt for dt, a in spans if a[4]) / duration
            tracked = [(dt, abs(a[3])) for dt, a in spans if a[3] is not None]
            if sum(dt for dt, _ in tracked):
                values["ecart_cible_moyen_30min"] = sum(dt * v for dt, v in tracked) / sum(dt for dt, _ in tracked)
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
                for key in NUMERIC:
                    if key not in values:
                        self.publish(f"{BASE_TOPIC}/{key}", "unknown", False)
            self.last_publish = now
        self.last_status = status
