"""Read-only weather observer; never calls heating controls."""
import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import urlopen

LOG = logging.getLogger("chauffage_c.solar")
TZ = ZoneInfo("Europe/Paris")
URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
FIELDS = ("shortwave_radiation", "direct_radiation", "diffuse_radiation",
          "sunshine_duration", "temperature_2m", "wind_speed_10m", "cloud_cover")
TOPIC = "chauffage_c/solaire"
FILE = "/data/solar_weather.json"
FUEL = "sensor.fioul_monitor_paliers"
METRICS = {
    "global_kwh": ("Rayonnement global hier", "kWh/m²"),
    "direct_kwh": ("Rayonnement direct hier", "kWh/m²"),
    "diffuse_kwh": ("Rayonnement diffus hier", "kWh/m²"),
    "sun_h": ("Ensoleillement hier", "h"),
    "wind_kmh": ("Vent moyen hier", "km/h"),
    "temp_c": ("Température extérieure hier (modèle)", "°C"),
    "cloud_pct": ("Nuages hier", "%"),
}

def decode(payload):
    h = payload["hourly"]
    units = payload["hourly_units"]
    expected = ("W/m²", "W/m²", "W/m²", "s", "°C", "km/h", "%")
    if any(units.get(k) != u for k, u in zip(FIELDS, expected)):
        raise ValueError("Unités météo inattendues")
    times = h["time"]
    if any(len(h[k]) != len(times) for k in FIELDS):
        raise ValueError("Séries météo incomplètes")
    rows = {}
    for i, stamp in enumerate(times):
        if not isinstance(stamp, int) or stamp % 3600:
            raise ValueError("Horodatage météo invalide")
        try:
            vals = [float(h[k][i]) for k in FIELDS]
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in vals):
            continue
        if min(vals[:4]) < 0 or vals[3] > 3600 or vals[5] < 0 or not 0 <= vals[6] <= 100:
            continue
        rows[stamp] = vals
    return rows

def aggregate(rows, start, end):
    """UTC ending-hour radiation; partial hours are approximated uniformly.
    Instantaneous wind/temp/cloud use endpoint samples. Reject missing hours."""
    if end <= start:
        return None
    sums = [0.0] * len(FIELDS)
    covered = 0.0
    first = (math.floor(start / 3600) + 1) * 3600
    for stamp in range(first, math.ceil(end / 3600) * 3600 + 1, 3600):
        overlap = max(0, min(end, stamp) - max(start, stamp - 3600))
        if not overlap or stamp not in rows:
            continue
        covered += overlap
        for i, value in enumerate(rows[stamp]):
            sums[i] += value * overlap
    if covered < end - start - 0.01:
        return None
    return {
        "global_kwh": round(sums[0] / 3600000, 3),
        "direct_kwh": round(sums[1] / 3600000, 3),
        "diffuse_kwh": round(sums[2] / 3600000, 3),
        "sun_h": round(sums[3] / 12960000, 2),
        "temp_c": round(sums[4] / covered, 2),
        "wind_kmh": round(sums[5] / covered, 2),
        "cloud_pct": round(sums[6] / covered, 1),
    }

def fetch(now):
    today = datetime.fromtimestamp(now, TZ).date()
    start = today - timedelta(days=46)
    params = dict(latitude=45.21, longitude=5.78, timezone="GMT",
                  timeformat="unixtime", wind_speed_unit="kmh",
                  start_date=start.isoformat(), end_date=today.isoformat(),
                  hourly=",".join(FIELDS))
    with urlopen(URL + "?" + urlencode(params), timeout=20) as response:
        rows = decode(json.load(response))
    return {t: v for t, v in rows.items() if t <= now}

def report(rows, now, fuel_rows):
    today = datetime.fromtimestamp(now, TZ).date()
    daily = []
    for offset in range(45, 0, -1):
        day = today - timedelta(days=offset)
        start = datetime.combine(day, datetime.min.time(), TZ).timestamp()
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), TZ).timestamp()
        values = aggregate(rows, start, end)
        if values:
            daily.append(dict(date=day.isoformat(), **values))
    paliers = []
    for row in fuel_rows:
        try:
            if row.get("reference_initiale"):
                continue
            start = datetime.fromisoformat(row["debut"]).timestamp()
            end = datetime.fromisoformat(row["fin"]).timestamp()
            if end > now:
                continue
            values = aggregate(rows, start, end)
            if values is None:
                continue
            days = (end - start) / 86400
            paliers.append(dict(date=row["date"], allure=row["allure"],
                               litres=row["litres"], l_j=row["l_j"],
                               global_kwh_j=round(values["global_kwh"] / days, 3),
                               soleil_h_j=round(values["sun_h"] / days, 2),
                               temp_c=values["temp_c"], wind_kmh=values["wind_kmh"],
                               cloud_pct=values["cloud_pct"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    yesterday = (today - timedelta(days=1)).isoformat()
    latest = next((d for d in daily if d["date"] == yesterday), {})
    return dict(latest=latest, daily=daily, paliers=paliers,
                source="Open-Meteo Historical Forecast (modèle, pas mesure sur site)",
                updated=datetime.fromtimestamp(now, timezone.utc).isoformat())

class SolarMonitor:
    def __init__(self, read_state, publish, clock=time.time, fetcher=fetch):
        self.read_state = read_state
        self.publish = publish
        self.clock = clock
        self.fetcher = fetcher
        self.next_run = 0

    def discover(self):
        device = dict(identifiers=["chauffage_c_regulateur"],
                      name="Régulation intelligente - Circuit C",
                      manufacturer="Régulation locale", model="Circuit C")
        for key, (name, unit) in METRICS.items():
            identity = "chauffage_c_solaire_" + key
            config = dict(name=name, unique_id=identity,
                          default_entity_id="sensor." + identity,
                          state_topic=TOPIC + "/data", device=device,
                          value_template="{{ value_json.latest." + key + " | default(none) }}",
                          unit_of_measurement=unit, state_class="measurement",
                          availability_topic=TOPIC + "/availability", expire_after=14400)
            self.publish("homeassistant/sensor/" + identity + "/config", json.dumps(config), True)
        identity = "chauffage_c_solaire_historique"
        config = dict(name="Historique solaire et fioul", unique_id=identity,
                      default_entity_id="sensor." + identity,
                      state_topic=TOPIC + "/data", device=device,
                      value_template="{{ value_json.daily | length }}",
                      json_attributes_topic=TOPIC + "/data",
                      json_attributes_template="{{ {'daily': value_json.daily, 'paliers': value_json.paliers, 'source': value_json.source, 'updated': value_json.updated} | to_json }}",
                      availability_topic=TOPIC + "/availability", expire_after=14400)
        self.publish("homeassistant/sensor/" + identity + "/config", json.dumps(config), True)

    def cycle(self):
        now = self.clock()
        if now < self.next_run:
            return
        self.next_run = now + 900
        try:
            rows = self.fetcher(now)
            record = self.read_state(FUEL) or {}
            result = report(rows, now, record.get("attributes", {}).get("rows", []))
            if not result["latest"]:
                raise ValueError("Hier: données météo incomplètes")
            tmp = FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"hourly": rows, **result}, handle, ensure_ascii=False)
            os.replace(tmp, FILE)
            self.discover()
            self.publish(TOPIC + "/data", json.dumps(result, ensure_ascii=False), False)
            self.publish(TOPIC + "/availability", "online", False)
            self.next_run = now + 3600
            LOG.info("Observation solaire: %d jours complets, %d paliers rapprochés",
                     len(result["daily"]), len(result["paliers"]))
        except Exception as exc:
            LOG.warning("Observation solaire indisponible (sans effet chauffage): %s", exc)
            try:
                self.publish(TOPIC + "/availability", "offline", False)
            except Exception:
                pass
