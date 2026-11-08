"""Read-only fuel usage monitor for the Diematic 10-hour burner counters.

Records observed intervals, not fictitious calendar-day fuel measurements.
Stage 1 consumes an estimated 4.64 L per recorded burner hour;
stage 2 adds an estimated 2.98 L per recorded hour.
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

LOG = logging.getLogger("chauffage_c.fioul")
ZONE = ZoneInfo("Europe/Paris")
FILE = "/data/fioul_monitor.json"
STAGES = {
    "1": ("sensor.burner1_hours", 4.64),
    "2": ("sensor.burner2_hours", 2.98),
}
MAX_ROWS = 40
TOPIC = "fioul/monitor"


def instant(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def human(timestamp):
    return datetime.fromtimestamp(timestamp, ZONE).strftime("%d/%m/%Y %H:%M")


class FuelMonitor:
    def __init__(self, api, get_state, publish):
        self.api = api
        self.get_state = get_state
        self.publish = publish
        self.snapshot = {}
        self.rows = []
        self.last_publish = 0
        self.first_run = not os.path.exists(FILE)
        if not self.first_run:
            try:
                with open(FILE, encoding="utf-8") as f:
                    stored = json.load(f)
                self.snapshot = stored.get("snapshot", {})
                self.rows = stored.get("rows", [])[-MAX_ROWS:]
            except (OSError, ValueError, TypeError) as exc:
                LOG.warning("Historique fioul local illisible: %s", exc)
                self.first_run = True

    def save(self):
        temp = FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump({"snapshot": self.snapshot, "rows": self.rows[-MAX_ROWS:]},
                      f, ensure_ascii=False)
        os.replace(temp, FILE)

    def record(self, stage, value, stamp):
        """Record a step only if the same counter genuinely advanced."""
        previous = self.snapshot.get(stage)
        if previous:
            prior = float(previous["value"])
            prev_t = float(previous["time"])
            delta = round(value - prior, 5)
            elapsed = (stamp - prev_t) / 3600
            if delta < 0:
                LOG.warning("Compteur allure %s réinitialisé : %.1f → %.1f", stage, prior, value)
            elif delta > 0 and 0.02 < elapsed < 24 * 31:
                liters = round(delta * STAGES[stage][1], 2)
                rate = liters * 24 / elapsed
                entry = {
                    "date": human(stamp),
                    "allure": stage,
                    "litres": round(liters, 1),
                    "delai_h": round(elapsed, 1),
                    "marche_h": round(delta, 1),
                    "l_j": round(rate, 1),
                    "debut": datetime.fromtimestamp(prev_t, timezone.utc).isoformat(),
                    "fin": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                    "reference_initiale": bool(previous.get("initial", False)),
                }
                self.rows.append(entry)
                self.rows = sorted(self.rows, key=lambda r: r["fin"])[-MAX_ROWS:]
                LOG.info("FIOUL: allure %s +%.1f L, intervalle %.1f h, brûleur +%.1f h, équiv %.1f L/j",
                         stage, liters, elapsed, delta, rate)
        if not previous or value != float(previous["value"]):
            self.snapshot[stage] = {"value": value, "time": stamp,
                                    "initial": not bool(previous)}
            return True
        return False

    def bootstrap(self):
        """Recover recent genuine state transitions from Home Assistant recorder.

        The first historical state is an observation, not a verified prior
        counter transition; mark the next interval as provisional.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        until = datetime.now(timezone.utc).isoformat()
        names = ",".join(sensor for sensor, _ in STAGES.values())
        params = urlencode({"filter_entity_id": names, "end_time": until,
                            "minimal_response": "1", "significant_changes_only": "1"})
        path = "/history/period/" + quote(since, safe=":-T") + "?" + params
        data = self.api("GET", path)
        if not isinstance(data, list):
            raise ValueError("Réponse du recorder inattendue")
        events = []
        for series in data:
            if not isinstance(series, list):
                continue
            series_entity = next((item.get("entity_id") for item in series
                                  if isinstance(item, dict) and item.get("entity_id")), None)
            for reading in series:
                if not isinstance(reading, dict):
                    continue
                entity = reading.get("entity_id") or series_entity
                stage = next((key for key, item in STAGES.items() if item[0] == entity), None)
                if not stage:
                    continue
                try:
                    value = float(reading["state"])
                except (TypeError, ValueError, KeyError):
                    continue
                timestamp = instant(reading.get("last_changed") or reading.get("last_updated"))
                if timestamp is not None and value >= 0:
                    events.append((timestamp, stage, value))
        events.sort()
        if not events:
            raise ValueError("Aucun compteur dans l'historique")
        for t, stage, value in events:
            self.record(stage, value, t)
        self.first_run = False
        self.save()
        LOG.info("Historique fioul initialisé : %s relevés, %s intervalles",
                 len(events), len(self.rows))

    def discovery(self):
        base = {
            "device": {
                "identifiers": ["chauffage_c_fuel_monitor"],
                "name": "Suivi fioul Diematic",
                "manufacturer": "Régulation locale",
                "model": "Compteurs brûleur",
            }
        }
        specs = (
            ("paliers", "Historique des paliers fioul", None, None),
            ("debit_allure_1", "Fioul - équivalent journalier allure 1", "L/j", "measurement"),
            ("debit_allure_2", "Fioul - équivalent journalier allure 2", "L/j", "measurement"),
        )
        for key, name, unit, stclass in specs:
            obj = {
                **base,
                "name": name,
                "unique_id": "fioul_monitor_" + key,
                "object_id": "fioul_monitor_" + key,
                "default_entity_id": "sensor.fioul_monitor_" + key,
                "state_topic": TOPIC + "/" + key,
                "icon": "mdi:chart-timeline-variant" if key == "paliers" else "mdi:fire",
            }
            if key == "paliers":
                obj["json_attributes_topic"] = TOPIC + "/rows"
                obj["entity_category"] = "diagnostic"
            else:
                obj["unit_of_measurement"] = unit
                obj["state_class"] = stclass
            self.publish("homeassistant/sensor/fioul_monitor_" + key + "/config",
                         json.dumps(obj, ensure_ascii=False), True)

    def telemetry(self):
        self.discovery()
        display = list(reversed(self.rows[-18:]))
        attrs = {
            "rows": display,
            "count": len(self.rows),
            "last_recorded": display[0]["date"] if display else "aucun",
            "method": "paliers de 10 h; litres/24 h = litres relevés / heures écoulées * 24",
        }
        self.publish(TOPIC + "/rows", json.dumps(attrs, ensure_ascii=False), True)
        self.publish(TOPIC + "/paliers", str(len(self.rows)), True)
        for stage in STAGES:
            last = next((entry for entry in reversed(self.rows)
                         if entry["allure"] == stage), None)
            self.publish(TOPIC + "/debit_allure_" + stage,
                         str(last["l_j"]) if last else "unknown", True)

    def cycle(self):
        """Called by the app every 30s. No boiler actuator is accessed."""
        try:
            if self.first_run:
                try:
                    self.bootstrap()
                except (RuntimeError, ValueError) as exc:
                    LOG.warning("Import historique fioul impossible: %s; démarrage sur les compteurs actuels", exc)
                    self.first_run = False
            changed = False
            for stage, (entity, _) in STAGES.items():
                data = self.get_state(entity)
                if not data:
                    continue
                try:
                    value = float(data.get("state"))
                except (ValueError, TypeError):
                    continue
                stamp = instant(data.get("last_changed") or data.get("last_updated"))
                if stamp is None or value < 0:
                    continue
                if self.record(stage, value, stamp):
                    changed = True
            if changed:
                self.save()
            if changed or not self.last_publish or time.monotonic() - self.last_publish > 900:
                self.telemetry()
                self.last_publish = time.monotonic()
        except Exception as exc:
            LOG.warning("Suivi fioul indisponible (sans impact sur chaudière): %s", exc)
