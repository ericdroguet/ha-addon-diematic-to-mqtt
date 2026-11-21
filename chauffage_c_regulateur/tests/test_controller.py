"""Tests for the active Circuit C controller contracts (v0.4.0)."""
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


OPTS = {
    "minimum_virtual": 18.5,
    "maximum_virtual": 23.0,
    "max_rise_per_write": 1.0,
    "max_drop_per_write": 0.5,
    "manual_day": 21.0,
    "manual_night": 19.0,
    "automatic_control": False,
    "dry_run": True,
    "write_interval_seconds": 120,
    "weather_anticipation": False,
    "weather_max_boost": 1.0,
}


def demand(gap=0.0, count=0, valid=11):
    result = app.Demand(
        valid=valid,
        count=count,
        max_gap=max(0.0, gap),
        actual_max_gap=gap,
        priority="Salon TV" if count else "Aucune",
    )
    if count:
        result.gaps.append(("Salon TV", max(0.0, gap)))
    return result


class DemandTests(unittest.TestCase):
    def test_no_demand_reduces_gradually(self):
        self.assertEqual(app.decide(demand(), 21.0, OPTS), 20.5)

    def test_large_demand_rises_with_limit(self):
        self.assertEqual(app.decide(demand(2.5, 5), 21.0, OPTS), 22.0)

    def test_setpoint_bounded(self):
        self.assertLessEqual(app.decide(demand(4.0, 10), 23.0, OPTS), 23.0)

    def test_manual_override_above_max_still_clamps(self):
        self.assertEqual(app.decide(demand(0.8, 5), 24.0, OPTS), 23.0)

    def test_manual_override_below_min_still_clamps(self):
        self.assertEqual(app.decide(demand(), 17.0, OPTS), 18.5)

    def test_missing_temperature_ignored(self):
        item = demand(valid=0)
        app.add_room(item, "Unavailable", None, 21.5, True)
        self.assertEqual(item.valid, 0)

    def test_heating_at_threshold_counts(self):
        item = demand()
        app.add_room(item, "Salon", 21.5, 21.5, True)
        self.assertEqual(item.count, 1)

    def test_eve_group_counts_once(self):
        item = demand()
        app.add_room(item, "Chambre Eve", 20.0, 21.5, True)
        self.assertEqual(item.count, 1)

    def test_simulation_does_not_write(self):
        fake = {
            app.ZONE_MODE: {"state": "AUTO"},
            app.ZONE_DEPARTURE: {"state": "42.0"},
            app.OUTDOOR: {"state": "8.0"},
            app.ZONE_DAY: {"state": "21.0"},
            app.ZONE_NIGHT: {"state": "19.0"},
        }
        with patch.object(app, "collect", return_value=demand(1.0, 2)), \
             patch.object(app, "state", side_effect=lambda key: fake.get(key)), \
             patch.object(app, "call_number") as writer, \
             patch.object(app, "mqtt_publish"):
            app.Controller(OPTS).cycle()
            writer.assert_not_called()

    def test_invalid_data_does_not_write(self):
        fake = {
            app.ZONE_MODE: {"state": "AUTO"},
            app.ZONE_DEPARTURE: {"state": "unavailable"},
            app.OUTDOOR: {"state": "8.0"},
            app.ZONE_DAY: {"state": "21.0"},
            app.ZONE_NIGHT: {"state": "19.0"},
        }
        active = {**OPTS, "automatic_control": True, "dry_run": False}
        with patch.object(app, "collect", return_value=demand(1.0, 7)), \
             patch.object(app, "state", side_effect=lambda key: fake.get(key)), \
             patch.object(app, "call_number") as writer, \
             patch.object(app, "mqtt_publish"):
            app.Controller(active).cycle()
            writer.assert_not_called()


class PredictiveTests(unittest.TestCase):
    def test_cooling_raises_anticipation(self):
        item = demand()
        calculated, retained, protection = app.weather_adjustment(-4.5, 10.0, item, 1.0)
        self.assertEqual(calculated, 1.0)
        self.assertEqual(retained, 1.0)
        self.assertEqual(protection, 1.0)

    def test_warming_reduces_anticipation(self):
        item = demand()
        calculated, retained, _ = app.weather_adjustment(4.5, 10.0, item, 1.0)
        self.assertEqual(calculated, -1.0)
        self.assertEqual(retained, -1.0)

    def test_warming_with_deficit_protects_comfort(self):
        item = demand(1.5, 1)
        calculated, retained, factor = app.weather_adjustment(4.5, 10.0, item, 1.0)
        self.assertEqual(calculated, -1.0)
        self.assertGreater(retained, calculated)
        self.assertGreaterEqual(factor, 0.25)

    def test_no_forecast_returns_no_correction(self):
        self.assertEqual(app.weather_adjustment(None, 10.0, demand(), 1.0), (None, 0.0, 1.0))

    def test_too_few_valid_zones_disables_weather(self):
        self.assertEqual(app.weather_adjustment(-4.5, 10.0, demand(valid=4), 1.0),
                         (None, 0.0, 1.0))

    def test_anticipation_changes_virtual_setpoint(self):
        item = demand(0.3, 1)
        with_weather = app.decide(item, 20.0, OPTS, anticipation=1.0)
        without_weather = app.decide(item, 20.0, OPTS, anticipation=0.0)
        self.assertGreaterEqual(with_weather, without_weather)


if __name__ == "__main__":
    unittest.main()
