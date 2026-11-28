import json
import tempfile
import unittest
from pathlib import Path
import thermal

def row(state, **attrs):
    return {"s":state,"a":attrs,"q":"ok","age":10}

class TestThermal(unittest.TestCase):
    def setUp(self):
        self.eid="climate.test_room"
        self.base={self.eid:(20.,21.,[1.,-.5,.5,1.,0.,.2])}
        self.rows={"binary_sensor.zone_c_pump":row("on"),
                   "sensor.zone_c_departure_temp":row(50.),
                   "sensor.test_attic":row(10.),
                   "climate.test_neighbor":row("heat",current_temperature=22.),
                   "climate.test_ac":row("off"),
                   "cover.test":row("open",current_position=50.),
                   "sun.sun":row("above_horizon",elevation=30.)}
        self.profiles={self.eid:{"area_m2":20., "controlled_emitters":1,
            "manual_emitters":1,"attic":"sensor.test_attic",
            "neighbors":["climate.test_neighbor"],"air_conditioners":["climate.test_ac"],
            "covers":["cover.test"]}}
    def test_validate_and_persist(self):
        p=thermal.validate_profiles({"schema":1,"rooms":self.profiles},
                                    set(self.rows)|{self.eid})
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/"profiles.json"
            self.assertEqual(thermal.load_profiles(f),{"schema":1,"rooms":{}})
            f.write_text(json.dumps(p))
            self.assertEqual(thermal.load_profiles(f),p)
            f.write_text("{")
            self.assertIsNone(thermal.load_profiles(f))
            self.assertEqual(f.read_text(),"{")
    def test_reject_private_unknown_duplicate_invalid(self):
        for p in ({"neighbors":["person.test"]},{"area_m2":float("nan")},
                  {"height_min_m":4,"height_max_m":2},{"manual_emitters":True},
                  {"covers":["cover.test","cover.test"]},{"neighbors":[self.eid]},
                  {"unexpected":1},{"attic":"sensor.missing"}):
            with self.subTest(p=p), self.assertRaises(ValueError):
                thermal.validate_profiles({"schema":1,"rooms":{self.eid:p}},
                                          set(self.rows)|{self.eid})
    def test_features(self):
        x=thermal.thermal_features(self.rows,self.base,self.profiles)[self.eid][2]
        self.assertEqual(len(x),thermal.N_FEATURES)
        self.assertAlmostEqual(x[2],.5)
        self.assertAlmostEqual(x[3],.5)
        self.assertAlmostEqual(x[7],.1)
        self.assertAlmostEqual(x[8],-1/3)
        self.assertAlmostEqual(x[9],0.)
        self.assertAlmostEqual(x[10],.25)
        self.rows["binary_sensor.zone_c_pump"]["s"]="off"
        x=thermal.thermal_features(self.rows,self.base,self.profiles)[self.eid][2]
        self.assertEqual(x[2:4],[0.,0.])
    def test_missing_stale_not_zero(self):
        self.rows["sensor.test_attic"]["age"]=3700
        self.assertEqual(thermal.thermal_features(self.rows,self.base,self.profiles),{})
    def test_shadow_scoring_and_profile_reset(self):
        model=thermal.Shadow()
        for i in range(31):
            result=model.observe(1000+60*i,self.rows,self.base,self.profiles,
                                 {self.eid:{"prediction_30min":20.2}})
        self.assertEqual(result[self.eid]["samples"],1)
        self.assertEqual(result[self.eid]["paired_samples"],1)
        self.assertAlmostEqual(result[self.eid]["paired_legacy_mae"],.2)
        self.assertFalse(result[self.eid]["validated"])
        self.profiles[self.eid]["manual_emitters"]=2
        result=model.observe(2860,self.rows,self.base,self.profiles,{})
        self.assertEqual(result[self.eid]["samples"],0)
    def test_shadow_gap(self):
        model=thermal.Shadow()
        model.observe(1000,self.rows,self.base,self.profiles,{})
        result=model.observe(2800,self.rows,self.base,self.profiles,{})
        self.assertEqual(result[self.eid]["samples"],0)
    def test_shadow_reload(self):
        model=thermal.Shadow()
        model.observe(1000,self.rows,self.base,self.profiles,{})
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/"shadow.json"
            f.write_text(json.dumps({"schema":1,"rooms":model.rooms}))
            self.assertEqual(thermal.load_shadow(f),model.rooms)

class TestSolar(unittest.TestCase):
    def setUp(self):
        self.rows={"sun.sun":row("above_horizon",elevation=30.,azimuth=180.),
                   "weather.forecast_maison":row("partlycloudy",cloud_coverage=20.),
                   "cover.test":row("open",current_position=50.),
                   "sensor.chauffage_c_solaire_prevision_solar_6h":row(1.4,unit_of_measurement="kWh/m²")}
    def test_orientation(self):
        south={"azimuth":180.,"tilt":90.}
        north={"azimuth":0.,"tilt":90.}
        self.assertAlmostEqual(thermal.solar_proxy(self.rows,[south]),.8*3**.5/2)
        self.assertEqual(thermal.solar_proxy(self.rows,[north]),0.)
        self.assertAlmostEqual(thermal.solar_proxy(self.rows,[{"azimuth":0.,"tilt":0.}]),.4)
        self.assertEqual(thermal.solar_context(self.rows)["forecast_kwh_m2"],{"6":1.4})
    def test_cover_and_missing(self):
        s={"azimuth":180.,"tilt":90.,"cover":"cover.test"}
        self.assertAlmostEqual(thermal.solar_proxy(self.rows,[s]),.4*3**.5/2)
        self.rows["cover.test"]["age"]=3601
        self.assertIsNone(thermal.solar_proxy(self.rows,[s]))
        self.rows["sun.sun"]["a"]["elevation"]=-5
        self.assertEqual(thermal.solar_proxy(self.rows,[s]),0.)
    def test_unknown_cloud(self):
        self.rows["weather.forecast_maison"]["q"]="unavailable"
        self.assertIsNone(thermal.solar_proxy(self.rows,[{"azimuth":180.,"tilt":90.}]))
    def test_validation(self):
        for surf in ({"azimuth":-1,"tilt":90},{"azimuth":180,"tilt":91},{"azimuth":180,"tilt":90,"cover":"person.test"},{"azimuth":180,"tilt":90,"weight":0}):
            with self.assertRaises(ValueError):
                thermal.validate_profiles({"schema":1,"rooms":{"climate.test":{"solar_surfaces":[surf]}}})
        p=thermal.validate_profiles({"schema":1,"rooms":{"climate.test":{"solar_surfaces":[{"azimuth":180,"tilt":90}]}}})
        self.assertEqual(p["rooms"]["climate.test"]["solar_surfaces"][0]["weight"],1.)
    def test_profile_isolation(self):
        p={"area_m2":20.}
        enriched=dict(p,solar_surfaces=[{"azimuth":180.,"tilt":90.}])
        self.assertEqual(thermal.signature(p),thermal.signature({k:v for k,v in enriched.items() if k!="solar_surfaces"}))

if __name__=="__main__":
    unittest.main()
