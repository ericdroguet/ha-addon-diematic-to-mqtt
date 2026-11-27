"""ECS V0.1: read-only observation. There is deliberately no HA write API."""
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from policy import Cycle, Settings, evaluate, restore

LOG = logging.getLogger("ecs")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
BASE = "http://supervisor/core/api"
DATA = Path("/data")
ENTITIES = {
    "temp": "sensor.hot_water_temp",
    "pump": "binary_sensor.hot_water_pump",
    "color": "sensor.calendrier_tempo_couleur_actuelle",
    "period": "sensor.calendrier_tempo_periode",
    "price": "sensor.calendrier_tempo_prix_kwh_actuel",
    "resistance": "switch.chauffe_eau",
    "automatic": "input_boolean.ecs_automatique",
    "season": "input_select.saison_ecs",
    "boost": "input_boolean.boost_ecs",
    "fault": "input_boolean.ecs_chauffe_inefficace",
    "day": "number.hot_water_temp_day",
    "night": "number.hot_water_temp_night",
}
def get_states():
    req = Request(BASE + "/states", headers={
        "Authorization": "Bearer " + os.environ["SUPERVISOR_TOKEN"]}, method="GET")
    with urlopen(req, timeout=10) as resp:
        return {row["entity_id"]: row for row in json.load(resp)}

def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def persist(cycle):
    tmp = DATA / "cycle.tmp"
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(asdict(cycle), f)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(DATA / "cycle.json")

def log_event(event):
    # Keep bounded local diagnostics, not simulated energy billed as actual.
    path = DATA / "events.jsonl"
    if path.exists() and path.stat().st_size > 2_000_000:
        path.replace(DATA / "events.previous.jsonl")
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

def main():
    opts = json.loads((DATA / "options.json").read_text())
    cfg = Settings(**{k: int(opts[k]) for k in asdict(Settings())})
    interval = max(10, min(60, int(opts["poll_seconds"])))
    try:
        cycle = restore(json.loads((DATA / "cycle.json").read_text()), time.time(), cfg)
    except (OSError, ValueError):
        cycle = Cycle()
    LOG.info("OBSERVATION STRICTE : aucune commande chaudière ou résistance")
    last_key = None
    last_log = 0
    while True:
        now = time.time()
        try:
            records = get_states()
            vals = {k: records.get(e, {}).get("state") for k, e in ENTITIES.items()}
            permitted = (vals["automatic"] == "on" and vals["season"] == "Hiver - Mixte"
                         and vals["boost"] == "off" and vals["fault"] == "off"
                         and vals["resistance"] in ("on", "off"))
            # Require a recent reported boiler temperature, not merely a cached numeric state.
            record = records.get(ENTITIES["temp"], {})
            try:
                stamp = datetime.fromisoformat(record.get("last_reported") or record["last_updated"]).timestamp()
                temp = number(vals["temp"]) if 0 <= now - stamp <= 180 else None
            except (KeyError, TypeError, ValueError):
                temp = None
            proposal = evaluate(cycle, cfg, now=now, temp=temp, color=vals["color"],
                                period=vals["period"], pump=vals["pump"], allowed=permitted)
            event = {"utc": datetime.now(timezone.utc).isoformat(), "observed": vals,
                     "proposed": asdict(proposal), "mode": "observation",
                     "max_theoretical_kwh": 2.4 * cfg.electric_max_minutes / 60}
        except Exception as exc:
            cycle = Cycle(reason="lecture HA impossible")
            event = {"utc": datetime.now(timezone.utc).isoformat(), "mode": "observation",
                     "error": str(exc), "proposed": {"electric": False, "phase": "locked"}}
        persist(cycle)
        p = event["proposed"]
        key = (p.get("phase"), p.get("fuel_target"), p.get("electric"), p.get("reason"), event.get("error"))
        if key != last_key or now - last_log >= 300:
            log_event(event)
            LOG.info("%s", json.dumps(event, ensure_ascii=False))
            last_key, last_log = key, now
        time.sleep(interval)

if __name__ == "__main__":
    main()
