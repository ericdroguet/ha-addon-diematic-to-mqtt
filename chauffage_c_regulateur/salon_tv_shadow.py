"""Salon TV: shadow-only compensation. No climate or boiler write path."""
import json
import math
import statistics
import time
from collections import deque
from datetime import datetime, timezone

DANFOSS = "climate.salon_tv"
EVE = "sensor.salon_tv_eve_room_b9d2_temperature"
COV = "sensor.salon_tv_eve_room_b9d2_volatile_organic_compound_density"
THREAD = "sensor.salon_tv_eve_room_b9d2_thread_status"
TOPIC = "chauffage_c/salon_tv_shadow/data"

def numeric(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None

def age(record, now):
    try:
        stamp = record.get("last_reported") or record["last_updated"]
        seconds = now - datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        return max(0, seconds) if -30 <= seconds else None
    except (AttributeError, KeyError, ValueError, TypeError):
        return None

def fresh(record, now, limit):
    seconds = age(record, now)
    return seconds is not None and seconds <= limit

def half(value):
    return math.floor(value * 2 + 0.5) / 2

class Shadow:
    """In-memory hypothetical setpoint, reset on every safety event.
    Observes actual Danfoss target, never treats a simulated target as real.
    Stale temperature is rejected even if other Eve characteristics report.
    """
    def __init__(self):
        self.samples = deque()
        self.base = None
        self.simulated = None
        self.last_step = None
        self.last_sample = None
        self.signature = None

    def evaluate(self, now, dan, eve, cov=None, thread=None):
        attrs = (dan or {}).get("attributes", {})
        base = numeric(attrs.get("temperature"))
        internal = numeric(attrs.get("current_temperature"))
        external = numeric((eve or {}).get("state"))
        comfort = numeric(attrs.get("at_home_setpoint"))
        reduced = numeric(attrs.get("away_setpoint"))
        limits = (numeric(attrs.get("min_temp")), numeric(attrs.get("max_temp")))
        result = dict(mode="simulation uniquement", reason="initialisation",
                      eve=external, danfoss=internal, base=base,
                      proposed=base, simulated=base, correction=0.0,
                      eve_age_s=age(eve, now), cov_age_s=age(cov, now),
                      thread=(thread or {}).get("state"), samples=0,
                      gap_danfoss=round(base-internal, 2) if base is not None and internal is not None else None,
                      gap_eve=round(base-external, 2) if base is not None and external is not None else None,
                      candidate=False, steps=0)
        def reset(reason):
            self.samples.clear()
            self.base = base
            self.simulated = base
            self.last_step = now
            self.last_sample = None
            self.signature = None
            result["reason"] = reason
            return result
        if not dan or dan.get("state") != "auto" or attrs.get("room_state") != "Normal":
            return reset("mode manuel ou non normal")
        if not fresh(dan, now, 900):
            return reset("Danfoss périmé")
        if any(v is None for v in (base, internal, external, comfort, reduced, *limits)):
            return reset("mesure ou consigne indisponible")
        if not (5 <= internal <= 35 and 5 <= external <= 35 and limits[0] <= base <= limits[1]):
            return reset("mesure hors limites")
        if not fresh(eve, now, 1200):
            return reset("température Eve périmée")
        if abs(internal-external) > 6:
            return reset("écart capteurs incohérent")
        # Do not fight a manual override or infer a schedule from a corrected target.
        if min(abs(base-comfort), abs(base-reduced)) > 0.01:
            return reset("consigne manuelle ou dérogation")
        signature = (base, comfort, reduced, attrs.get("setpoint_type"))
        if self.signature != signature or self.last_sample is None or not 0 <= now-self.last_sample <= 150:
            reset("stabilisation après démarrage ou changement de consigne")
            self.signature = signature
        self.last_sample = now
        self.samples.append((now, internal, external))
        while self.samples and now-self.samples[0][0] > 660:
            self.samples.popleft()
        result["samples"] = len(self.samples)
        result["simulated"] = self.simulated
        if len(self.samples) < 8 or now-self.samples[0][0] < 540:
            result["reason"] = "lissage 10 min"
            return result
        offset = statistics.median([d-e for _,d,e in self.samples])
        target = max(limits[0], base-2, min(limits[1], base+2, half(base+offset)))
        result["proposed"] = target
        result["reason"] = "stable"
        if abs(target-self.simulated) >= 0.49:
            if now-self.last_step >= 900:
                self.simulated = round(self.simulated + math.copysign(0.5, target-self.simulated), 2)
                self.last_step = now
                result["candidate"] = True
                result["steps"] = 1
                result["reason"] = "commande simulée"
            else:
                result["reason"] = "temporisation 15 min"
        result["simulated"] = self.simulated
        result["correction"] = round(self.simulated-base, 2)
        result["offset_lisse"] = round(offset, 2)
        return result

class SalonTVMonitor:
    def __init__(self, state, publish):
        self.state = state
        self.publish = publish
        self.shadow = Shadow()
        self.last_discovery = None

    def cycle(self):
        now = time.time()
        result = self.shadow.evaluate(now, self.state(DANFOSS), self.state(EVE),
                                      self.state(COV), self.state(THREAD))
        metrics = {
            "etat": ("Salon TV simulation état", "reason", None),
            "eve": ("Salon TV température Eve", "eve", "°C"),
            "danfoss": ("Salon TV température Danfoss", "danfoss", "°C"),
            "base": ("Salon TV consigne réelle", "base", "°C"),
            "proposee": ("Salon TV consigne compensée cible", "proposed", "°C"),
            "simulee": ("Salon TV consigne simulée", "simulated", "°C"),
            "correction": ("Salon TV correction simulée", "correction", "°C"),
            "age": ("Salon TV âge température Eve", "eve_age_s", "s"),
            "gap_eve": ("Salon TV écart consigne Eve", "gap_eve", "°C"),
            "gap_danfoss": ("Salon TV écart consigne Danfoss", "gap_danfoss", "°C"),
        }
        if self.last_discovery is None or now-self.last_discovery >= 3600:
            for key,(name,field,unit) in metrics.items():
                identity = "chauffage_c_salon_tv_" + key
                config = dict(name=name, unique_id=identity, default_entity_id="sensor."+identity,
                              state_topic=TOPIC, expire_after=240,
                              value_template="{{ value_json."+field+" }}",
                              entity_category="diagnostic",
                              device={"identifiers":["chauffage_c_regulateur"],"name":"Régulation intelligente - Circuit C"})
                if unit:
                    config.update(unit_of_measurement=unit, state_class="measurement")
                else:
                    config["json_attributes_topic"] = TOPIC
                self.publish("homeassistant/sensor/"+identity+"/config",json.dumps(config,ensure_ascii=False),True)
            self.last_discovery = now
        self.publish(TOPIC,json.dumps(result,ensure_ascii=False,allow_nan=False),False)
