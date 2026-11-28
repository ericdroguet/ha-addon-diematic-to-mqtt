"""Experimental local thermal model. No services, external calls or physical power estimates."""
import hashlib
import json
import math
import re

HORIZON = 1800
N_FEATURES = 11
MAX_ROOMS = 100
FIELDS = {"area_m2", "height_min_m", "height_max_m", "height_mean_m",
          "controlled_emitters", "manual_emitters", "insulation",
          "neighbors", "attic", "air_conditioners", "covers"}
ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


def numeric(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x) if math.isfinite(x) else None


def bounded(x, lo, hi):
    n = numeric(x)
    return n if n is not None and lo <= n <= hi else None


def validate_profiles(payload, catalog=None):
    """Reject unknown fields, private entities, nonfinite values and dangling mappings."""
    if not isinstance(payload, dict) or set(payload) != {"schema", "rooms"} or type(payload["schema"]) is not int or payload["schema"] != 1:
        raise ValueError("Profil thermique: schema invalide")
    rooms = payload["rooms"]
    if not isinstance(rooms, dict) or len(rooms) > MAX_ROOMS:
        raise ValueError("Nombre de zones invalide")
    def eid(value, domains):
        if (not isinstance(value, str) or len(value) > 250 or not ENTITY.fullmatch(value)
                or value.split(".")[0] not in domains or (catalog is not None and value not in catalog)):
            raise ValueError("Entite thermique invalide ou non observee")
        return value
    out = {}
    for room, p in rooms.items():
        eid(room, {"climate"})
        if not isinstance(p, dict) or set(p) - FIELDS:
            raise ValueError("Champ de profil inconnu")
        q = {}
        for field, limits in (("area_m2",(1,1000)),("height_min_m",(.5,15)),
                              ("height_max_m",(.5,15)),("height_mean_m",(.5,15))):
            if field in p:
                n = bounded(p[field], *limits)
                if n is None:
                    raise ValueError("Dimension invalide")
                q[field] = n
        if q.get("height_min_m", .5) > q.get("height_max_m",15) or not q.get("height_min_m",.5) <= q.get("height_mean_m",q.get("height_min_m",.5)) <= q.get("height_max_m",15):
            raise ValueError("Hauteurs incoherentes")
        for field in ("controlled_emitters", "manual_emitters"):
            if field in p:
                n=p[field]
                if type(n) is not int or not 0 <= n <= 30:
                    raise ValueError("Nombre emetteurs invalide")
                q[field]=n
        if "insulation" in p:
            if not isinstance(p["insulation"],str) or p["insulation"] not in ("low","medium","high","mixed","unknown"):
                raise ValueError("Isolation invalide")
            q["insulation"]=p["insulation"]
        for field, domains in (("neighbors",{"climate"}),("air_conditioners",{"climate"}),("covers",{"cover"})):
            if field in p:
                vals=p[field]
                if not isinstance(vals,list) or len(vals)>20:
                    raise ValueError("Liste de correspondances invalide")
                vals=[eid(v,domains) for v in vals]
                if room in vals or len(vals)!=len(set(vals)):
                    raise ValueError("Correspondance circulaire ou dupliquee")
                q[field]=sorted(vals)
        if "attic" in p:
            q["attic"]=eid(p["attic"],{"climate","sensor"})
        out[room]=q
    return {"schema":1,"rooms":out}


def load_profiles(path):
    try:
        return validate_profiles(json.loads(path.read_text()))
    except FileNotFoundError:
        return {"schema":1,"rooms":{}}
    except (ValueError,TypeError,OSError,AttributeError):
        return None


def signature(p):
    return hashlib.sha256(json.dumps(p,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def fresh(rows, eid, age=3600):
    r=rows.get(eid,{})
    if r.get("q")!="ok" or bounded(r.get("age"),-60,age) is None:
        return None
    return r


def temperature(rows,eid):
    r=fresh(rows,eid)
    if r is None:
        return None
    return bounded(r.get("a",{}).get("current_temperature") if eid.startswith("climate.") else r.get("s"),-40,65)


def thermal_features(rows, base, profiles):
    """Heat-balance proxies, not watts: learn coefficients from observations.
    Missing mapped data invalidates the sample, never becomes an assumed zero.
    No guessed volume or conductance. Mean height is used only if explicitly supplied.
    """
    out={}
    pump=fresh(rows,"binary_sensor.zone_c_pump",1800)
    dep=fresh(rows,"sensor.zone_c_departure_temp",1800)
    if pump is None or dep is None or pump.get("s") not in ("on","off"):
        return out
    d=bounded(dep.get("s"),0,100)
    if d is None:
        return out
    for eid,p in profiles.items():
        if eid not in base:
            continue
        t,target,x=base[eid]
        # Scale the heat availability proxy by floor area, and known mean height.
        # Counts do NOT assert equal radiator power; the fitted coefficients absorb differences.
        scale=20/p["area_m2"] if "area_m2" in p else 1.
        if "height_mean_m" in p:
            scale*=2.5/p["height_mean_m"]
        scale=max(.1,min(5.,scale))
        available=max(0.,(d-t)/60) if pump["s"]=="on" else 0.
        controlled=available*float(x[3])*p.get("controlled_emitters",1)*scale
        manual=available*p.get("manual_emitters",0)*scale
        neighbors=[temperature(rows,i) for i in p.get("neighbors",[])]
        if any(v is None for v in neighbors):
            continue
        coupling=(sum(neighbors)/len(neighbors)-t)/20 if neighbors else 0.
        attic=temperature(rows,p["attic"]) if "attic" in p else None
        if "attic" in p and attic is None:
            continue
        attic_delta=(attic-t)/30 if attic is not None else 0.
        ac=[]
        for i in p.get("air_conditioners",[]):
            r=fresh(rows,i)
            if r is None:
                break
            mode=r.get("s")
            action=r.get("a",{}).get("hvac_action")
            # Mode indicates availability, not actual compressor power.
            if mode=="off" or action=="idle":
                ac.append(0.)
            elif action=="heating" or mode=="heat":
                ac.append(1.)
            elif action=="cooling" or mode=="cool":
                ac.append(-1.)
            elif mode in ("dry","fan_only"):
                ac.append(0.)
            else:
                break
        if len(ac)!=len(p.get("air_conditioners",[])):
            continue
        covers=[]
        for i in p.get("covers",[]):
            r=fresh(rows,i)
            v=bounded(r.get("a",{}).get("current_position"),0,100) if r else None
            if v is None:
                break
            covers.append(v/100)
        if len(covers)!=len(p.get("covers",[])):
            continue
        solar=0.
        if covers:
            sun=fresh(rows,"sun.sun",1800)
            elevation=bounded(sun.get("a",{}).get("elevation"),-90,90) if sun else None
            if elevation is None:
                continue
            solar=max(0.,math.sin(math.radians(elevation)))*sum(covers)/len(covers)
        # Separate external, heating, manual, neighbour, attic, AC, solar terms.
        vector=[1.,x[1],controlled,manual,x[3],x[4],x[5],coupling,attic_delta,
                sum(ac),solar]
        out[eid]=(t,target,vector,signature(p))
    return out


class Shadow:
    """Independent online model, paired scoring with the old model and persistence."""
    def __init__(self,saved=None):
        self.rooms=saved if isinstance(saved,dict) else {}
        self.pending={}
        self.last=None
        self.settings=None

    def reset_pending(self):
        self.pending.clear()
        self.last=None

    def observe(self,now,rows,base,profiles,legacy):
        settings=tuple(rows.get(i,{}).get("s") for i in
                       ("sensor.zone_c_heating_slope","sensor.trial_valve_band"))
        if self.last is None or not 0<now-self.last<=180 or settings!=self.settings:
            self.reset_pending()
        self.last,self.settings=now,settings
        f=thermal_features(rows,base,profiles)
        for eid in list(self.pending):
            if eid not in f:
                self.pending.pop(eid)
        result={}
        for eid,(t,target,x,sig) in f.items():
            m=self.rooms.get(eid)
            if not m or m.get("signature")!=sig:
                # Profile edits invalidate only the affected room's shadow fit.
                m=self.rooms[eid]={"signature":sig,"w":[0.]*N_FEATURES,"n":0,
                                    "ae":0.,"baseline_ae":0.,"legacy_ae":0.,"paired_n":0,"paired_ae":0.}
                self.pending.pop(eid,None)
            p=self.pending.get(eid)
            if p and (abs(target-p["target"])>.01 or abs(t-p["last_t"])>1.5):
                self.pending.pop(eid,None)
                p=None
            if p:
                p["last_t"]=t
                if now-p["time"]>=HORIZON:
                    self.pending.pop(eid,None)
                    if now-p["time"]<=HORIZON+180 and abs(t-p["t"])<=3:
                        y=t-p["t"]
                        err=y-p["pred"]
                        m["n"]+=1
                        m["ae"]+=abs(err)
                        m["baseline_ae"]+=abs(y)
                        if p["legacy"] is not None:
                            m["paired_n"]+=1
                            m["paired_ae"]+=abs(err)
                            m["legacy_ae"]+=abs(t-p["legacy"])
                        norm=.1+sum(v*v for v in p["x"])
                        m["w"]=[max(-3,min(3,w+.15*err*v/norm)) for w,v in zip(m["w"],p["x"])]
                    p=None
            pred=max(-3,min(3,sum(w*v for w,v in zip(m["w"],x))))
            if not p:
                self.pending[eid]={"time":now,"t":t,"target":target,"x":x,
                    "pred":pred,"last_t":t,"legacy":numeric(legacy.get(eid,{}).get("prediction_30min"))}
            n,pn=m["n"],m["paired_n"]
            result[eid]={"temperature":round(t,2),"prediction_30min":round(t+pred,2),
                "samples":n,"mae":round(m["ae"]/n,3) if n else None,
                "baseline_mae":round(m["baseline_ae"]/n,3) if n else None,
                "paired_samples":pn,"paired_shadow_mae":round(m["paired_ae"]/pn,3) if pn else None,
                "paired_legacy_mae":round(m["legacy_ae"]/pn,3) if pn else None,
                "validated":False}
        return result


def load_shadow(path):
    try:
        o=json.loads(path.read_text())
        if o.get("schema")!=1 or not isinstance(o.get("rooms"),dict):
            return {}
        out={}
        for k,m in o["rooms"].items():
            if (isinstance(m,dict) and isinstance(m.get("signature"),str)
                and isinstance(m.get("w"),list) and len(m["w"])==N_FEATURES
                and all(bounded(v,-3,3) is not None for v in m["w"])
                and all(type(m.get(a)) is int and m[a]>=0 for a in ("n","paired_n"))
                and m["paired_n"]<=m["n"]
                and all(numeric(m.get(a)) is not None and m[a]>=0 for a in
                        ("ae","baseline_ae","legacy_ae","paired_ae"))):
                out[k]=m
        return out
    except (ValueError,TypeError,OSError,AttributeError):
        return {}
