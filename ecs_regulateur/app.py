"""ECS 0.1.1: gated active control, independent HA cutout."""
import json, logging, os, time, signal
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from policy import Cycle, Settings, evaluate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("ecs")
BASE = "http://supervisor/core/api"
DATA = Path("/data")
ENABLE = "input_boolean.ecs_hybride_actif"
TIMER = "timer.ecs_hybride_limite"
GUARD = "automation.ecs_hybride_parachute_electrique"
SW = "switch.chauffe_eau"
DAY = "number.hot_water_temp_day"
NIGHT = "number.hot_water_temp_night"
TEMP = "sensor.hot_water_temp"
PUMP = "binary_sensor.hot_water_pump"
COLOR = "sensor.calendrier_tempo_couleur_actuelle"
PERIOD = "sensor.calendrier_tempo_periode"
AUTO = "input_boolean.ecs_automatique"
SEASON = "input_select.saison_ecs"
BOOST = "input_boolean.boost_ecs"
FAULT = "input_boolean.ecs_chauffe_inefficace"
OLD = ("automation.ecs_consigne_fioul_tempo",
       "automation.ecs_resistance_selon_tempo_et_temperature",
       "automation.ecs_boost_manuel_resistance_3_heures")
STOP = False

def api(path, payload=None):
    req = Request(BASE + path, data=json.dumps(payload).encode() if payload is not None else None,
                  headers={"Authorization": "Bearer " + os.environ["SUPERVISOR_TOKEN"],
                           "Content-Type": "application/json"})
    with urlopen(req, timeout=8) as resp:
        return json.load(resp)

def get(entity):
    return api("/states/" + entity)["state"]

def call(service, entity, **data):
    return api("/services/" + service, {"entity_id": entity, **data})

def fuel(target):
    for entity in (DAY, NIGHT):
        if float(get(entity)) != target:
            call("number/set_value", entity, value=target)

def cut():
    if get(SW) != "off":
        call("switch/turn_off", SW)
    if get(SW) != "off":
        raise RuntimeError("coupure résistance non confirmée")
    if get(TIMER) != "idle":
        call("timer/cancel", TIMER)

def lock(cycle, reason):
    cycle.phase, cycle.reason = "locked", reason
    cycle.started = cycle.deadline = None

def save(cycle, owned):
    p = DATA / "active_cycle.tmp"
    with p.open("w") as f:
        json.dump({"cycle": asdict(cycle), "owned": owned}, f)
        f.flush()
        os.fsync(f.fileno())
    p.replace(DATA / "active_cycle.json")

def stop(sig, frame):
    global STOP
    STOP = True

def official(records, now):
    local = datetime.fromtimestamp(now, ZoneInfo("Europe/Paris"))
    date = (local.date() - timedelta(days=1) if local.hour < 6 else local.date()).isoformat()
    attrs = records.get(COLOR, {}).get("attributes", {})
    return (attrs.get("officielle") is True and attrs.get("date") == date
            and records.get(PERIOD, {}).get("state") == ("hc" if local.hour >= 22 or local.hour < 6 else "hp"))

def main():
    global STOP
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    opts = json.loads((DATA / "options.json").read_text())
    settings = Settings(**{k: int(opts[k]) for k in asdict(Settings())})
    interval = max(10, min(30, int(opts["poll_seconds"])))
    cycle = Cycle()
    owned = False
    recovery = False
    try:
        saved = json.loads((DATA / "active_cycle.json").read_text())
        owned = bool(saved.get("owned"))
        recovery = owned
        # A restart cannot resume an electrical cycle. Restore fuel and require fresh recharge.
    except (OSError, ValueError, TypeError):
        pass
    last = None
    last_log = 0
    LOG.info("ECS 0.1.1 : autorisation HA et parachute obligatoires")
    try:
        while not STOP:
            now = time.time()
            try:
                rec = {r["entity_id"]: r for r in api("/states")}
                def val(e): return rec.get(e, {}).get("state")
                try:
                    r = rec[TEMP]
                    stamp = datetime.fromisoformat(r.get("last_reported") or r["last_updated"]).timestamp()
                    temp = float(r["state"]) if 0 <= now-stamp <= 180 else None
                except (ValueError, TypeError, KeyError):
                    temp = None
                enabled = val(ENABLE) == "on"
                valid = (enabled and val(GUARD) == "on" and all(val(e) == "off" for e in OLD)
                         and val(AUTO) == "on" and val(SEASON) == "Hiver - Mixte"
                         and val(BOOST) == "off" and val(FAULT) == "off"
                         and official(rec, now) and temp is not None and 0 <= temp <= 95
                         and val(PUMP) in ("on", "off") and val(SW) in ("on", "off")
                         and val(TIMER) in ("idle", "active") and val(COLOR) in ("bleu", "blanc", "rouge"))
                if recovery:
                    cut()
                    fuel(55)
                    lock(cycle, "redémarrage : ancien complément non repris")
                    recovery = False
                if not valid:
                    if owned or (enabled and val(SEASON) == "Hiver - Mixte"):
                        cut()
                        fuel(55)
                    lock(cycle, "désactivé ou garde-fou")
                    owned = False
                else:
                    owned = True
                    previous = cycle.phase == "electric"
                    proposal = evaluate(cycle, settings, now=now, temp=temp, color=val(COLOR),
                                        period=val(PERIOD), pump=val(PUMP), allowed=True)
                    save(cycle, owned)
                    if proposal.electric and previous:
                        # An external cut, expired timer or contradictory fuel target locks the cycle.
                        if (val(SW) != "on" or val(TIMER) != "active"
                            or float(val(DAY)) != 50 or float(val(NIGHT)) != 50):
                            cut()
                            fuel(55)
                            lock(cycle, "coupure externe : aucun redémarrage")
                    elif proposal.electric:
                        fuel(50)
                        if (float(get(DAY)) == float(get(NIGHT)) == 50 and get(PUMP) == "off"
                                and get(ENABLE) == get(GUARD) == "on" and get(SW) == "off"
                                and get(TIMER) == "idle" and get(FAULT) == "off"
                                and get(BOOST) == "off" and 50 <= float(get(TEMP)) < 55):
                            seconds = min(7140, int(cycle.deadline-time.time())-60)
                            if seconds <= 0:
                                raise RuntimeError("délai insuffisant")
                            call("timer/start", TIMER, duration=seconds)
                            if get(TIMER) != "active" or get(ENABLE) != "on" or get(GUARD) != "on":
                                raise RuntimeError("parachute non confirmé")
                            call("switch/turn_on", SW)
                            if get(SW) != "on":
                                raise RuntimeError("contacteur non confirmé")
                            LOG.info("Complément électrique démarré, limite %s secondes", seconds)
                        else:
                            cut()
                            fuel(55)
                            lock(cycle, "interverrouillage non confirmé")
                    else:
                        cut()
                        fallback = cycle.phase == "locked" and cycle.reason != "cible électrique atteinte"
                        fuel(55 if fallback else proposal.fuel_target)
                save(cycle, owned)
                event = {"phase": cycle.phase, "reason": cycle.reason, "active": valid,
                         "temp": temp, "color": val(COLOR), "period": val(PERIOD),
                         "pump": val(PUMP), "resistance": val(SW), "day": val(DAY), "night": val(NIGHT),
                         "power": val("sensor.chauffe_eau_puissance"),
                         "remaining": max(0, int(cycle.deadline-now)) if cycle.deadline else 0}
                key = (event["phase"], event["reason"], event["active"], event["day"], event["resistance"])
                if key != last or now-last_log >= 300:
                    LOG.info("%s", json.dumps(event, ensure_ascii=False))
                    last, last_log = key, now
            except Exception as exc:
                LOG.error("Erreur contrôle : %s", exc)
                lock(cycle, "erreur : complément interrompu")
                if owned:
                    try:
                        cut()
                        fuel(55)
                    except Exception as e:
                        LOG.error("Coupure non confirmée : parachute requis : %s", e)
                save(cycle, owned)
            time.sleep(interval)
    finally:
        if owned:
            try:
                cut()
                fuel(55)
            except Exception:
                LOG.exception("Arrêt : coupure non confirmée")
            save(cycle, owned)

if __name__ == "__main__":
    main()
