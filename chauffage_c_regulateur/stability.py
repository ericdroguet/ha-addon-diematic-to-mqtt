"""Stabilize Circuit C virtual room targets; observe without touching TRVs.

Five minutes of median filtering, then ten minutes of persistent demand
before small commands. A real deficit >= 1.2 C permits a fast upward
response, still subject to existing step and write rate limits.
"""
import json
import logging
import math
import os
import statistics
import time
from collections import deque

LOG = logging.getLogger("chauffage_c.stability")
FILE = "/data/stability_state.json"
STATS_TOPIC = "chauffage_c/stability"
PUBLISH_INTERVAL = 180
DISCOVERY_INTERVAL = 3600


def round_half(value):
    return round(value * 2) / 2


class Stabilizer:
    def __init__(self, publish):
        self.publish_mqtt = publish
        self.samples = deque()
        self.first_sample_time = None
        self.pending_direction = None
        self.pending_since = None
        self.last_write_mono = None
        self.events = []
        self.underheat_bins = {}
        self.last_saved = 0
        self.last_publish = 0
        self.last_discovery = 0
        self.filtered = None
        self.status = "démarrage"
        self.pending_seconds = 0
        try:
            with open(FILE, encoding="utf-8") as f:
                saved = json.load(f)
            self.events = saved.get("events", [])[-2000:]
            self.underheat_bins = saved.get("underheat_bins", {})
        except (OSError, ValueError, TypeError):
            pass

    def reset_pending(self, reset_filter=False):
        self.pending_direction = None
        self.pending_since = None
        self.pending_seconds = 0
        if reset_filter:
            self.samples.clear()
            self.first_sample_time = None
            self.filtered = None
            self.status = "attente des mesures"

    def plan(self, now, raw_target, day, night, deficit, opts):
        """Return target/None; state is only committed after successful writes."""
        if not all(isinstance(x, (int, float)) and math.isfinite(x)
                   for x in (now, raw_target, day, night, deficit)):
            self.reset_pending(reset_filter=True)
            self.status = "mesures invalides"
            return None
        filter_s = max(60, float(opts.get("stability_filter_minutes", 5)) * 60)
        confirm_s = max(120, float(opts.get("stability_confirm_minutes", 10)) * 60)
        emergency_gap = max(0.7, float(opts.get("stability_emergency_gap", 1.2)))
        min_write = max(60, float(opts["write_interval_seconds"]))
        if self.first_sample_time is None:
            self.first_sample_time = now
        self.samples.append((now, raw_target))
        while len(self.samples) > 1 and now - self.samples[0][0] > filter_s:
            self.samples.popleft()
        self.filtered = statistics.median(value for _, value in self.samples)
        urgent = deficit >= emergency_gap and raw_target >= day + 0.49
        if urgent:
            desired = raw_target
        elif now - self.first_sample_time < filter_s:
            self.reset_pending()
            self.status = "filtrage initial"
            return None
        else:
            desired = self.filtered
        low = float(opts["minimum_virtual"])
        high = float(opts["maximum_virtual"])
        up = max(0.5, float(opts["max_rise_per_write"]))
        down = max(0.5, float(opts["max_drop_per_write"]))
        stepped = min(day + up, max(day - down, desired))
        candidate = round_half(min(high, max(low, stepped)))
        if abs(candidate - day) < 0.49 and abs(candidate - night) < 0.49:
            self.reset_pending()
            self.status = "consigne stabilisée"
            return None
        if self.last_write_mono is not None and now - self.last_write_mono < min_write:
            self.status = "sécurité: fréquence écriture"
            return None
        if urgent and candidate > day + 0.49:
            self.reset_pending()
            self.status = "rattrapage prioritaire"
            return candidate
        direction = 1 if candidate > day + 0.49 else -1 if candidate < day - 0.49 else 0
        if direction != self.pending_direction:
            self.pending_direction = direction
            self.pending_since = now
        self.pending_seconds = max(0, now - self.pending_since)
        if self.pending_seconds < confirm_s:
            self.status = "confirmation demande"
            return None
        if self.last_write_mono is not None and now - self.last_write_mono < confirm_s:
            self.status = "maintien consigne"
            return None
        self.status = "modification confirmée"
        return candidate

    def committed(self, now_mono, old, new):
        self.last_write_mono = now_mono
        self.reset_pending()
        now = time.time()
        self.events.append({"time": now, "delta": round(abs(new - old), 3)})
        self._trim(now)
        self.save()
        LOG.info("Stabilisation: commande confirmée %.1f → %.1f, amplitude %.1f °C",
                 old, new, abs(new - old))

    def _trim(self, now):
        threshold = now - 86400
        self.events = [r for r in self.events
                       if isinstance(r, dict) and float(r.get("time", 0)) >= threshold]
        self.underheat_bins = {
            str(key): value for key, value in self.underheat_bins.items()
            if int(key) * 300 >= threshold - 300
        }

    def observe(self, now, elapsed, gaps, enabled):
        if not enabled:
            return
        seconds = max(0, min(120, elapsed))
        count = sum(gap >= 0.5 for _, gap in gaps)
        bucket = str(int(now // 300))
        self.underheat_bins[bucket] = round(
            float(self.underheat_bins.get(bucket, 0)) + seconds * count / 60, 2)
        self._trim(now)
        if now - self.last_saved >= 600:
            self.save()
            self.last_saved = now

    def save(self):
        try:
            tmp = FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"events": self.events, "underheat_bins": self.underheat_bins}, f)
            os.replace(tmp, FILE)
        except OSError as exc:
            LOG.warning("Statistiques stabilisation non sauvegardées: %s", exc)

    def metrics(self):
        now = time.time()
        self._trim(now)
        return {
            "consigne_filtree": self.filtered,
            "commandes_24h": len(self.events),
            "amplitude_24h": round(sum(float(e["delta"]) for e in self.events), 1),
            "temps_deficit_24h": round(sum(float(x) for x in self.underheat_bins.values())),
            "etat": self.status,
        }

    def publish(self, force=False):
        now = time.monotonic()
        if not force and self.last_publish and now - self.last_publish < PUBLISH_INTERVAL:
            return
        specs = {
            "consigne_filtree": ("Consigne virtuelle filtrée", "°C", "measurement"),
            "commandes_24h": ("Changements de consigne sur 24 h", None, "measurement"),
            "amplitude_24h": ("Amplitude totale de consigne sur 24 h", "°C", "measurement"),
            "temps_deficit_24h": ("Déficit thermique cumulé sur 24 h", "min", "measurement"),
            "etat": ("État du filtre de régulation", None, None),
        }
        try:
            if not self.last_discovery or now - self.last_discovery >= DISCOVERY_INTERVAL:
                for key, (name, unit, state_class) in specs.items():
                    config = {
                        "name": name,
                        "object_id": "chauffage_c_" + key,
                        "default_entity_id": "sensor.chauffage_c_" + key,
                        "unique_id": "chauffage_c_" + key,
                        "state_topic": STATS_TOPIC + "/" + key,
                        "expire_after": 600,
                        "device": {
                            "identifiers": ["chauffage_c_regulateur"],
                            "name": "Régulation intelligente - Circuit C",
                            "manufacturer": "Régulation locale",
                            "model": "Circuit C",
                        },
                    }
                    if unit:
                        config["unit_of_measurement"] = unit
                    if state_class:
                        config["state_class"] = state_class
                    if key == "temps_deficit_24h":
                        config["icon"] = "mdi:clock-alert-outline"
                    self.publish_mqtt("homeassistant/sensor/chauffage_c_" + key + "/config",
                                      json.dumps(config, ensure_ascii=False), True)
                self.last_discovery = now
            for key, value in self.metrics().items():
                self.publish_mqtt(STATS_TOPIC + "/" + key,
                                  "unknown" if value is None else str(value), False)
            self.last_publish = now
        except Exception as exc:
            LOG.warning("Télémétrie stabilisation momentanément indisponible: %s", exc)
