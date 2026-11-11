import importlib.util
import pathlib
import unittest
from unittest.mock import patch

PATH=pathlib.Path(__file__).resolve().parents[1] / "app.py"
# Les imports internes de l’add-on doivent être accessibles pendant les tests.
import sys
sys.path.insert(0, str(PATH.parent))
spec=importlib.util.spec_from_file_location("heating_controller", PATH)
app=importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name]=app
spec.loader.exec_module(app)

OPTS={"minimum_virtual":18.5,"maximum_virtual":23.0,
      "max_rise_per_write":1.0,"max_drop_per_write":0.5,
      "manual_day":21.0,"manual_night":19.0,
      "automatic_control":False,"dry_run":True,
      "write_interval_seconds":120,
      "weather_anticipation":False,"weather_max_boost":1.0}

def d(gap=0,count=0,valid=11):
    return app.Demand(valid=valid,count=count,max_gap=gap,
                      priority="Séjour" if count else "Aucune")

class DemandTests(unittest.TestCase):
    def test_no_demand_reduces_gradually(self):
        self.assertEqual(app.decide(d(),21.0,OPTS),20.5)
    def test_large_demand_rises_with_limit(self):
        self.assertEqual(app.decide(d(2.5,5),21.0,OPTS),22.0)
    def test_setpoint_bounded(self):
        self.assertLessEqual(app.decide(d(4.0,10),23.0,OPTS),23.0)
    def test_manual_override_above_max_still_clamps(self):
        self.assertEqual(app.decide(d(0.8,5),24.0,OPTS),23.0)
    def test_manual_override_below_min_still_clamps(self):
        self.assertEqual(app.decide(d(0.0,0),17.0,OPTS),18.5)
    def test_missing_temperature_ignored(self):
        item=d(valid=0)
        app.add_room(item,"Unavailable",None,21.5,True)
        self.assertEqual(item.valid,0)
    def test_heating_at_threshold_counts(self):
        item=d()
        app.add_room(item,"Salon",21.5,21.5,True)
        self.assertEqual(item.count,1)
    def test_eve_group_only_counts_once(self):
        item=d()
        app.add_room(item,"Chambre Eve",20.0,21.5,True)
        self.assertEqual(item.count,1)
    def test_simulation_does_not_write(self):
        fake={
           app.ZONE_MODE:{"state":"AUTO"},
           app.ZONE_DEPARTURE:{"state":"42.0"},
           app.OUTDOOR:{"state":"8.0"},
           app.ZONE_DAY:{"state":"21.0"},
           app.ZONE_NIGHT:{"state":"19.0"}
        }
        with patch.object(app,"collect",return_value=d(1.0,2)),\
             patch.object(app,"state",side_effect=lambda key:fake.get(key)),\
             patch.object(app,"call_number") as writer:
            c=app.Controller(OPTS)
            c.cycle()
            writer.assert_not_called()
    def test_invalid_data_does_not_write(self):
        fake={
           app.ZONE_MODE:{"state":"AUTO"},
           app.ZONE_DEPARTURE:{"state":"unavailable"},
           app.OUTDOOR:{"state":"8.0"},
           app.ZONE_DAY:{"state":"21.0"},
           app.ZONE_NIGHT:{"state":"19.0"}
        }
        active={**OPTS,"automatic_control":True,"dry_run":False}
        with patch.object(app,"collect",return_value=d(1.0,7)),\
             patch.object(app,"state",side_effect=lambda key:fake.get(key)),\
             patch.object(app,"call_number") as writer:
            app.Controller(active).cycle()
            writer.assert_not_called()

class PredictiveTests(unittest.TestCase):
    def test_preheat_without_active_demand(self):
        item=d()
        app.add_room(item,"Salon",21.7,21.5,False)
        self.assertEqual(item.count,0)
        self.assertEqual(item.near_count,1)
        self.assertEqual(app.weather_adjustment(-4.5, 8.0, item, 1.0)[1], 1.0)
        self.assertGreater(app.decide(item,21.0,OPTS,1.0),
                           app.decide(item,21.0,OPTS,0.0))
    def test_no_preheat_when_all_rooms_warm(self):
        item=d()
        app.add_room(item,"Salon",22.5,21.5,False)
        # La formule actuelle applique aussi la météo sans demande active.
        self.assertGreater(app.weather_adjustment(-5.0, 8.0, item, 1.0)[1], 0.0)
    def test_no_preheat_without_forecast(self):
        item=d()
        app.add_room(item,"Salon",21.5,21.5,False)
        self.assertEqual(app.weather_adjustment(None, 8.0, item, 1.0)[1], 0.0)

if __name__=="__main__":
    unittest.main()

class WarmingTests(unittest.TestCase):
    def test_warming_reduces_virtual_setpoint(self):
        item=d()
        app.add_room(item,"Salon",21.2,21.5,True)
        correction=app.weather_adjustment(4.5, 8.0, item, 1.0)[1]
        self.assertEqual(correction,-1.0)
        self.assertLess(app.decide(item,19.5,OPTS,correction),app.decide(item,19.5,OPTS,0.0))
    def test_no_reduction_when_room_is_cold(self):
        item=d()
        app.add_room(item,"Salon",20.0,21.5,True)
        item.actual_max_gap = 1.5
        # V0.3.4 protège progressivement le confort, sans annuler à 100 %.
        self.assertEqual(app.weather_adjustment(5.0, 8.0, item, 1.0)[1], -0.5)
    def test_small_change_ignored(self):
        item=d()
        app.add_room(item,"Salon",21.5,21.5,False)
        self.assertAlmostEqual(app.weather_adjustment(1.5, 8.0, item, 1.0)[1], -0.21)
