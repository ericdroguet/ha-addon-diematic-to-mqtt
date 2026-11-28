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

if __name__=="__main__":
    unittest.main()
