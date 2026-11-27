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

class TestHumanKnowledge(unittest.TestCase):
    def test_annotations_and_events(self):
        current={"schema":1,"entities":{},"events":[]}
        catalog={"sensor.temp":{"quality":"unsupported_state"}}
        result=app.validate_knowledge({"kind":"entity","entity_id":"sensor.temp","role":"chauffage","note":"Consigne"},catalog,current,"2026-11-27")
        self.assertEqual(result["entities"]["sensor.temp"]["note"],"Consigne")
        self.assertEqual(current["entities"],{})
        result=app.validate_knowledge({"kind":"event","category":"rideaux","note":"Fermés"},catalog,result,"2026-11-27")
        self.assertEqual(len(result["events"]),1)
        event_id=result["events"][0]["id"]
        result=app.validate_knowledge({"kind":"remove_event","id":event_id},catalog,result,"2026-11-27")
        self.assertEqual(result["events"],[])
        result=app.validate_knowledge({"kind":"remove","entity_id":"sensor.temp"},catalog,result,"2026-11-27")
        self.assertEqual(result["entities"],{})
    def test_reject_invalid(self):
        current={"schema":1,"entities":{},"events":[]}
        with self.assertRaises(ValueError):
            app.validate_knowledge({"kind":"entity","entity_id":"person.test","role":"presence"}, {"person.test":{}},current,"now")
        with self.assertRaises(ValueError):
            app.validate_knowledge({"kind":"event","category":"rideaux","note":" "},{},current,"now")
        with self.assertRaises(ValueError):
            app.validate_knowledge({"kind":"entity","entity_id":"sensor.test","role":"bad"},{"sensor.test":{}},current,"now")
    def test_persistence_and_corruption(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"human_knowledge.json"
            self.assertEqual(app.load_knowledge(p),{"schema":1,"entities":{},"events":[]})
            app.atomic_json(p,{"schema":1,"entities":{"sensor.temp":{"role":"chauffage","note":"Consigne"}},"events":[]})
            self.assertEqual(app.load_knowledge(p)["entities"]["sensor.temp"]["role"],"chauffage")
            p.write_text("{")
            self.assertIsNone(app.load_knowledge(p))
            self.assertEqual(p.read_text(),"{")

class TestSuggestions(unittest.TestCase):
    def test_specific_and_ambiguous(self):
        self.assertEqual(app.suggest_role("binary_sensor.chambre_frost_protection",{"a":{}})["role"],"chauffage")
        self.assertEqual(app.suggest_role("cover.salon_tv",{"a":{}})["role"],"ouvrants")
        self.assertEqual(app.suggest_role("sensor.unknown_power",{"a":{"unit_of_measurement":"W"}})["role"],"energie")
        self.assertIsNone(app.suggest_role("sensor.trial_vario_306_raw",{"a":{}}))
    def test_quality_unchanged(self):
        cat=app.make_catalog({"cover.test":{"q":"unknown","s":None,"age":1,"a":{}}})
        self.assertEqual(cat["cover.test"]["quality"],"unknown")
        self.assertEqual(cat["cover.test"]["suggestion"]["role"],"ouvrants")

if __name__=="__main__":
    unittest.main()
