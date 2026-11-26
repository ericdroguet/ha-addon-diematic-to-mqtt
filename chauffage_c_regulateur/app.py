"""Régulateur indépendant du circuit C, HA OS add-on. Safe by default."""
import json
import logging
import os
import signal
import threading
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib import request, error
from fuel_monitor import FuelMonitor
from stability import Stabilizer
from thermal_monitor import ThermalMonitor
from thermal_model import HydraulicModel
from solar_monitor import SolarMonitor
from solar_forecast import SolarForecast

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("chauffage_c")
BASE = "http://supervisor/core/api"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
ROOMS = (
    "climate.sejour",
    "climate.salon_tv",
    "climate.cuisine_2",
    "climate.entree",
    "climate.bureau",
    "climate.buanderie",
    "climate.chambre_adaline",
    "climate.chambre_octave",
    "climate.chambre_invite",
    "climate.chambre_monique",
    "climate.sdb_valeric",
)
EVE_ROOM = "climate.chambre_radiateurs_eve"
EVE_SENSOR = "sensor.eve_thermo_control_20eck9901_temperature"
EVE_VALVES = (
    "sensor.eve_thermo_20ebp1701_position_de_la_vanne",
    "sensor.eve_thermo_20ebp1701_position_de_la_vanne_2",
)
ZONE_DAY = "number.zone_c_temp_day"
ZONE_NIGHT = "number.zone_c_temp_night"
ZONE_MODE = "select.zone_c_mode"
ZONE_DEPARTURE = "sensor.zone_c_departure_temp"
OUTDOOR = "sensor.ext_temp"
WEATHER = "weather.forecast_maison"
INFLUENCE_ENTITY = "input_number.circuit_c_influence_pieces"
WEATHER_LIMIT_ENTITY = "input_number.circuit_c_correction_meteo_maximale"
METRIC_PREFIX = "chauffage_c"
METRICS = (
    ("meteo_calculee", "Météo calculée", "Correction météo plafonnée avant protection"),
    ("meteo_retenue", "Météo retenue", "Correction après protection progressive des pièces"),
    ("meteo_effet_consigne", "Effet météo sur consigne", "Différence entre consigne proposée avec et sans météo, après arrondi et limitation"),
)
HOUSE_MODE = "select.meylan_house_mode"
HOUSE_STATE = "sensor.meylan_house_state"
LOCAL_TZ = ZoneInfo("Europe/Paris")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
STOP = False


def sig_stop(_sig, _frame):
    global STOP
    STOP = True


signal.signal(signal.SIGTERM, sig_stop)
signal.signal(signal.SIGINT, sig_stop)


def api(method, path, payload=None):
    if not TOKEN:
        raise RuntimeError("SUPERVISOR_TOKEN absent")
    data = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=12) as response:
            return json.load(response) if response.status != 204 else None
    except (error.HTTPError, error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Home Assistant API {method} {path}: {exc}") from exc


def state(entity):
    try:
        return api("GET", "/states/" + entity)
    except RuntimeError:
        return None


def val(value):
    try:
        out = float(value)
        return out if -100 < out < 150 else None
    except (ValueError, TypeError):
        return None


def call_number(entity, temperature):
    api("POST", "/services/number/set_value", {
        "entity_id": entity, "value": temperature
    })


def live_setting(entity, default, minimum, maximum):
    """Read the current HA helper at every cycle; fall back safely if absent."""
    record = state(entity)
    value = val(record.get("state")) if record else None
    if value is None:
        return default
    return min(maximum, max(minimum, value))


def mqtt_publish(topic, payload, retain=False):
    api("POST", "/services/mqtt/publish", {
        "topic": topic, "payload": payload, "retain": retain
    })



def read_options():
    with open("/data/options.json", encoding="utf-8") as f:
        opts = json.load(f)
    for key in ("minimum_virtual", "maximum_virtual", "manual_day", "manual_night"):
        opts[key] = float(opts[key])
    if not 6 <= opts["minimum_virtual"] < opts["maximum_virtual"] <= 30:
        raise ValueError("Bornes virtuelles incohérentes")
    opts["poll_seconds"] = max(15, int(opts["poll_seconds"]))
    opts["write_interval_seconds"] = max(60, int(opts["write_interval_seconds"]))
    opts["weather_anticipation"] = bool(opts.get("weather_anticipation", False))
    opts["schedule_anticipation"] = bool(opts.get("schedule_anticipation", False))
    opts["schedule_preheat_minutes"] = max(30, min(240, int(opts.get("schedule_preheat_minutes", 120))))
    opts["schedule_setback_minutes"] = max(0, min(120, int(opts.get("schedule_setback_minutes", 45))))
    opts["weather_max_boost"] = max(0.0, min(1.5, float(opts.get("weather_max_boost", 1.5))))
    opts["stability_filter_minutes"] = max(1, min(15, int(opts.get("stability_filter_minutes", 5))))
    opts["stability_confirm_minutes"] = max(2, min(30, int(opts.get("stability_confirm_minutes", 10))))
    opts["stability_emergency_gap"] = max(0.7, min(3.0, float(opts.get("stability_emergency_gap", 1.2))))
    return opts


@dataclass
class Demand:
    valid: int = 0
    count: int = 0
    max_gap: float = 0
    priority: str = "Aucune"
    near_count: int = 0
    gaps: list = field(default_factory=list)
    preheat_rooms: list = field(default_factory=list)
    setback_rooms: list = field(default_factory=list)
    next_comfort_minutes: float | None = None
    next_setback_minutes: float | None = None
    actual_max_gap: float = 0.0
    actual_rooms: list = field(default_factory=list)
    schedule_enabled: bool = False


def add_room(result, name, current, target, heating=False):
    if current is None or target is None:
        return
    result.valid += 1
    gap = target - current
    result.gaps.append((name, max(0.0, gap)))
    if -0.35 <= gap <= 0.15:
        result.near_count += 1
    if gap > 0.15 or (heating and gap > -0.2):
        result.count += 1
        score = max(0.2, gap)
        if score > result.max_gap:
            result.max_gap = score
            result.priority = name


def schedule_transitions(attributes, now):
    """Minutes to next Comfort / Reduced switch, per-room weekly schedule.
    
    Home Assistant exposes the Danfoss schedule as 'HH:MM-HH:MM' comfort
    windows; 24:00 is a valid end and windows may cross midnight.
    """
    schedule = attributes.get("schedule")
    if not isinstance(schedule, dict):
        return None, None
    windows = []
    for offset in range(-1, 3):
        day = now.date() + timedelta(days=offset)
        intervals = schedule.get(WEEKDAYS[day.weekday()], [])
        if not isinstance(intervals, list):
            continue
        midnight = datetime.combine(day, datetime.min.time(), tzinfo=LOCAL_TZ)
        for period in intervals:
            if not isinstance(period, str):
                continue
            try:
                begin, end = period.split("-")
                h0, m0 = (int(x) for x in begin.split(":"))
                h1, m1 = (int(x) for x in end.split(":"))
                if not (0 <= h0 <= 23 and 0 <= m0 < 60
                        and 0 <= h1 <= 24 and 0 <= m1 < 60
                        and (h1 < 24 or m1 == 0)):
                    continue
                first, last = h0 * 60 + m0, h1 * 60 + m1
                if last <= first:
                    last += 1440
                windows.append((midnight + timedelta(minutes=first),
                                midnight + timedelta(minutes=last)))
            except (ValueError, TypeError):
                continue
    now_ts = now.timestamp()
    current_end = [end.timestamp() for start, end in windows
                   if start.timestamp() <= now_ts < end.timestamp()]
    future_starts = [start.timestamp() for start, end in windows
                     if start.timestamp() > now_ts]
    if current_end:
        return None, (min(current_end) - now_ts) / 60
    return ((min(future_starts) - now_ts) / 60 if future_starts else None), None


def adjust_for_schedule(attributes, current, target, now, opts, demand):
    """Apply a gentle prospective demand, never change a Danfoss setpoint.

    During Reduced, ramp toward the *future* comfort target up to two hours
    ahead. Near the end of Comfort, taper this room's demand toward Reduced.
    Every other room retains its own contribution and the boiler safety
    constraints remain unchanged.
    """
    next_comfort, next_setback = schedule_transitions(attributes, now)
    comfort = val(attributes.get("at_home_setpoint"))
    reduced = val(attributes.get("away_setpoint"))
    if comfort is None or reduced is None or comfort <= reduced + 0.3:
        return target, False
    if next_comfort is not None:
        if demand.next_comfort_minutes is None or next_comfort < demand.next_comfort_minutes:
            demand.next_comfort_minutes = next_comfort
        window = opts["schedule_preheat_minutes"]
        if 0 <= next_comfort < window and comfort > target + 0.3:
            progress = (window - next_comfort) / window
            predictive_gap = max(0.0, comfort - current) * progress
            if predictive_gap > max(0.0, target - current) + 0.05:
                demand.preheat_rooms.append(attributes.get("friendly_name", "?"))
                return max(target, current + predictive_gap), False
    if next_setback is not None:
        if demand.next_setback_minutes is None or next_setback < demand.next_setback_minutes:
            demand.next_setback_minutes = next_setback
        window = opts["schedule_setback_minutes"]
        if 0 < next_setback < window and target > reduced + 0.3:
            gap = max(0.0, target - current)
            reduced_gap = max(0.0, reduced - current)
            fade = 0.35 + 0.65 * next_setback / window
            projected_gap = max(reduced_gap, gap * fade)
            if projected_gap < gap - 0.05:
                demand.setback_rooms.append(attributes.get("friendly_name", "?"))
                return current + projected_gap, True
    return target, False


def collect(opts=None):
    opts = opts or {}
    result = Demand()
    enabled = opts.get("schedule_anticipation", False)
    if enabled:
        house_mode = state(HOUSE_MODE)
        house_state = state(HOUSE_STATE)
        result.schedule_enabled = bool(
            house_mode and house_mode.get("state") == "schedule"
            and house_state and house_state.get("state") in ("at_home", "asleep")
        )
    now = datetime.now(LOCAL_TZ)
    for room in ROOMS:
        record = state(room)
        if not record or record.get("state") not in ("auto", "heat"):
            continue
        attrs = record.get("attributes", {})
        current = val(attrs.get("current_temperature"))
        target = val(attrs.get("temperature"))
        if current is None or target is None:
            continue
        result.actual_rooms.append((room, current, target))
        result.actual_max_gap = max(result.actual_max_gap, target - current)
        heating = attrs.get("hvac_action") == "heating"
        if result.schedule_enabled and record.get("state") == "auto" and attrs.get("room_state") == "Normal":
            target, fade_heating = adjust_for_schedule(attrs, current, target, now, opts, result)
            if fade_heating:
                heating = False
        add_room(result, attrs.get("friendly_name", room), current, target, heating)
    eve = state(EVE_ROOM)
    thermometer = state(EVE_SENSOR)
    if eve and thermometer and eve.get("state") == "heat":
        result.actual_max_gap = max(result.actual_max_gap, (val(eve.get("attributes", {}).get("temperature")) or 0) - (val(thermometer.get("state")) or 0))
        eve_current = val(thermometer.get("state"))
        eve_target = val(eve.get("attributes", {}).get("temperature"))
        if eve_current is not None and eve_target is not None:
            result.actual_rooms.append((EVE_ROOM, eve_current, eve_target))
        valves = [state(e) for e in EVE_VALVES]
        opening = any(v and val(v.get("state")) is not None
                      and val(v["state"]) > 5 for v in valves)
        add_room(
            result, "Chambre Eve", val(thermometer.get("state")),
            val(eve.get("attributes", {}).get("temperature")), opening,
        )
    return result


def forecast_change(current_outdoor):
    """Weighted forecast change at 1, 2, 3 and 6 hours."""
    try:
        result = api("POST", "/services/weather/get_forecasts?return_response=true",
                     {"entity_id": WEATHER, "type": "hourly"})
        hourly = (result or {}).get("service_response", {}).get(WEATHER, {}).get("forecast", [])
        now = datetime.now(timezone.utc)
        points = [(0.0, current_outdoor)]
        for item in hourly:
            stamp = datetime.fromisoformat(item["datetime"].replace("Z", "+00:00"))
            hours = (stamp - now).total_seconds() / 3600
            temp = val(item.get("temperature"))
            if 0 < hours <= 8 and temp is not None:
                points.append((hours, temp))
        points.sort()
        if points[-1][0] < 5.5:
            return None
        def interpolate(hour):
            for (h0, t0), (h1, t1) in zip(points, points[1:]):
                if h0 <= hour <= h1:
                    return t0 + (t1 - t0) * (hour - h0) / (h1 - h0)
            return points[-1][1]
        return sum(weight * (interpolate(hour) - current_outdoor)
                   for hour, weight in ((1, .35), (2, .30), (3, .20), (6, .15)))
    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        LOG.warning("Prévisions indisponibles: %s", exc)
        return None


def weather_adjustment(change, outdoor, demand, max_boost):
    """Return (calculated, protected, protection_factor).

    The old rule cancelled all negative weather effects as soon as any
    room deficit exceeded 0.5C. Instead, protect comfort progressively:
    for each 1C beyond that threshold the negative correction is halved
    again, but retain at least 25% of the forecast's contribution.
    """
    if change is None or outdoor is None or demand.valid < 8:
        return None, 0.0, 1.0
    calculated = max(-max_boost, min(max_boost, 0.12 * (10 - outdoor) - 0.30 * change))
    if calculated >= 0:
        return calculated, calculated, 1.0
    factor = max(0.25, 1.0 - 0.5 * max(0.0, demand.actual_max_gap - 0.5))
    return calculated, calculated * factor, factor


def persistent_index(demand, deficits, elapsed):
    active = set()
    for name, gap in demand.gaps:
        active.add(name)
        prior = deficits.get(name, 0.0)
        if gap >= 0.25:
            deficits[name] = min(7200.0, prior + elapsed)
        elif gap <= 0.10:
            deficits[name] = max(0.0, prior - 2 * elapsed)
        else:
            deficits[name] = max(0.0, prior - 0.5 * elapsed)
    for name in list(deficits):
        if name not in active:
            deficits.pop(name, None)
    ranked = sorted(((gap, deficits.get(name, 0.0)) for name, gap in demand.gaps
                     if gap >= 0.25), reverse=True)[:3]
    return (sum(min(1.0, seconds / 7200.0) for _, seconds in ranked) / len(ranked)
            if ranked else 0.0)


def round_half(value):
    return round(value * 2) / 2


def decide(demand, previous, opts, anticipation=0.0, persistence=0.0):
    gaps = sorted((gap for _, gap in demand.gaps), reverse=True)
    representative = (0.5 * gaps[0] + 0.5 * sum(gaps[:3]) / min(3, len(gaps))) if gaps else 0.0
    raw = (20.5 + opts.get("room_influence", 1.5) * min(representative, 2.0)
           + 0.25 * min(demand.count, 8)
           + 0.8 * persistence + anticipation)
    raw = min(opts["maximum_virtual"], max(opts["minimum_virtual"], raw))
    if previous is None:
        previous = min(opts["maximum_virtual"], max(opts["minimum_virtual"], opts["manual_day"]))
    limit_up = max(0.5, float(opts["max_rise_per_write"]))
    limit_down = max(0.5, float(opts["max_drop_per_write"]))
    stepped = min(previous + limit_up, max(previous - limit_down, raw))
    return round_half(min(opts["maximum_virtual"], max(opts["minimum_virtual"], stepped)))


def thermal_candidate(demand, delta, departure, discrepancy, persistence):
    if any(x is None for x in (delta, departure, discrepancy)) or not (0 <= delta <= 20 and 35 <= departure <= 60 and abs(discrepancy) <= 3):
        return None
    if demand.preheat_rooms or demand.setback_rooms:
        return 0.0
    if demand.actual_max_gap >= 0.7 and demand.count >= 3 and delta >= 5 and persistence >= 0.25:
        return 0.5
    if demand.actual_max_gap <= 0.15 and demand.count <= 1 and delta <= 2:
        return -0.5
    return 0.0


class ThermalCorrection:
    def __init__(self):
        self.pending = None
        self.since = None
        self.value = 0.0

    def step(self, now, candidate):
        if candidate is None or candidate == 0:
            self.pending = self.since = None
            self.value = 0.0
            return self.value
        if candidate != self.pending:
            self.pending, self.since = candidate, now
            self.value = 0.0
        if now - self.since >= 900:
            self.value = candidate
        return self.value


def thermal_input(observer, demand, persistence, diagnostic=None, model=None, forecast=None, snapshot=None):
    def reject(reason):
        if diagnostic is not None:
            diagnostic['reason'] = reason
        return None
    if snapshot is None:
        snapshot = observer.snapshot
    if snapshot is None:
        LOG.info("Thermique rejet: historique indisponible")
        return reject("historique")
    stamp, status, departure, returned, values = snapshot
    if not 0 <= time.monotonic() - stamp <= 180:
        LOG.info("Thermique rejet: calcul périmé")
        return reject("perime")
    if status != "ok":
        LOG.info("Thermique rejet: %s", status)
        return reject("sonde")
    delta = values.get("delta_t_30min")
    discrepancy = values.get("ecart_depart_diematic")
    if delta is None:
        return reject("couverture_insuffisante")
    if discrepancy is None:
        return reject("diematic_indisponible")
    if not 0 <= departure - returned <= 20:
        LOG.info("Thermique rejet: delta instantané hors limites")
        return reject("delta_instantane")
    tracking = values.get("ecart_cible_diematic")
    target = values.get("cible_diematic")
    if tracking is None or target is None:
        LOG.info("Thermique rejet: cible Diematic indisponible")
        return reject("cible")
    if abs(tracking) > 3.0:
        LOG.info("Thermique rejet: transitoire Diematic; cible=%.2f écart réel-cible=%+.2f", target, tracking)
        return reject("transitoire")
    LOG.info("Suivi Diematic: cible=%.2f écart réel-cible=%+.2f", target, tracking)
    if values.get("stable_seconds", 0) < 300:
        LOG.info("Thermique rejet: stabilité hydraulique moins de 5 min")
        return reject("stabilite")
    if values.get("confiance", 0) < 60:
        return reject("confiance")
    if not (0 <= delta <= 20 and 35 <= departure <= 60 and abs(discrepancy) <= 3):
        return reject("limites_physiques")
    if demand.preheat_rooms or demand.setback_rooms:
        return 0.0
    if model is None:
        return reject("modele_indisponible")
    candidate, reason, info = model.evaluate(time.monotonic(), demand.actual_rooms, forecast, persistence)
    if diagnostic is not None:
        diagnostic.update(reason=reason, model=info)
    LOG.info("Modèle hydraulique: %s %s", reason, json.dumps(info, ensure_ascii=False))
    if candidate is None:
        return reject(reason)
    if candidate == -0.5 and (values.get("inversion_30min") is None or values["inversion_30min"] > 10):
        LOG.info("Thermique rejet: inversions récentes, réduction non fiable")
        return reject("inversions")
    if candidate is None:
        if diagnostic is not None:
            diagnostic["reason"] = "limites_physiques"
        LOG.info("Thermique rejet: limites physiques; delta30=%.2f départ=%.2f écart=%.2f", delta, departure, discrepancy)
    else:
        LOG.info("Thermique valide: delta30=%.2f départ=%.2f écart=%.2f", delta, departure, discrepancy)
    return candidate



class ThermalStats:
    """Bounded persistent five-minute buckets; diagnostic only."""
    PATH = "/data/thermal_stats.json"
    def __init__(self):
        self.buckets = {}
        self.previous = None
        self.last_flush = 0
        self.last_discovery = 0
        try:
            with open(self.PATH, encoding="utf-8") as f:
                self.buckets = json.load(f)
            if not isinstance(self.buckets, dict):
                self.buckets = {}
        except (OSError, ValueError, TypeError):
            pass

    def observe(self, reason, candidate, correction, effect, snapshot, control):
        try:
            now = time.time()
            mono = time.monotonic()
            key = str(int(now // 300))
            cutoff = int(now // 300) - 2015
            self.buckets = {k: v for k, v in self.buckets.items()
                            if cutoff <= int(k) <= int(key)}
            b = self.buckets.setdefault(key, {})
            def add(k, n=1):
                b[k] = b.get(k, 0) + n
            add("cycles")
            add("valid" if candidate is not None else "reject_" + reason)
            if candidate is not None:
                add("candidate_positive" if candidate > 0 else "candidate_negative" if candidate < 0 else "candidate_neutral")
            if control and correction and self.previous is not None and correction != self.previous[1]:
                add("positive" if correction > 0 else "negative")
            if self.previous is not None:
                old_time, old_correction, old_effect, old_inverse, old_control = self.previous
                dt = min(120, max(0, mono - old_time))
                add("observed_s", dt)
                if old_control and old_correction:
                    add("active_s", dt)
                if old_control and old_effect:
                    add("effect_s", dt)
                if old_inverse:
                    add("inverse_s", dt)
            inverse = bool(snapshot and 0 <= mono - snapshot[0] <= 180
                           and snapshot[1] == "ok" and snapshot[4].get("delta_t", 0) < 0)
            self.previous = (mono, correction, effect, inverse, control)
            if not self.last_flush or mono - self.last_flush >= 300:
                self.flush()
                self.last_flush = mono
                self.publish(now)
        except Exception as exc:
            LOG.warning("Statistiques thermiques indisponibles: %s", exc)

    def flush(self):
        with open(self.PATH + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.buckets, f, separators=(",", ":"))
        os.replace(self.PATH + ".tmp", self.PATH)

    def publish(self, now):
        metrics = {"cycles": ("Cycles observés", None), "valid_pct": ("Cycles valides", "%"),
                   "positive": ("Activations positives", None), "negative": ("Activations négatives", None),
                   "active_min": ("Correction active", "min"), "effect_min": ("Effet sur proposition", "min"),
                   "inverse_min": ("Inversions observées", "min"), "observed_h": ("Durée observée", "h"),
                   "candidate_positive": ("Candidats positifs", None),
                   "candidate_negative": ("Candidats négatifs", None),
                   "candidate_neutral": ("Candidats neutres", None),
                   "rejects": ("Motifs de rejet", None)}
        discover = now - self.last_discovery >= 3600
        for hours in (24, 168):
            totals = {}
            for k, b in self.buckets.items():
                if int(k) > int(now // 300) - hours * 12:
                    for name, n in b.items():
                        totals[name] = totals.get(name, 0) + n
            cycles = totals.get("cycles", 0)
            reasons = {k[7:]: v for k, v in totals.items() if k.startswith("reject_")}
            values = dict(totals, candidate_positive=totals.get("candidate_positive", 0),
                          candidate_negative=totals.get("candidate_negative", 0),
                          candidate_neutral=totals.get("candidate_neutral", 0), valid_pct=round(100 * totals.get("valid", 0) / cycles, 1) if cycles else "None",
                          active_min=round(totals.get("active_s", 0)/60, 1),
                          effect_min=round(totals.get("effect_s", 0)/60, 1),
                          inverse_min=round(totals.get("inverse_s", 0)/60, 1),
                          observed_h=round(totals.get("observed_s", 0)/3600, 2),
                          rejects=json.dumps(reasons, ensure_ascii=False, separators=(",", ":")))
            for key, (name, unit) in metrics.items():
                identity = f"chauffage_c_stats_{key}_{hours}h"
                topic = f"chauffage_c/stats/{key}/{hours}h"
                if discover:
                    config = {"name": f"{name} {hours}h", "unique_id": identity,
                              "default_entity_id": "sensor." + identity, "object_id": identity,
                              "state_topic": topic, "expire_after": 900, "entity_category": "diagnostic",
                              "device": {"identifiers": ["chauffage_c_regulateur"], "name": "Régulation intelligente - Circuit C"}}
                    if unit:
                        config["unit_of_measurement"] = unit
                    if key == "rejects":
                        config["value_template"] = "{{ value_json | length }}"
                        config["json_attributes_topic"] = topic
                    mqtt_publish(f"homeassistant/sensor/{identity}/config", json.dumps(config, ensure_ascii=False), True)
                mqtt_publish(topic, str(values.get(key, 0)), False)
        self.last_discovery = now if discover else self.last_discovery


class Controller:
    def __init__(self, opts, observer):
        self.observer = observer
        self.opts = opts
        self.last_write = 0
        self.last_command = None
        self.touched = False
        self.was_allowed = False
        self.last_forecast_check = 0
        self.cached_change = None
        self.deficits = {}
        self.last_cycle = time.monotonic()
        self.last_save = 0
        self.last_discovery = 0
        self.stabilizer = Stabilizer(mqtt_publish)
        self.thermal = ThermalCorrection()
        self.hydraulic_model = HydraulicModel()
        self.stats = ThermalStats()
        try:
            with open("/data/thermal_state.json", encoding="utf-8") as f:
                self.deficits = {str(k): min(7200.0, max(0.0, float(v)))
                                 for k, v in json.load(f).get("deficits", {}).items()}
        except (OSError, ValueError, TypeError):
            pass

    def save_memory(self):
        path = "/data/thermal_state.json"
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump({"deficits": self.deficits}, f)
        os.replace(temp, path)

    def publish_weather_metrics(self, calculated, retained, real_effect):
        """Expose real, recorder-backed MQTT sensors through HA discovery.

        Metrics are informational only. A failed telemetry publish must
        never interrupt the boiler's normal regulation loop.
        """
        try:
            now = time.monotonic()
            if not self.last_discovery or now - self.last_discovery >= 3600:
                for key, name, description in METRICS:
                    topic = f"homeassistant/sensor/{METRIC_PREFIX}_{key}/config"
                    data = {
                        "name": name,
                        "object_id": f"{METRIC_PREFIX}_{key}",
                        "default_entity_id": f"sensor.{METRIC_PREFIX}_{key}",
                        "unique_id": f"{METRIC_PREFIX}_{key}",
                        "state_topic": f"{METRIC_PREFIX}/diagnostics/{key}",
                        "unit_of_measurement": "°C",
                        "state_class": "measurement",
                        "expire_after": 180,
                        "entity_category": "diagnostic",
                        "device": {
                            "identifiers": ["chauffage_c_regulateur"],
                            "name": "Régulation intelligente - Circuit C",
                            "manufacturer": "Régulation locale",
                            "model": "Circuit C",
                        },
                    }
                    mqtt_publish(topic, json.dumps(data, ensure_ascii=False), True)
                self.last_discovery = now
            values = (calculated, retained, real_effect)
            for (key, _, _), value in zip(METRICS, values):
                mqtt_publish(
                    f"{METRIC_PREFIX}/diagnostics/{key}",
                    "None" if value is None else f"{value:+.3f}",
                    False,
                )
        except Exception as exc:
            LOG.warning("Télémétrie MQTT indisponible: %s", exc)

    def restore(self):
        if not self.touched:
            return
        LOG.warning("Retour aux consignes manuelles jour/nuit")
        call_number(ZONE_DAY, self.opts["manual_day"])
        call_number(ZONE_NIGHT, self.opts["manual_night"])
        self.touched = False
        self.last_command = None

    def cycle(self):
        opts = self.opts
        control = bool(opts.get("automatic_control", False)) and not bool(opts.get("dry_run", True))
        demand = collect(opts)
        now_cycle = time.monotonic()
        elapsed = max(0.0, min(120.0, now_cycle - self.last_cycle))
        self.last_cycle = now_cycle
        persistence = persistent_index(demand, self.deficits, elapsed)
        if now_cycle - self.last_save > 600:
            try:
                self.save_memory()
                self.last_save = now_cycle
            except OSError as exc:
                LOG.warning("Mémoire thermique non enregistrée: %s", exc)
        mode = state(ZONE_MODE)
        departure = state(ZONE_DEPARTURE)
        outdoor = state(OUTDOOR)
        day = state(ZONE_DAY)
        night = state(ZONE_NIGHT)
        good_numbers = all(x is not None and val(x.get("state")) is not None
                           for x in (departure, outdoor, day, night))
        safe = (demand.valid >= 8 and good_numbers and mode is not None
                and mode.get("state") == "AUTO" and val(departure["state"]) <= 60)
        if not safe:
            self.hydraulic_model.reset()
            self.thermal.step(now_cycle, None)
            self.stats.observe('securite', None, 0, 0, self.observer.snapshot, control)
            self.stabilizer.reset_pending(reset_filter=True)
            self.stabilizer.status = "pause sécurité"
            self.stabilizer.publish()
            LOG.warning("SÉCURITÉ: mesures=%s, mode=%s, départ=%s; restauration si nécessaire",
                        demand.valid, mode.get("state") if mode else None,
                        departure.get("state") if departure else None)
            if control and self.touched:
                self.restore()
            return
        current = val(day["state"])
        # Use the actual Diematic setting rather than an old issued command;
        # manual changes and startup restoration must be reflected immediately.
        previous = current
        anticipation = 0.0
        if opts["weather_anticipation"]:
            now_forecast = time.monotonic()
            if not self.last_forecast_check or now_forecast - self.last_forecast_check >= 900:
                self.cached_change = forecast_change(val(outdoor["state"]))
                self.last_forecast_check = now_forecast
            # Correction computed below with the current dashboard setting.
        live_opts = dict(opts)
        live_opts["room_influence"] = live_setting(INFLUENCE_ENTITY, 1.5, 0.5, 3.0)
        live_limit = live_setting(WEATHER_LIMIT_ENTITY, opts["weather_max_boost"], 0.0, 1.5)
        if opts["weather_anticipation"]:
            calculated, anticipation, protection = weather_adjustment(
                self.cached_change, val(outdoor["state"]), demand, live_limit
            )
        else:
            calculated, protection = None, 1.0
        baseline = decide(demand, previous, live_opts, 0.0, persistence)
        proposed = decide(demand, previous, live_opts, anticipation, persistence)
        effect = round(proposed - baseline, 3)
        diagnostic = {'reason': 'desactive'}
        try:
            snapshot = self.observer.snapshot
            if opts.get('thermal_correction', True):
                self.hydraulic_model.observe(time.monotonic(), snapshot, demand.actual_rooms, val(outdoor["state"]))
                candidate = thermal_input(self.observer, demand, persistence, diagnostic,
                                          model=self.hydraulic_model,
                                          forecast=self.cached_change if opts["weather_anticipation"] else None,
                                          snapshot=snapshot)
            else:
                self.hydraulic_model.reset()
                candidate = None
        except Exception as exc:
            LOG.warning('Correction thermique indisponible: %s', exc)
            self.hydraulic_model.reset()
            candidate = None
            diagnostic['reason'] = 'exception'
        correction = self.thermal.step(now_cycle, candidate)
        before_thermal = proposed
        if correction:
            proposed = decide(demand, previous, live_opts, anticipation + correction, persistence)
        LOG.info('Thermique: candidat=%s correction=%+.1f effet=%+.1f', candidate, correction, proposed - before_thermal)
        self.stats.observe(diagnostic['reason'], candidate, correction, proposed - before_thermal, self.observer.snapshot, control)
        # Diagnostic only: bounded local trace for retrospective analysis.
        try:
            snap = self.observer.snapshot
            day_index = int(time.time() // 86400)
            path = "/data/thermal_trace_%d.jsonl" % (day_index % 7)
            if os.path.exists(path) and int(os.stat(path).st_mtime // 86400) != day_index:
                os.remove(path)
            if not os.path.exists(path) or os.path.getsize(path) < 4 * 1024 * 1024:
                with open(path, "a", encoding="utf-8") as trace:
                    trace.write(json.dumps({
                        "ts": time.time(), "version": "0.4.11",
                        "status": snap[1] if snap else None,
                        "departure": snap[2] if snap else None,
                        "return": snap[3] if snap else None,
                        "thermal": snap[4] if snap else {},
                        "snapshot_age": time.monotonic() - snap[0] if snap else None,
                        "actual_max_gap": demand.actual_max_gap, "count": demand.count,
                        "preheat": bool(demand.preheat_rooms), "setback": bool(demand.setback_rooms),
                        "persistence": persistence, "reason": diagnostic['reason'],
                        "rooms": demand.actual_rooms, "model": diagnostic.get("model", {}),
                        "candidate": candidate, "correction": correction,
                        "current": current, "before": before_thermal, "proposed": proposed,
                        "control": control}, separators=(",", ":")) + "\n")
        except Exception as exc:
            LOG.warning("Trace thermique indisponible: %s", exc)
        self.publish_weather_metrics(calculated, anticipation if calculated is not None else None,
                                     effect if calculated is not None else None)
        if opts["weather_anticipation"]:
            LOG.info("Météo: variation prévue pondérée 1-6h=%s °C; zones_proches=%d; calculée=%s °C; protégée=%+.2f °C; facteur=%.2f; effet_consigne=%+.2f °C; influence_pièces=%.1f; plafond_météo=%.1f; sans_météo=%.1f °C; avec_météo=%.1f °C",
                     self.cached_change, demand.near_count,
                     f"{calculated:+.2f}" if calculated is not None else "indisponible",
                     anticipation, protection, effect, live_opts["room_influence"], live_limit,
                     baseline, proposed)
        LOG.info("zones_valides=%d demandes=%d ecart_max=%.2f priorite=%s départ=%.1f extérieur=%.1f"
                 " consigne_actuelle=%.1f consigne_proposée=%.1f pilotage=%s",
                 demand.valid, demand.count, demand.max_gap, demand.priority,
                 val(departure["state"]), val(outdoor["state"]), current, proposed,
                 "ACTIF" if control else "SIMULATION")
        LOG.info("Rattrapage: indice_persistance=%.2f; zones_en_demande=%d", persistence, demand.count)
        if opts.get("schedule_anticipation"):
            LOG.info("Planning: %s; prochain_confort=%s min; prochain_réduit=%s min; "
                     "préchauffage=%s; réduction_anticipée=%s",
                     "ACTIF" if demand.schedule_enabled else "SUSPENDU (mode maison)",
                     round(demand.next_comfort_minutes) if demand.next_comfort_minutes is not None else "-",
                     round(demand.next_setback_minutes) if demand.next_setback_minutes is not None else "-",
                     ",".join(demand.preheat_rooms) or "-", ",".join(demand.setback_rooms) or "-")
        if not control:
            self.stabilizer.reset_pending(reset_filter=True)
            self.stabilizer.status = "simulation (sans écriture)"
            self.stabilizer.publish()
            if self.touched:
                self.restore()
            return
        now = time.monotonic()
        self.stabilizer.observe(time.time(), elapsed, demand.gaps, enabled=True)
        target = self.stabilizer.plan(now, proposed, current, val(night["state"]),
                                      demand.actual_max_gap, opts)
        self.stabilizer.publish()
        if target is None:
            return
        call_number(ZONE_DAY, target)
        self.touched = True
        try:
            call_number(ZONE_NIGHT, target)
        except Exception:
            self.stabilizer.reset_pending(reset_filter=True)
            self.restore()
            raise
        self.last_command = target
        self.last_write = now
        self.stabilizer.committed(now, current, target)
        self.stabilizer.publish(force=True)
        LOG.warning("Consigne Diematic jour/nuit -> %.1f °C (filtrage 5 min / confirmation 10 min)", target)


def monitor_thermal_loop(observer):
    """Read-only Shelly telemetry in a daemon thread: never block the regulator."""
    LOG.info("Surveillance hydraulique C active : mesure seulement, sans commandes")
    while not STOP:
        observer.cycle()
        for _ in range(60):
            if STOP:
                break
            time.sleep(1)


def monitor_solar_loop(observer):
    while not STOP:
        observer.cycle()
        for _ in range(60):
            if STOP:
                break
            time.sleep(1)


def main():
    opts = read_options()
    LOG.info("Régulation C %s; démarrage en %s",
             "V0.4.11", "SIMULATION" if opts.get("dry_run", True) or not opts.get("automatic_control") else "ACTIF")
    observer = ThermalMonitor(state, mqtt_publish)
    controller = Controller(opts, observer)
    fioul = FuelMonitor(api, state, mqtt_publish)
    threading.Thread(target=monitor_thermal_loop, args=(observer,), name="circuit-c-thermal", daemon=True).start()
    solar = SolarMonitor(state, mqtt_publish)
    threading.Thread(target=monitor_solar_loop, args=(solar,), name="circuit-c-solar", daemon=True).start()
    solar_forecast = SolarForecast(state, mqtt_publish, ROOMS, EVE_ROOM, EVE_SENSOR)
    threading.Thread(target=monitor_solar_loop, args=(solar_forecast,), name="circuit-c-solar-forecast", daemon=True).start()
    try:
        while not STOP:
            fioul.cycle()
            try:
                controller.cycle()
            except Exception as exc:
                LOG.exception("Cycle défaillant: %s", exc)
                try:
                    controller.restore()
                except Exception:
                    LOG.exception("Restauration impossible; vérifier immédiatement les consignes Diematic")
            for _ in range(opts["poll_seconds"]):
                if STOP:
                    break
                time.sleep(1)
    finally:
        try:
            controller.stats.flush()
        except Exception as exc:
            LOG.warning('Sauvegarde statistiques impossible: %s', exc)
        if controller.touched:
            # Un SIGTERM est attendu lors d'une mise à jour / d'un redémarrage.
            # Laisser la Diematic avec sa dernière consigne sûre, plutôt que
            # d'imposer 21/19 pendant l'initialisation de la version suivante.
            # En cas d'arrêt non sollicité, conserver le repli de sécurité.
            orderly_active_stop = (
                STOP and opts.get("automatic_control", False)
                and not opts.get("dry_run", True)
            )
            if orderly_active_stop:
                LOG.warning("Arrêt contrôlé: dernières consignes Diematic conservées; "
                            "pas de retour provisoire à 21/19 °C")
            else:
                try:
                    controller.restore()
                except Exception:
                    LOG.exception("Arrêt: restauration impossible")


if __name__ == "__main__":
    main()
