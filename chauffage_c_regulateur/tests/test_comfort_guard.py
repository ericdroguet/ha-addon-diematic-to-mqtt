"""Tests du mode comparatif V0.3.5 (aucun accès Home Assistant requis)."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from comfort_guard import (
    ComfortGuard, WeatherHysteresis, protected_weather,
)


class LocalComfortTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000.0

    def test_local_deficit_after_sixty_minutes(self):
        g = ComfortGuard(now=self.now)
        gaps = [("Salon TV", 0.9), ("Séjour", 0.1), ("Cuisine", 0.0)]
        self.assertEqual(g.observe(gaps, 12, 30, self.now).mode, "normal")
        result = g.observe(gaps, 12, 30, self.now + 3601)
        self.assertEqual(result.mode, "local")
        self.assertEqual(result.local_room, "Salon TV")
        self.assertEqual(result.cold_rooms, 1)
        self.assertEqual(protected_weather(-1.0, result), -1.0)

    def test_multiple_deficits_require_forty_five_minutes(self):
        g = ComfortGuard(now=self.now)
        gaps = [("Salon TV", 0.9), ("Séjour", 0.6)]
        self.assertEqual(g.observe(gaps, 12, 30, self.now).mode, "normal")
        self.assertEqual(g.observe(gaps, 12, 30, self.now + 2400).mode, "normal")
        a = g.observe(gaps, 12, 30, self.now + 2701)
        self.assertEqual(a.mode, "collective")
        self.assertEqual(protected_weather(-1.0, a), -1.0)
        halfway = g.observe(gaps, 12, 30, self.now + 3300)
        self.assertAlmostEqual(protected_weather(-1.0, halfway), -1 / 3, places=3)
        fully = g.observe(gaps, 12, 30, self.now + 3600)
        self.assertEqual(protected_weather(-1.0, fully), 0.0)
        self.assertEqual(protected_weather(0.6, fully), 0.6)

    def test_collective_mode_resets_when_only_one_room_is_cold(self):
        g = ComfortGuard(now=self.now)
        many = [("Salon TV", 0.9), ("Séjour", 0.6)]
        g.observe(many, 12, 30, self.now)
        self.assertEqual(g.observe(many, 12, 30, self.now + 3000).mode, "collective")
        single = g.observe([("Salon TV", 0.9), ("Séjour", 0.2)], 12, 30, self.now + 3700)
        self.assertEqual(single.mode, "local")
        self.assertIsNone(g.collective_since)

    def test_inadequate_sensor_coverage_cannot_raise_a_guard_alert(self):
        g = ComfortGuard(now=self.now)
        gaps = [("Salon TV", 1.1)]
        g.observe(gaps, 7, 30, self.now)
        self.assertEqual(g.observe(gaps, 7, 30, self.now + 5000).mode, "normal")

    def test_buckets_and_persistence_survive_restart(self):
        g = ComfortGuard(now=self.now)
        g.observe([("Salon TV", 0.9)], 12, 30, self.now)
        memory = g.snapshot()
        h = ComfortGuard(memory, now=self.now + 120)
        result = h.observe([("Salon TV", 0.9)], 12, 30, self.now + 120)
        self.assertGreater(result.total_minutes_24h, 0)
        self.assertAlmostEqual(result.local_minutes, 2.0)

    def test_missing_room_removes_local_persistence(self):
        g = ComfortGuard(now=self.now)
        g.observe([("Salon TV", 0.8)], 12, 30, self.now)
        a = g.observe([("Séjour", 0.1)], 12, 30, self.now + 4000)
        self.assertEqual(a.mode, "normal")
        self.assertNotIn("Salon TV", g.since)


class WeatherTests(unittest.TestCase):
    def test_decreases_require_fifteen_minutes(self):
        h = WeatherHysteresis()
        self.assertEqual(h.apply(-0.5, 0), -0.5)
        self.assertEqual(h.apply(-1.0, 1), -0.5)
        self.assertEqual(h.apply(-1.0, 900), -0.5)
        self.assertEqual(h.apply(-1.0, 901), -1.0)

    def test_less_negative_weather_is_immediate(self):
        h = WeatherHysteresis()
        h.apply(-1.0, 0)
        self.assertEqual(h.apply(-0.5, 1), -0.5)
        self.assertEqual(h.apply(0.5, 2), 0.5)

    def test_short_lived_weather_dip_is_ignored(self):
        h = WeatherHysteresis()
        h.apply(-0.5, 0)
        h.apply(-1.0, 1)
        self.assertEqual(h.apply(-0.5, 60), -0.5)
        self.assertEqual(h.apply(-1.0, 70), -0.5)
        self.assertEqual(h.apply(-1.0, 71), -0.5)

    def test_protected_weather_does_not_change_normal_or_local_modes(self):
        g = ComfortGuard(now=1800000000.0)
        assessment = g.observe([("Salon TV", 0.9)], 12, 30, 1800000000.0)
        self.assertEqual(protected_weather(-1.0, assessment), -1.0)


if __name__ == "__main__":
    unittest.main()
