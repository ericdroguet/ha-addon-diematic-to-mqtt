"""ECS 0.1.4: bounded Netatmo commands and fail-closed cloud handling."""
import json, logging, os, time, signal
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from policy import Cycle, Settings, Proposal, evaluate, unused_recharge, fresh_temperature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("ecs")
BASE = "http://supervisor/core/api"
DATA = Path("/data")
ENABLE = "input_boolean.ecs_hybride_actif"
TIMER = "timer.ecs_hybride_limite"
GUARD = "automation.ecs_hybride_parachute_electrique"
CUT_SCRIPT = "script.ecs_netatmo_coupure"
CUT_PAUSE = "timer.ecs_netatmo_pause_coupure"
START_PAUSE = 3600
last_uncertain = False
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
        state = get(entity)
        if state in ("unknown", "unavailable"):
            continue
        if float(state) != target:
            call("number/set_value", entity, value=target)

def cut():
    """Do not send cloud commands for unknown/off states. OFF goes through HA's shared limiter."""
    global last_uncertain
    state = get(SW)
    if state == "off":
        last_uncertain = False
        if get(TIMER) != "idle":
            call("timer/cancel", TIMER)
        return True
    if state == "on" and get(CUT_PAUSE) == "idle" and get(CUT_SCRIPT) == "off":
        call("script/turn_on", CUT_SCRIPT)
    if not last_uncertain:
        last_uncertain = True
        LOG.warning("Coupure non confirmée (%s) : aucun ordre direct Netatmo", state)
        try:
            api("/services/persistent_notification/create",
                {"notification_id": "ecs_netatmo_incertain", "title": "ECS : état électrique non confirmé",
                 "message": "Vérifier le contacteur dans Home + Control ou localement. Ne pas considérer la coupure garantie."})
        except Exception:
            LOG.warning("Notification locale indisponible")
    return False

def set_start_pause(until):
    path = DATA / "netatmo_pause.tmp"
    with path.open("w") as f:
        json.dump({"until": until}, f)
        f.flush()
        os.fsync(f.fileno())
    path.replace(DATA / "netatmo_pause.json")

def get_start_pause():
    try:
        return float(json.loads((DATA / "netatmo_pause.json").read_text())["until"])
    except FileNotFoundError:
        return 0
    except (OSError, ValueError, TypeError, KeyError):
        return time.time() + START_PAUSE

def confirm_start(timeout=45):
    """Wait for cached HA feedback, never poll or retry the cloud."""
    deadline = time.monotonic() + timeout
    while not STOP and time.monotonic() < deadline:
        if (get(TIMER) != "active" or get(ENABLE) != "on" or get(GUARD) != "on"
                or get(PUMP) != "off" or get(BOOST) != "off" or get(FAULT) != "off"
                or get(CUT_SCRIPT) != "off" or get(CUT_PAUSE) != "idle"
                or not 48 <= float(get(TEMP)) < 55
                or get(COLOR) not in ("bleu", "blanc", "rouge")
                or get(PERIOD) not in ("hp", "hc")
                or (get(COLOR) == "rouge" and get(PERIOD) == "hp")):
            return False
        if get(SW) == "on":
            return True
        time.sleep(3)
    return False

def lock(cycle, reason):
    cycle.phase, cycle.reason = "locked", reason
    cycle.started = cycle.deadline = None

def save(cycle, owned):
    p = DATA / "active_cycle.tmp"
    with p.open("w") as f:
        json.dump({"schema": 2, "cycle": asdict(cycle), "owned": owned}, f)
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
    pending_recovery = False
    legacy_recovery = False
    next_start = get_start_pause()
    try:
        saved = json.loads((DATA / "active_cycle.json").read_text())
        owned = bool(saved.get("owned"))
        recovery = owned
        prior = saved.get("cycle", {})
        # The preceding release did not persist a cloud cooldown.
        if not (DATA / "netatmo_pause.json").exists() and prior.get("reason") == "erreur : complément interrompu":
            next_start = time.time() + START_PAUSE
            set_start_pause(next_start)
        # No restart of a spent/attempted electrical complement.
        pending_recovery = prior.get("phase") == "fuel"
        legacy_recovery = (saved.get("schema") != 2
                           and prior.get("phase") == "locked"
                           and prior.get("reason") in ("erreur : complément interrompu", "désactivé ou garde-fou",
                                                       "redémarrage : ancien complément non repris"))
        recovery = owned or pending_recovery or legacy_recovery
    except (OSError, ValueError, TypeError):
        pass
    last = None
    last_log = 0
    LOG.info("ECS 0.1.4 : coupure HA temporisée 120 s, pause ON 60 min sur erreur, confirmation locale")
    try:
        while not STOP:
            now = time.time()
            try:
                rec = {r["entity_id"]: r for r in api("/states")}
                def val(e): return rec.get(e, {}).get("state")
                temp = fresh_temperature(rec, now)
                enabled = val(ENABLE) == "on"
                valid = (enabled and val(GUARD) == "on" and all(val(e) == "off" for e in OLD)
                         and val(AUTO) == "on" and val(SEASON) == "Hiver - Mixte"
                         and val(BOOST) == "off" and val(FAULT) == "off"
                         and official(rec, now) and temp is not None and 0 <= temp <= 95
                         and val(PUMP) in ("on", "off") and val(SW) in ("on", "off")
                         and val(TIMER) in ("idle", "active") and val(COLOR) in ("bleu", "blanc", "rouge")
                         and val(CUT_SCRIPT) in ("on", "off") and val(CUT_PAUSE) in ("active", "idle")
                         and now >= next_start)
                inactive = val(SW) == "off" and val(TIMER) == "idle"
                if recovery and valid:
                    proven = pending_recovery and inactive
                    if legacy_recovery and inactive and 48 <= temp < 55:
                        # A one-time migration of legacy locked state only, not a periodic history query.
                        try:
                            start = datetime.fromtimestamp(now-10800, ZoneInfo("UTC")).isoformat()
                            rows = api("/history/period/" + start + "?" + urlencode({"filter_entity_id": TEMP}))
                            proven = proven or (isinstance(rows, list) and len(rows) == 1
                                                and unused_recharge(rec, rows[0], now=now, threshold=settings.rearm_below))
                        except Exception as exc:
                            LOG.warning("Migration recharge non prouvée : %s", exc)
                    if proven and temp < 55:
                        cycle = Cycle(phase="fuel", reason="reprise avant complément")
                        LOG.info("Reprise recharge fioul non consommée : %.1f °C", temp)
                    else:
                        cut()
                        fuel(55)
                        lock(cycle, "redémarrage : ancien complément non repris")
                    recovery = False
                    pending_recovery = legacy_recovery = False
                    save(cycle, owned)
                if not valid:
                    # If an electrical start was never attempted, retain eligibility.
                    keep = (cycle.phase == "fuel" and inactive)
                    if owned or (enabled and val(SEASON) == "Hiver - Mixte"):
                        cut()
                        fuel(55)
                    if keep:
                        cycle.reason = "recharge suspendue : données indisponibles"
                    else:
                        lock(cycle, "désactivé ou garde-fou")
                    owned = False
                else:
                    owned = True
                    previous = cycle.phase == "electric"
                    # Do not start the complement until both real fuel setpoints have caught up.
                    if (cycle.phase == "fuel" and temp < 55
                            and (val(COLOR) != "rouge" or val(PERIOD) == "hc")
                            and (val(DAY) not in ("50", "50.0") or val(NIGHT) not in ("50", "50.0")
                                 or val(CUT_PAUSE) != "idle" or val(CUT_SCRIPT) != "off"
                                 or val(SW) != "off" or val(TIMER) != "idle")):
                        proposal = Proposal("fuel", 50, False, "attente confirmation consignes fioul 50")
                    else:
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
                                and get(TIMER) == "idle" and get(CUT_PAUSE) == "idle"
                                and get(CUT_SCRIPT) == "off" and get(FAULT) == "off"
                                and get(BOOST) == "off" and 50 <= float(get(TEMP)) < 55):
                            seconds = min(7140, int(cycle.deadline-time.time())-60)
                            if seconds <= 0:
                                raise RuntimeError("délai insuffisant")
                            call("timer/start", TIMER, duration=seconds)
                            if get(TIMER) != "active" or get(ENABLE) != "on" or get(GUARD) != "on":
                                raise RuntimeError("parachute non confirmé")
                            # Persist BEFORE the one permitted ON; errors/timeouts are ambiguous.
                            next_start = time.time() + START_PAUSE
                            set_start_pause(next_start)
                            call("switch/turn_on", SW)
                            if not confirm_start():
                                raise RuntimeError("contacteur non confirmé après attente locale")
                            set_start_pause(0)
                            next_start = 0
                            LOG.info("Complément électrique démarré, limite %s secondes", seconds)
                        else:
                            cut()
                            fuel(55)
                            lock(cycle, "interverrouillage non confirmé")
                    else:
                        cut()
                        fallback = cycle.phase == "locked" and cycle.reason != "cible électrique atteinte"
                        fuel(55 if fallback else proposal.fuel_target)
                if not recovery:
                    save(cycle, owned)
                event = {"phase": cycle.phase, "reason": cycle.reason, "active": valid,
                         "temp": temp, "color": val(COLOR), "period": val(PERIOD),
                         "pump": val(PUMP), "resistance": val(SW), "day": val(DAY), "night": val(NIGHT),
                         "power": val("sensor.chauffe_eau_puissance"),
                         "remaining": max(0, int(cycle.deadline-now)) if cycle.deadline else 0,
                         "netatmo_pause_seconds": max(0, int(next_start-now))}
                key = (event["phase"], event["reason"], event["active"], event["day"], event["resistance"])
                if key != last or now-last_log >= 300:
                    LOG.info("%s", json.dumps(event, ensure_ascii=False))
                    last, last_log = key, now
            except Exception as exc:
                LOG.error("Erreur contrôle : %s", exc)
                if cycle.phase == "fuel":
                    cycle.reason = "recharge suspendue : erreur avant complément"
                else:
                    lock(cycle, "erreur : complément interrompu")
                if owned:
                    try:
                        cut()
                    except Exception as e:
                        LOG.error("Coupure non confirmée : parachute requis : %s", e)
                    try:
                        fuel(55)
                    except Exception as e:
                        LOG.error("Secours fioul non confirmé : %s", e)
                if not recovery:
                    save(cycle, owned)
            time.sleep(interval)
    finally:
        if owned:
            try:
                cut()
            except Exception:
                LOG.exception("Arrêt : coupure non confirmée")
            try:
                fuel(55)
            except Exception:
                LOG.exception("Arrêt : secours fioul non confirmé")
            if not recovery:
                save(cycle, owned)

if __name__ == "__main__":
    main()
