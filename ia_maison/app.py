"""Read-only local observation. No HA service calls, no external API."""
import datetime as dt
import gzip
import json
import logging
import math
import os
from pathlib import Path
import shutil
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("ia_maison")
DATA = Path("/data/observation")
DOMAINS = {"sensor", "binary_sensor", "climate", "switch", "number", "select",
           "input_boolean", "input_number", "cover", "weather", "fan", "light",
           "humidifier", "water_heater", "device_tracker", "person", "sun"}
PRIVATE = {"device_tracker", "person"}
ATTRS = {"current_temperature", "temperature", "target_temp_high", "target_temp_low",
         "current_humidity", "humidity", "hvac_action", "hvac_mode", "preset_mode",
         "current_position", "percentage", "wind_speed", "pressure", "cloud_coverage",
         "temperature_unit", "unit_of_measurement", "device_class", "state_class",
         "dew_point", "apparent_temperature", "elevation", "azimuth"}
ENUMS = {"on", "off", "open", "closed", "opening", "closing", "heat", "cool",
         "heat_cool", "auto", "idle", "heating", "cooling", "dry", "fan_only",
         "unavailable", "unknown", "home", "not_home", "above_horizon",
         "below_horizon", "bleu", "blanc", "rouge", "hp", "hc", "active",
         "paused", "standby", "normal", "comfort", "eco", "away", "sleep",
         "manual", "temp", "sunny", "clear-night", "cloudy", "partlycloudy",
         "rainy", "pouring", "snowy", "fog", "windy", "exceptional"}
EXCLUDE_ROOMS = {"climate.zone_c_climate", "climate.hot_water_climate", "climate.pac_piscine",
                 "climate.chambre_valeric", "climate.chambre", "climate.cuisine",
                 "climate.eve_thermo_20ebp1701", "climate.eve_thermo_20ebp1701_2",
                 "climate.droguet"}
HORIZON = 1800
BOOT = uuid.uuid4().hex[:10]
STATUS = {"phase": "initialisation", "control": False, "external_api": False}
LOCK = threading.Lock()

def number(x):
    if isinstance(x, bool) or x is None:
        return None
    try:
        n = float(x)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None

def timestamp(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError, TypeError, OverflowError):
        return None

def scalar(v):
    n = number(v)
    if n is not None:
        return n
    if isinstance(v, str) and v.lower() in ENUMS:
        return v.lower()
    return None

def clean(raw, now):
    out = {}
    for r in raw:
        eid = r.get("entity_id", "")
        domain = eid.split(".", 1)[0]
        if domain not in DOMAINS or not eid or len(eid) > 250:
            continue
        a = r.get("attributes") or {}
        state = scalar(r.get("state"))
        if domain in PRIVATE:
            state = "home" if state == "home" else "not_home" if state == "not_home" else None
        attrs = {}
        if domain not in PRIVATE:
            for k in ATTRS:
                if k not in a:
                    continue
                v = a[k]
                if k in {"unit_of_measurement", "temperature_unit", "device_class", "state_class"}:
                    if isinstance(v, str) and len(v) <= 40:
                        attrs[k] = v
                else:
                    val = scalar(v)
                    if val is not None:
                        attrs[k] = val
        report = timestamp(r.get("last_reported") or r.get("last_updated"))
        age = round(now - report, 1) if report is not None else None
        quality = "ok"
        if r.get("state") in {"unknown", "unavailable"}:
            quality = r["state"]
        elif age is None or age < -60:
            quality = "timestamp"
        elif domain == "climate" and age > 3600:
            quality = "old_report"
        elif state is None and not attrs:
            quality = "unsupported_state"
        out[eid] = {"s": state, "a": attrs, "age": age, "q": quality}
    return out

def read_states():
    # Only a GET of cached HA states: no service endpoint, no direct Netatmo polling.
    req = urllib.request.Request("http://supervisor/core/api/states",
          headers={"Authorization": "Bearer " + os.environ["SUPERVISOR_TOKEN"]},
          method="GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = json.load(resp)
    if not isinstance(raw, list) or not raw:
        raise ValueError("empty or invalid HA snapshot")
    return raw

def atomic_json(path, data):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, allow_nan=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def prune(root, days, max_bytes, now):
    files = sorted(root.glob("obs-*.jsonl.gz"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for p in files:
        st = p.stat()
        if st.st_mtime < now - days * 86400 or total > max_bytes:
            total -= st.st_size
            p.unlink()
    return total

def archive(root, row, now):
    if shutil.disk_usage(root).free < 5 * 1024**3:
        raise OSError("reserve disque de 5 Gio atteinte")
    hour = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y%m%d-%H")
    path = root / ("obs-" + hour + "-" + BOOT + ".jsonl.gz")
    # Each sample is a standalone gzip member with CRC; never append to a prior boot.
    payload = json.dumps(row, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    blob = gzip.compress(payload, compresslevel=3)
    with path.open("ab") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    return len(blob)

def value(rows, eid, lo, hi, max_age=None):
    r = rows.get(eid, {})
    if r.get("q") != "ok":
        return None
    if max_age is not None and (r.get("age") is None or not -60 <= r["age"] <= max_age):
        return None
    n = number(r.get("s"))
    return n if n is not None and lo <= n <= hi else None

def features(rows):
    ext = value(rows, "sensor.ext_temp", -40, 55, 1800)
    dep = value(rows, "sensor.zone_c_departure_temp", 0, 100, 1800)
    if ext is None or dep is None:
        return {}
    ecs = rows.get("binary_sensor.hot_water_pump", {})
    pump = rows.get("binary_sensor.zone_c_pump", {})
    if ecs.get("q") != "ok" or pump.get("q") != "ok" or ecs.get("s") not in ("on","off") or pump.get("s") not in ("on","off"):
        return {}
    out = {}
    for eid, r in rows.items():
        if not eid.startswith("climate.") or eid in EXCLUDE_ROOMS or r["q"] != "ok":
            continue
        a = r["a"]
        t, target = number(a.get("current_temperature")), number(a.get("temperature"))
        if t is None or target is None or not 5 <= t <= 35 or not 5 <= target <= 35:
            continue
        action = a.get("hvac_action")
        if action not in ("heating", "idle", "off"):
            continue
        heat = action == "heating"
        x = [1.0, (ext-t)/30, (dep-t)/60 if heat and pump["s"]=="on" else 0,
             float(heat), float(ecs["s"]=="on"), max(-1,min(1,(target-t)/5))]
        out[eid] = (t, target, x)
    return out

class Learner:
    """Online linear model of 30 minute temperature change, prequential scoring.
    Constant operating conditions only; not a causal model or MPC yet."""
    def __init__(self, saved=None):
        self.rooms = saved if isinstance(saved, dict) else {}
        self.pending = {}
        self.last = None
        self.settings = None

    def reset_pending(self):
        self.pending.clear()

    def observe(self, now, rows):
        setting_ids = ("sensor.zone_c_heating_slope", "sensor.trial_valve_band",
                       "number.zone_c_temp_day", "number.zone_c_temp_night")
        # Only slow physical settings invalidate training windows, not normal commands.
        settings = tuple(rows.get(i, {}).get("s") for i in setting_ids[:2])
        if self.last is None or not 0 < now-self.last <= 180 or settings != self.settings:
            self.reset_pending()
        self.last, self.settings = now, settings
        f = features(rows)
        for eid in list(self.pending):
            if eid not in f:
                self.pending.pop(eid)
        result = {}
        for eid, (t, target, x) in f.items():
            m = self.rooms.setdefault(eid, {"w":[0.0]*6, "n":0, "ae":0., "baseline_ae":0.})
            if not isinstance(m.get("w"), list) or len(m["w"]) != 6:
                m = self.rooms[eid] = {"w":[0.0]*6, "n":0, "ae":0., "baseline_ae":0.}
            p = self.pending.get(eid)
            if p and (abs(target-p["target"]) > .01 or abs(t-p["last_t"]) > 1.5):
                self.pending.pop(eid)
                p = None
            if p:
                p["last_t"] = t
                if now-p["time"] >= HORIZON:
                    self.pending.pop(eid)
                    if now-p["time"] <= HORIZON+180 and abs(t-p["t"]) <= 3:
                        y = t-p["t"]
                        err = y-p["pred"]
                        m["n"] += 1
                        m["ae"] += abs(err)
                        m["baseline_ae"] += abs(y)
                        norm = 0.1 + sum(v*v for v in p["x"])
                        m["w"] = [max(-3,min(3,w+0.15*err*v/norm))
                                  for w,v in zip(m["w"],p["x"])]
                    p = None
            pred = max(-3,min(3,sum(w*v for w,v in zip(m["w"],x))))
            if not p:
                self.pending[eid] = {"time":now,"t":t,"target":target,"x":x,
                                     "pred":pred,"last_t":t}
            n=m["n"]
            result[eid] = {"temperature":round(t,2), "prediction_30min":round(t+pred,2),
                           "samples":n,"mae":round(m["ae"]/n,3) if n else None,
                           "baseline_mae":round(m["baseline_ae"]/n,3) if n else None,
                           "validated":False}
        return result

def load_model(path):
    try:
        obj = json.loads(path.read_text())
        if obj.get("schema") != 1 or not isinstance(obj.get("rooms"),dict):
            raise ValueError("schema")
        valid = {}
        for k,m in obj["rooms"].items():
            if (isinstance(m,dict) and isinstance(m.get("w"),list) and len(m["w"])==6
                and all(number(v) is not None and abs(float(v))<=3 for v in m["w"])
                and isinstance(m.get("n"),int) and m["n"]>=0
                and all(number(m.get(a)) is not None and float(m[a])>=0 for a in ("ae","baseline_ae"))):
                valid[k] = m
        return valid
    except FileNotFoundError:
        return {}
    except Exception:
        LOG.warning("Modele illisible: reprise sans apprentissage historique")
        try:
            path.rename(path.with_name("model-corrupt-"+BOOT+".json"))
        except OSError:
            pass
        return {}

def update_status(**kwargs):
    with LOCK:
        STATUS.update(kwargs)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/health", "/api/status"):
            self.send_error(404)
            return
        with LOCK:
            body = json.dumps(STATUS, ensure_ascii=False, allow_nan=False, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    options = json.loads(Path("/data/options.json").read_text())
    interval = max(60, min(300, int(options.get("poll_seconds",60))))
    days = max(30,min(365,int(options.get("retention_days",365))))
    max_bytes = max(1,min(8,int(options.get("max_storage_gib",5))))*1024**3
    DATA.mkdir(parents=True, exist_ok=True)
    model_path = DATA/"model.json"
    model = Learner(load_model(model_path))
    server = ThreadingHTTPServer(("0.0.0.0",8099), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    LOG.info("Observation seule; aucun service HA ni API externe; intervalle=%ss",interval)
    last_prune = 0
    last_save = 0
    consecutive = 0
    while True:
        start = time.monotonic()
        now = time.time()
        try:
            raw = read_states()
            rows = clean(raw,now)
            if not rows:
                raise ValueError("no usable entity records")
            if now-last_prune >= 3600:
                prune(DATA,days,max_bytes,now)
                last_prune=now
            size=archive(DATA,{"schema":1,"ts":now,"boot":BOOT,"entities":rows},now)
            consecutive += 1
            preds = model.observe(now,rows) if consecutive>=3 else {}
            if now-last_save>=300:
                atomic_json(model_path,{"schema":1,"rooms":model.rooms})
                last_save=now
            q={}
            for r in rows.values():
                q[r["q"]]=q.get(r["q"],0)+1
            update_status(phase="observation", last_snapshot=dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),
                          entities=len(rows), quality=q, room_predictions=preds, snapshot_bytes=size,
                          boot=BOOT, consecutive=consecutive, last_error=None)
            if consecutive<=3 or consecutive%10==0:
                LOG.info("Collecte: %s entites, qualite=%s, modeles=%s, archive=%s octets",
                         len(rows),q,len(preds),size)
        except Exception as exc:
            consecutive=0
            model.reset_pending()
            model.last=None
            update_status(phase="degrade", room_predictions={}, consecutive=0,
                          last_error=type(exc).__name__)
            LOG.warning("Observation interrompue: %s",type(exc).__name__)
        time.sleep(max(interval-(time.monotonic()-start),10))

if __name__=="__main__":
    main()
