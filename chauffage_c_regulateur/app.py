"""Régulateur indépendant du circuit C, HA OS add-on. Safe by default."""
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from urllib import request, error

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("chauffage_c")
BASE = "http://supervisor/core/api"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
ROOMS = (
    "climate.sejour",
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
    return opts


@dataclass
class Demand:
    valid: int = 0
    count: int = 0
    max_gap: float = 0
    priority: str = "Aucune"


def add_room(result, name, current, target, heating=False):
    if current is None or target is None:
        return
    result.valid += 1
    gap = target - current
    if gap > 0.15 or (heating and gap > -0.2):
        result.count += 1
        score = max(0.2, gap)
        if score > result.max_gap:
            result.max_gap = score
            result.priority = name


def collect():
    result = Demand()
    for room in ROOMS:
        record = state(room)
        if not record or record.get("state") not in ("auto", "heat"):
            continue
        attrs = record.get("attributes", {})
        add_room(
            result, attrs.get("friendly_name", room),
            val(attrs.get("current_temperature")), val(attrs.get("temperature")),
            attrs.get("hvac_action") == "heating",
        )
    eve = state(EVE_ROOM)
    thermometer = state(EVE_SENSOR)
    if eve and thermometer and eve.get("state") == "heat":
        valves = [state(e) for e in EVE_VALVES]
        opening = any(v and val(v.get("state")) is not None
                      and val(v["state"]) > 5 for v in valves)
        add_room(
            result, "Chambre Eve", val(thermometer.get("state")),
            val(eve.get("attributes", {}).get("temperature")), opening,
        )
    return result


def round_half(value):
    return round(value * 2) / 2


def decide(demand, previous, opts):
    # V1: empirical heuristic only; Diematic retains weather compensation.
    raw = 18.5 + 2.25 * min(demand.max_gap, 2.5) + 0.3 * min(demand.count, 4)
    raw = min(opts["maximum_virtual"], max(opts["minimum_virtual"], raw))
    if previous is None:
        previous = min(opts["maximum_virtual"], max(opts["minimum_virtual"], opts["manual_day"]))
    limit_up = max(0.5, float(opts["max_rise_per_write"]))
    limit_down = max(0.5, float(opts["max_drop_per_write"]))
    stepped = min(previous + limit_up, max(previous - limit_down, raw))
    # A manual override can start outside the configured bounds.
    # Never propose a value outside the hard limits, even during ramp-down.
    return round_half(min(opts["maximum_virtual"], max(opts["minimum_virtual"], stepped)))


class Controller:
    def __init__(self, opts):
        self.opts = opts
        self.last_write = 0
        self.last_command = None
        self.touched = False
        self.was_allowed = False

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
        demand = collect()
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
        proposed = decide(demand, previous, opts)
        LOG.info("zones_valides=%d demandes=%d ecart_max=%.2f priorite=%s départ=%.1f extérieur=%.1f"
                 " consigne_actuelle=%.1f consigne_proposée=%.1f pilotage=%s",
                 demand.valid, demand.count, demand.max_gap, demand.priority,
                 val(departure["state"]), val(outdoor["state"]), current, proposed,
                 "ACTIF" if control else "SIMULATION")
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
             "V0.1.0", "SIMULATION" if opts.get("dry_run", True) or not opts.get("automatic_control") else "ACTIF")
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
