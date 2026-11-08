"""Régulateur indépendant du circuit C, HA OS add-on. Safe by default."""
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib import request, error

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
            and house_state and house_state.get("state") == "at_home"
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
    if change is None or outdoor is None or demand.valid < 8:
        return 0.0
    correction = max(-max_boost, min(max_boost, 0.12 * (10 - outdoor) - 0.30 * change))
    if demand.actual_max_gap > 0.5:
        correction = max(0.0, correction)
    return correction


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
    raw = (20.5 + 1.5 * min(representative, 2.0)
           + 0.25 * min(demand.count, 8)
           + 0.8 * persistence + anticipation)
    raw = min(opts["maximum_virtual"], max(opts["minimum_virtual"], raw))
    if previous is None:
        previous = min(opts["maximum_virtual"], max(opts["minimum_virtual"], opts["manual_day"]))
    limit_up = max(0.5, float(opts["max_rise_per_write"]))
    limit_down = max(0.5, float(opts["max_drop_per_write"]))
    stepped = min(previous + limit_up, max(previous - limit_down, raw))
    return round_half(min(opts["maximum_virtual"], max(opts["minimum_virtual"], stepped)))


class Controller:
    def __init__(self, opts):
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
            LOG.warning("SÉCURITÉ: mesures=%s, mode=%s, départ=%s; restauration si nécessaire",
                        demand.valid, mode.get("state") if mode else None,
                        departure.get("state") if departure else None)
            if control and self.touched:
                self.restore()
            return
        current = val(day["state"])
        previous = self.last_command if self.last_command is not None else current
        anticipation = 0.0
        if opts["weather_anticipation"]:
            now_forecast = time.monotonic()
            if not self.last_forecast_check or now_forecast - self.last_forecast_check >= 900:
                self.cached_change = forecast_change(val(outdoor["state"]))
                self.last_forecast_check = now_forecast
            anticipation = weather_adjustment(self.cached_change, val(outdoor["state"]), demand, opts["weather_max_boost"])
        baseline = decide(demand, previous, opts, 0.0, persistence)
        proposed = decide(demand, previous, opts, anticipation, persistence)
        if opts["weather_anticipation"]:
            LOG.info("Météo: variation prévue pondérée 1-6h=%s °C; zones_proches=%d; correction=%+.1f °C; sans_météo=%.1f °C; avec_météo=%.1f °C",
                     self.cached_change, demand.near_count, anticipation, baseline, proposed)
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
            if self.touched:
                self.restore()
            return
        now = time.monotonic()
        if self.last_write and now - self.last_write < opts["write_interval_seconds"]:
            return
        if current is None:
            return
        if abs(proposed - current) < 0.49 and abs(proposed - val(night["state"])) < 0.49:
            self.last_command = proposed
            return
        call_number(ZONE_DAY, proposed)
        self.touched = True
        try:
            call_number(ZONE_NIGHT, proposed)
        except Exception:
            self.restore()
            raise
        self.last_command = proposed
        self.last_write = now
        LOG.warning("Consigne Diematic jour/nuit -> %.1f °C (via HA uniquement)", proposed)


def main():
    opts = read_options()
    LOG.info("Régulation C %s; démarrage en %s",
             "V0.3.0", "SIMULATION" if opts.get("dry_run", True) or not opts.get("automatic_control") else "ACTIF")
    controller = Controller(opts)
    try:
        while not STOP:
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
        if controller.touched:
            try:
                controller.restore()
            except Exception:
                LOG.exception("Arrêt: restauration impossible")


if __name__ == "__main__":
    main()
