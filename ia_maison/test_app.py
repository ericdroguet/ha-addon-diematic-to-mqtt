import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app

class TestObservation(unittest.TestCase):
    def test_private_attributes_and_nonfinite(self):
        rows=app.clean([{"entity_id":"person.test","state":"home","attributes":{"latitude":45,"longitude":5}},
                        {"entity_id":"sensor.test","state":"nan","attributes":{"unit_of_measurement":"W"}},
                        {"entity_id":"camera.test","state":"idle"}],1700000000)
        self.assertEqual(rows["person.test"]["a"],{})
        self.assertIsNone(rows["sensor.test"]["s"])
        self.assertNotIn("camera.test",rows)
        self.assertIsNone(app.number("inf"))
    def test_unavailable_outdoor(self):
        self.assertEqual(app.features({"sensor.ext_temp":{"q":"unavailable","s":0}}),{})
    def test_archive(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            with patch("app.shutil.disk_usage",return_value=type("Disk",(),{"free":10*1024**3})()):
                app.archive(p,{"ts":1},1700000000)
                app.archive(p,{"ts":2},1700000000)
            data=gzip.decompress(next(p.glob("*.gz")).read_bytes()).decode()
            self.assertEqual([json.loads(x)["ts"] for x in data.splitlines()],[1,2])
            app.atomic_json(p/"model.json",{"schema":1,"rooms":{}})
            self.assertEqual(app.load_model(p/"model.json"),{})
    def test_learning_and_gap(self):
        l=app.Learner()
        rows={"sensor.zone_c_heating_slope":{"s":1.8},"sensor.trial_valve_band":{"s":14}}
        with patch("app.features",return_value={"climate.test":(20,21,[1,0,0,0,0,0])}):
            for i in range(31):
                l.observe(1000+60*i,rows)
        self.assertEqual(l.rooms["climate.test"]["n"],1)
        with patch("app.features",return_value={"climate.test":(21,21,[1,0,0,0,0,0])}):
            l.observe(10000,rows)
        self.assertEqual(l.rooms["climate.test"]["n"],1)
    def test_corrupt_model(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"model.json"
            p.write_text("{")
            self.assertEqual(app.load_model(p),{})

if __name__=="__main__":
    unittest.main()
