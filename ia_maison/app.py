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
import thermal

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


# Human annotations are descriptive only; never execute notes or replace raw states.
KNOWLEDGE_LOCK = threading.Lock()
KNOWLEDGE = {"schema":1,"entities":{},"events":[]}
PROFILES = {"schema":1,"rooms":{}}
CATALOG = {}
CSRF = uuid.uuid4().hex + uuid.uuid4().hex
ROLES = {"chauffage","ecs","energie","meteo","hydraulique","presence","ouvrants",
         "piscine","recharge","climatisation","reseau","autre","ignore","incertain"}
EVENT_TYPES = {"rideaux","invites","cheminee","reglage","capteur","autre"}

def validate_knowledge(payload, catalog, current, now):
    if not isinstance(payload,dict):
        raise ValueError("Objet JSON attendu")
    result = {"schema":1,"entities":dict(current["entities"]),"events":list(current["events"])}
    kind = payload.get("kind")
    if kind == "entity":
        eid, role, note = payload.get("entity_id"), payload.get("role"), payload.get("note","")
        if not isinstance(eid,str) or eid not in catalog or eid.split(".",1)[0] in PRIVATE:
            raise ValueError("Entité inconnue ou privée")
        if not isinstance(role,str) or role not in ROLES or not isinstance(note,str) or len(note)>1000:
            raise ValueError("Rôle ou note invalide")
        if len(result["entities"])>=3000 and eid not in result["entities"]:
            raise ValueError("Limite des annotations atteinte")
        result["entities"][eid]={"role":role,"note":note.strip(),"updated_at":now}
    elif kind == "event":
        category,note = payload.get("category"),payload.get("note")
        if not isinstance(category,str) or category not in EVENT_TYPES or not isinstance(note,str) or not note.strip() or len(note)>1000:
            raise ValueError("Catégorie ou description invalide")
        if len(result["events"])>=10000:
            raise ValueError("Limite du journal atteinte")
        result["events"].append({"id":uuid.uuid4().hex,"category":category,"note":note.strip(),"at":now})
    elif kind == "remove":
        eid=payload.get("entity_id")
        if not isinstance(eid,str) or eid not in result["entities"]:
            raise ValueError("Annotation inexistante")
        del result["entities"][eid]
    elif kind == "remove_event":
        event_id=payload.get("id")
        if not isinstance(event_id,str) or not any(e.get("id")==event_id for e in result["events"]):
            raise ValueError("Événement inexistant")
        result["events"]=[e for e in result["events"] if e.get("id")!=event_id]
    else:
        raise ValueError("Action inconnue")
    return result

def load_knowledge(path):
    try:
        data=json.loads(path.read_text())
        if (data.get("schema")!=1 or not isinstance(data.get("entities"),dict)
            or not isinstance(data.get("events"),list)
            or len(data["entities"])>3000 or len(data["events"])>10000
            or not all(isinstance(v,dict) and isinstance(v.get("role"),str)
                       and v["role"] in ROLES and isinstance(v.get("note"),str)
                       for v in data["entities"].values())
            or not all(isinstance(v,dict) and isinstance(v.get("note"),str)
                       and isinstance(v.get("id"),str) for v in data["events"])):
            raise ValueError("schema")
        return data
    except FileNotFoundError:
        return {"schema":1,"entities":{},"events":[]}
    except Exception:
        LOG.warning("Mémoire humaine illisible; écriture bloquée pour préserver le fichier")
        return None

def suggest_role(eid,r):
    """Conservative, descriptive suggestions only; not equipment diagnosis."""
    domain,name=eid.split(".",1)
    a=r.get("a",{})
    unit=a.get("unit_of_measurement")
    dc=a.get("device_class")
    def has(*words):
        return any(w in name for w in words)
    def suggestion(role,note):
        return {"role":role,"note":note,"source":"automatic_rule"}
    if has("frost_protection","protection_contre_le_gel","antiice"):
        return suggestion("chauffage","Protection hors gel ou seuil associé. Un état inconnu ne signifie pas que la protection est désactivée.")
    if has("overheat_protection","protection_contre_la_surchauffe"):
        return suggestion("chauffage","Protection contre la surchauffe ou seuil associé. État physique non confirmé.")
    if domain=="cover":
        return suggestion("ouvrants","Volet, store ou autre ouvrant. Un état inconnu ne confirme ni position ni fermeture.")
    if has("hot_water","ecs_","ballon_ecs","eau_chaude"):
        return suggestion("ecs","Entité liée à l'eau chaude sanitaire. Mesure ou commande à distinguer selon son type.")
    if has("fioul","linky","tempo","ecojoko","consommation","energie","energy","puissance","power"):
        return suggestion("energie","Entité énergétique probable. Ne pas additionner sans vérifier les doublons, les unités et le périmètre.")
    if has("wilo","zone_c_pump","pompe_circuit","delta_t","chauffage_c_melange"):
        return suggestion("hydraulique","Entité liée au circuit hydraulique ou à sa régulation. Une valeur absente ne vaut pas zéro.")
    if has("diematic","zone_a_","zone_b_","zone_c_","chauffage_c","trial_boiler","burner","bruleur","mode_vacances"):
        return suggestion("chauffage","Paramètre ou état de régulation du chauffage probable. Usage exact à confirmer si nécessaire.")
    if has("pac_piscine","filtration_piscine","pompe_piscine"):
        return suggestion("piscine","Équipement lié à la piscine.")
    if has("g6_","g6bullet") and has("lux_trigger","plage_dynamique","infrared","disk_write"):
        return suggestion("autre","Réglage ou diagnostic de caméra, sans usage thermique direct identifié.")
    if has("wall_connector","wallconnector","tesla_charg"):
        return suggestion("recharge","Équipement ou donnée de recharge électrique probable.")
    if has("wifi","wi_fi","rssi","signal_strength","uptime","firmware","ip_address"):
        return suggestion("reseau","Information de connectivité ou diagnostic technique probable.")
    if domain=="weather" or has("weather","meteo","ext_temp","outdoor_temperature"):
        return suggestion("meteo","Donnée météorologique ou température extérieure probable.")
    if dc in ("energy","power","current","voltage") or unit in ("kWh","Wh","W","kW","A","V"):
        return suggestion("energie","Grandeur électrique selon l'unité ou la classe Home Assistant. Source et périmètre à vérifier.")
    return None

def make_catalog(rows):
    # Never retain arbitrary raw strings, GPS, attributes or secret text.
    return {eid:{"quality":r["q"],"state":r["s"],"age":r["age"],
                 "unit":r["a"].get("unit_of_measurement"),"suggestion":suggest_role(eid,r)}
            for eid,r in rows.items() if eid.split(".",1)[0] not in PRIVATE}

def knowledge_view():
    with KNOWLEDGE_LOCK:
        current=KNOWLEDGE or {"entities":{},"events":[]}
        return {"writable":KNOWLEDGE is not None,"csrf":CSRF,
                "catalog":CATALOG,"annotations":current["entities"],
                "events":current["events"][-100:],"event_count":len(current["events"])}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            body = Path(__file__).with_name("dashboard.html").read_bytes()
            content_type = "text/html; charset=utf-8"
        elif path == "/api/thermal/profiles":
            with KNOWLEDGE_LOCK:
                body=json.dumps({"writable":PROFILES is not None,"profiles":PROFILES},ensure_ascii=False,allow_nan=False).encode()
            content_type = "application/json; charset=utf-8"
        elif path == "/api/knowledge/session":
            with KNOWLEDGE_LOCK:
                body=json.dumps({"writable":KNOWLEDGE is not None,"csrf":CSRF}).encode()
            content_type = "application/json; charset=utf-8"
        elif path == "/api/knowledge/entity":
            from urllib.parse import urlsplit, parse_qs
            eid=parse_qs(urlsplit(self.path).query).get("id",[""])[0]
            with KNOWLEDGE_LOCK:
                body=json.dumps({"entity_id":eid,"observed":eid in CATALOG,
                    "annotation":(KNOWLEDGE or {}).get("entities",{}).get(eid)},ensure_ascii=False).encode()
            content_type = "application/json; charset=utf-8"
        elif path == "/api/knowledge/summary":
            with KNOWLEDGE_LOCK:
                annotations=(KNOWLEDGE or {}).get("entities",{})
                issues={k:v for k,v in CATALOG.items() if v["quality"]!="ok"}
                body=json.dumps({"issues":len(issues),
                    "suggested":sum(bool(v.get("suggestion")) for v in issues.values()),
                    "needs_review":sum(not v.get("suggestion") and k not in annotations for k,v in issues.items()),
                    "annotated":len(annotations)},ensure_ascii=False).encode()
            content_type = "application/json; charset=utf-8"
        elif path == "/api/knowledge":
            body = json.dumps(knowledge_view(),ensure_ascii=False,allow_nan=False).encode()
            content_type = "application/json; charset=utf-8"
        elif path in ("/health", "/api/status"):
            with LOCK:
                body = json.dumps(STATUS, ensure_ascii=False, allow_nan=False, indent=2).encode()
            content_type = "application/json; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global KNOWLEDGE,PROFILES
        path=self.path.split("?")[0]
        if path not in ("/api/knowledge","/api/thermal/profiles"):
            self.send_error(404)
            return
        # Custom per-boot CSRF token; no CORS, no external access or service calls.
        import hmac
        if not hmac.compare_digest(self.headers.get("X-IA-CSRF",""),CSRF):
            self.send_error(403)
            return
        if self.headers.get("Content-Type","").split(";")[0].strip()!="application/json":
            self.send_error(415)
            return
        try:
            size=int(self.headers.get("Content-Length","0"))
            if not 0<size<=(32768 if path=="/api/thermal/profiles" else 8192):
                raise ValueError("Taille invalide")
            self.connection.settimeout(5)
            payload=json.loads(self.rfile.read(size))
            with KNOWLEDGE_LOCK:
                if path=="/api/thermal/profiles":
                    if PROFILES is None:
                        raise OSError("Profil thermique en lecture seule")
                    next_data=thermal.validate_profiles(payload,CATALOG)
                    atomic_json(DATA/"thermal_profiles.json",next_data)
                    PROFILES=next_data
                else:
                    if KNOWLEDGE is None:
                        raise OSError("Mémoire en lecture seule")
                    next_data=validate_knowledge(payload,CATALOG,KNOWLEDGE,
                                dt.datetime.now(dt.timezone.utc).isoformat())
                    atomic_json(DATA/"human_knowledge.json",next_data)
                    KNOWLEDGE=next_data
            status,response=200,{"saved":True}
        except (ValueError,TypeError,UnicodeError) as exc:
            status,response=400,{"error":str(exc)[:200]}
        except Exception:
            status,response=503,{"error":"Mémoire non enregistrée; vérifier le journal"}
        body=json.dumps(response,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

def main():
    global KNOWLEDGE,CATALOG,PROFILES
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    options = json.loads(Path("/data/options.json").read_text())
    interval = max(60, min(300, int(options.get("poll_seconds",60))))
    days = max(30,min(365,int(options.get("retention_days",365))))
    max_bytes = max(1,min(8,int(options.get("max_storage_gib",5))))*1024**3
    DATA.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE = load_knowledge(DATA/"human_knowledge.json")
    model_path = DATA/"model.json"
    model = Learner(load_model(model_path))
    PROFILES=thermal.load_profiles(DATA/"thermal_profiles.json")
    shadow_path=DATA/"thermal_shadow.json"
    shadow=thermal.Shadow(thermal.load_shadow(shadow_path))
    solar_path=DATA/"thermal_solar.json"
    solar_model=thermal.Shadow(thermal.load_shadow(solar_path))
    server = ThreadingHTTPServer(("0.0.0.0",8099), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    LOG.info("Observation seule; aucun service HA ni API externe; intervalle=%ss",interval)
    last_prune = 0
    last_save = 0
    last_shadow_save = 0
    last_solar_save = 0
    consecutive = 0
    total_cycles = 0
    failures = 0
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
            with KNOWLEDGE_LOCK:
                CATALOG=make_catalog(rows)
            size=archive(DATA,{"schema":1,"ts":now,"boot":BOOT,"entities":rows},now)
            consecutive += 1
            total_cycles += 1
            preds = model.observe(now,rows) if consecutive>=3 else {}
            if now-last_save>=300:
                atomic_json(model_path,{"schema":1,"rooms":model.rooms})
                last_save=now
            # Shadow failure must never interrupt the existing observation and learner.
            shadow_preds={}
            solar_preds={}
            solar_error=None
            shadow_error=None
            with KNOWLEDGE_LOCK:
                profiles=(PROFILES or {}).get("rooms",{})
                profile_ok=PROFILES is not None
            try:
                if consecutive>=3 and profile_ok:
                    base_profiles={k:{a:b for a,b in p.items() if a!="solar_surfaces"} for k,p in profiles.items()}
                    shadow_preds=shadow.observe(now,rows,features(rows),base_profiles,preds)
                if now-last_shadow_save>=300:
                    atomic_json(shadow_path,{"schema":1,"rooms":shadow.rooms})
                    last_shadow_save=now
            except Exception as exc:
                shadow.reset_pending()
                shadow_error=type(exc).__name__
                LOG.warning("Modele thermique experimental: %s",shadow_error)
            try:
                solar_profiles={k:p for k,p in profiles.items() if p.get("solar_surfaces")}
                if consecutive>=3 and profile_ok:
                    solar_preds=solar_model.observe(now,rows,features(rows),solar_profiles,shadow_preds)
                if now-last_solar_save>=300:
                    atomic_json(solar_path,{"schema":1,"rooms":solar_model.rooms})
                    last_solar_save=now
            except Exception as exc:
                solar_model.reset_pending()
                solar_error=type(exc).__name__
                LOG.warning("Modele solaire experimental: %s",solar_error)
            q={}
            for r in rows.values():
                q[r["q"]]=q.get(r["q"],0)+1
            update_status(phase="observation", last_snapshot=dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),
                          entities=len(rows), quality=q, room_predictions=preds, snapshot_bytes=size,
                          boot=BOOT, consecutive=consecutive, last_error=None,
                          session_samples=total_cycles, session_failures=failures,
                          storage_bytes=sum(p.stat().st_size for p in DATA.glob("obs-*.jsonl.gz")),
                          storage_limit_bytes=max_bytes, retention_days=days, poll_seconds=interval,
                          learned_samples=sum(m["n"] for m in model.rooms.values()),
                          learned_rooms=sum(m["n"]>0 for m in model.rooms.values()),
                          thermal_shadow={"configured_rooms":len(profiles),"profile_ok":profile_ok,
                              "predictions":shadow_preds,"error":shadow_error,"control":False},
                          solar_shadow={"configured_rooms":len(solar_profiles),"context":thermal.solar_context(rows),
                              "predictions":solar_preds,"error":solar_error,"control":False})
            if consecutive<=3 or consecutive%10==0:
                LOG.info("Collecte: %s entites, qualite=%s, modeles=%s, archive=%s octets",
                         len(rows),q,len(preds),size)
        except Exception as exc:
            consecutive=0
            failures += 1
            model.reset_pending()
            model.last=None
            shadow.reset_pending()
            solar_model.reset_pending()
            update_status(phase="degrade", room_predictions={}, consecutive=0,
                          last_error=type(exc).__name__, session_failures=failures)
            LOG.warning("Observation interrompue: %s",type(exc).__name__)
        time.sleep(max(interval-(time.monotonic()-start),10))

if __name__=="__main__":
    main()
