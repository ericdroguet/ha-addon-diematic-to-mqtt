"""Forecast and shadow-only solar correction. No connection to control logic."""
import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen
from solar_monitor import TZ, FILE as HISTORY, decode, aggregate

LOG = logging.getLogger("chauffage_c.solar_forecast")
TOPIC = "chauffage_c/solaire_prevision"
FILE = "/data/solar_forecast_archive.json"
URL = "https://api.open-meteo.com/v1/forecast"
LEADS = (1, 6, 24, 72)
FIELDS = ("shortwave_radiation", "direct_radiation", "diffuse_radiation",
          "sunshine_duration", "temperature_2m", "wind_speed_10m", "cloud_cover")
METRICS = {
    "solar_6h": ("Soleil prévu 6 h", "kWh/m²"),
    "solar_24h": ("Soleil prévu 24 h", "kWh/m²"),
    "solar_72h": ("Soleil prévu 72 h", "kWh/m²"),
    "south_6h": ("Façade sud de référence 6 h", "kWh/m²"),
    "shadow": ("Correction solaire simulée", "°C"),
}
def fetch():
    params = dict(latitude=45.21, longitude=5.78, timezone="GMT",
                  timeformat="unixtime", wind_speed_unit="kmh", forecast_days=5,
                  tilt=90, azimuth=0, hourly=",".join(FIELDS+("global_tilted_irradiance",)))
    with urlopen(URL+"?"+urlencode(params), timeout=20) as response:
        payload = json.load(response)
    rows = decode(payload)
    h = payload["hourly"]
    south = {}
    if payload["hourly_units"].get("global_tilted_irradiance") == "W/m²":
        for stamp, value in zip(h["time"], h.get("global_tilted_irradiance", [])):
            if isinstance(value, (int,float)) and math.isfinite(value) and value >= 0:
                south[stamp] = value
    return rows, south

def sum_south(south, start, end):
    if end <= start:
        return None
    energy = 0.
    for stamp in range((math.floor(start/3600)+1)*3600, math.ceil(end/3600)*3600+1, 3600):
        if stamp not in south:
            return None
        energy += south[stamp]*max(0,min(stamp,end)-max(start,stamp-3600))
    return round(energy/3600000,3)

def archive_forecast(archive, rows, issued):
    # Whole future hours only; fixed lead buckets relative to next full hour.
    first = (math.floor(issued/3600)+2)*3600
    cutoff = issued-35*86400
    clean = {k:v for k,v in archive.items() if v["target"] >= cutoff}
    for lead in LEADS:
        target = first+(lead-1)*3600
        if target in rows:
            key = f"{lead}:{target}"
            clean.setdefault(key, dict(lead=lead, target=target, issued=issued,
                                       ghi=rows[target][0], temp=rows[target][4]))
    return clean

def verify(archive, historical, now):
    out = []
    # Reference is historical weather MODEL, not local pyranometer.
    for lead in LEADS:
        errors = []
        temp_errors = []
        daylight = []
        for row in archive.values():
            if row["lead"] != lead or row["target"] > now-86400:
                continue
            ref = historical.get(str(row["target"]))
            if not ref:
                continue
            error = row["ghi"]-ref[0]
            errors.append(error)
            temp_errors.append(abs(row["temp"]-ref[4]))
            if max(row["ghi"],ref[0]) >= 20:
                daylight.append(error)
        if errors:
            out.append(dict(lead=lead,n=len(errors),daylight_n=len(daylight),
                            mae=round(sum(map(abs,errors))/len(errors),1),
                            daylight_mae=round(sum(map(abs,daylight))/len(daylight),1) if daylight else None,
                            bias=round(sum(errors)/len(errors),1),
                            temp_mae=round(sum(temp_errors)/len(temp_errors),2)))
    return out

def simulate(energy, gaps, local_hour):
    """Illustrative heuristic, uncalibrated; never connected to controller."""
    if energy is None or gaps is None:
        return None, "données incomplètes"
    if not 9 <= local_hour < 17:
        return 0., "hors fenêtre solaire"
    if max(gaps, default=1) > .3+1e-6:
        return 0., "protection confort"
    if energy >= 1.2:
        return -.5, "soleil fort prévu"
    if energy >= .6:
        return -.25, "soleil modéré prévu"
    return 0., "soleil insuffisant"

def load(path):
    try:
        with open(path,encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError,ValueError):
        return {}

def save(path, data):
    tmp=path+".tmp"
    with open(tmp,"w",encoding="utf-8") as handle:
        json.dump(data,handle,separators=(",",":"))
    os.replace(tmp,path)

def build_report(rows,south,now):
    out={}
    for lead in (3,6,24,72):
        values=aggregate(rows,now,now+lead*3600)
        if values is None:
            raise ValueError(f"prévision incomplète {lead} h")
        out[f"solar_{lead}h"]=values["global_kwh"]
    out["south_6h"]=sum_south(south,now,now+21600)
    hourly=[]
    first=(math.floor(now/3600)+1)*3600
    for stamp in range(first,first+72*3600,3600):
        v=rows[stamp]
        hourly.append(dict(time=datetime.fromtimestamp(stamp,TZ).isoformat(),
                           ghi=v[0], direct=v[1], diffuse=v[2], south=south.get(stamp),
                           temp=v[4], wind=v[5], cloud=v[6]))
    daily=[]
    today=datetime.fromtimestamp(now,TZ).date()
    for i in range(3):
        day=today+timedelta(days=i)
        start=max(now,datetime.combine(day,datetime.min.time(),TZ).timestamp())
        end=datetime.combine(day+timedelta(days=1),datetime.min.time(),TZ).timestamp()
        values=aggregate(rows,start,end)
        if values:
            daily.append(dict(date=day.isoformat(),partial=i==0,
                              south_kwh=sum_south(south,start,end),**values))
    return dict(latest=out,hourly=hourly,daily=daily,
                issued=datetime.fromtimestamp(now,timezone.utc).isoformat(),
                source="Open-Meteo Forecast; façade sud verticale théorique; simulation seule")

class SolarForecast:
    def __init__(self,read_state,publish,rooms,eve_room,eve_sensor,clock=time.time,fetcher=fetch):
        self.read_state=read_state
        self.publish=publish
        self.rooms=tuple(rooms)+(eve_room,)
        self.eve_room=eve_room
        self.eve_sensor=eve_sensor
        self.clock=clock
        self.fetcher=fetcher
        self.next_run=0
    def gaps(self):
        gaps=[]
        for entity in self.rooms:
            r=self.read_state(entity) or {}
            a=r.get("attributes",{})
            if r.get("state") in (None,"unknown","unavailable","off"):
                return None
            actual=a.get("current_temperature")
            if entity==self.eve_room:
                actual=(self.read_state(self.eve_sensor) or {}).get("state")
            try:
                actual,target=float(actual),float(a.get("temperature"))
                if not (5<=actual<=40 and 5<=target<=35):
                    return None
                gaps.append(target-actual)
            except (ValueError,TypeError):
                return None
        return gaps
    def discover(self):
        device=dict(identifiers=["chauffage_c_regulateur"],name="Régulation intelligente - Circuit C",
                    manufacturer="Régulation locale",model="Circuit C")
        for key,(name,unit) in METRICS.items():
            identity="chauffage_c_solaire_prevision_"+key
            cfg=dict(name=name,unique_id=identity,default_entity_id="sensor."+identity,
                     state_topic=TOPIC+"/data",device=device,
                     value_template="{{ value_json.latest."+key+" | default(none) }}",
                     unit_of_measurement=unit,state_class="measurement",
                     availability_topic=TOPIC+"/availability",expire_after=7200)
            self.publish("homeassistant/sensor/"+identity+"/config",json.dumps(cfg),True)
        identity="chauffage_c_solaire_prevision_historique"
        cfg=dict(name="Prévisions solaires et vérification",unique_id=identity,
                 default_entity_id="sensor."+identity,state_topic=TOPIC+"/data",
                 value_template="{{ value_json.hourly | length }}",
                 json_attributes_topic=TOPIC+"/data",
                 json_attributes_template="{{ {'hourly': value_json.hourly, 'daily': value_json.daily, 'issued': value_json.issued, 'verification': value_json.verification, 'reason': value_json.reason, 'source': value_json.source} | to_json }}",
                 availability_topic=TOPIC+"/availability",expire_after=7200,device=device)
        self.publish("homeassistant/sensor/"+identity+"/config",json.dumps(cfg),True)
    def cycle(self):
        if self.clock()<self.next_run:
            return
        self.next_run=self.clock()+900
        try:
            rows,south=self.fetcher()
            now=self.clock()
            result=build_report(rows,south,now)
            archive=archive_forecast(load(FILE),rows,now)
            save(FILE,archive)
            result["verification"]=verify(archive,load(HISTORY).get("hourly",{}),now)
            shadow,reason=simulate(result["latest"]["solar_3h"],self.gaps(),datetime.fromtimestamp(now,TZ).hour)
            result["latest"]["shadow"]=shadow
            result["reason"]=reason
            self.discover()
            self.publish(TOPIC+"/data",json.dumps(result,ensure_ascii=False),False)
            self.publish(TOPIC+"/availability","online",False)
            self.next_run=now+3600
            LOG.info("Prévisions solaires: 72 h, correction SIMULÉE %s (%s), %d archives",shadow,reason,len(archive))
        except Exception as exc:
            LOG.warning("Prévisions solaires indisponibles (aucun effet chauffage): %s",exc)
            try:
                self.publish(TOPIC+"/availability","offline",False)
            except Exception:
                pass
