import unittest
from thermal_model import HydraulicModel


def rooms(gap=0.0, count=1, target=21.5):
    return [(str(i), target - (gap if i < count else 0), target) for i in range(12)]


def build(gap=0.0, count=1, delta=2.5, outdoor=9.5, missing=(), trend=0):
    m = HydraulicModel()
    for t in range(0, 3601, 60):
        if t not in missing:
            d = delta + trend * t / 3600
            m.observe(t, (t, 'ok', 48, 48-d, {}), rooms(gap, count), outdoor)
    return m


class TestModel(unittest.TestCase):
    def test_reduction(self):
        self.assertEqual(build().evaluate(3600, rooms(), 0, 0)[0], -0.5)
    def test_cold_forecast(self):
        self.assertEqual(build().evaluate(3600, rooms(), -1, 0)[0], 0)
    def test_no_forecast(self):
        self.assertIsNone(build().evaluate(3600, rooms(), None, 0)[0])
    def test_cold_room(self):
        self.assertEqual(build().evaluate(3600, rooms(.6), 0, 0)[0], 0)
    def test_boost(self):
        self.assertEqual(build(.8, 3, 5).evaluate(3600, rooms(.8,3), -1, .5)[0], .5)
    def test_no_boost_without_persistence(self):
        self.assertEqual(build(.8, 3, 5).evaluate(3600, rooms(.8,3), -1, .1)[0], 0)
    def test_neutral(self):
        self.assertEqual(build(delta=3.2).evaluate(3600, rooms(), 0, 0)[0], 0)
    def test_short_outage(self):
        self.assertEqual(build(missing=(1800,)).evaluate(3600, rooms(), 0, 0)[0], -.5)
    def test_long_outage(self):
        self.assertIsNone(build(missing=tuple(range(1200,2401,60))).evaluate(3600, rooms(), 0, 0)[0])
    def test_outside_domain(self):
        self.assertIsNone(build(outdoor=3).evaluate(3600, rooms(), 0, 0)[0])
    def test_missing_zone(self):
        self.assertIsNone(build().evaluate(3600, rooms()[:-1], 0, 0)[0])
    def test_target_change(self):
        self.assertIsNone(build().evaluate(3600, rooms(target=22), 0, 0)[0])
    def test_stale(self):
        self.assertIsNone(build().evaluate(3900, rooms(), 0, 0)[0])
    def test_repeated_snapshot(self):
        m=build(); n=len(m.samples); m.observe(3630,(3600,'ok',48,45,{}),rooms(),9.5)
        self.assertEqual(len(m.samples),n)
    def test_reset(self):
        m=build();m.reset();self.assertIsNone(m.evaluate(3600,rooms(),0,0)[0])
    def test_worsening_room(self):
        m=HydraulicModel()
        for t in range(0,3601,60):
            m.observe(t,(t,'ok',48,45.5,{}),rooms(.4*t/3600),9.5)
        self.assertEqual(m.evaluate(3600,rooms(.4),0,0)[0],0)
    def test_downward_trend(self):
        self.assertEqual(build(delta=3.7,trend=-1).evaluate(3600,rooms(),0,0)[0],-.5)


class TestSchedule(unittest.TestCase):
    def test_house_modes(self):
        from unittest.mock import patch
        import app
        for mode, house, enabled, expected in (
            ("schedule", "at_home", True, True),
            ("schedule", "asleep", True, True),
            ("schedule", "away", True, False),
            ("schedule", "vacation", True, False),
            ("schedule", "pause", True, False),
            ("schedule", "manual", True, False),
            ("schedule", "boost", True, False),
            ("schedule", "unavailable", True, False),
            ("schedule", None, True, False),
            ("vacation", "asleep", True, False),
            ("at_home", "asleep", True, False),
            ("pause", "asleep", True, False),
            ("schedule", "asleep", False, False),
        ):
            with self.subTest(mode=mode, house=house, enabled=enabled):
                records = {app.HOUSE_MODE: {"state": mode},
                           app.HOUSE_STATE: {"state": house} if house is not None else None}
                with patch.object(app, "state", side_effect=records.get):
                    self.assertEqual(app.collect({"schedule_anticipation": enabled}).schedule_enabled, expected)

    def test_preheat_living(self):
        from datetime import datetime
        import app
        attrs = {"friendly_name": "Living", "at_home_setpoint": 21.5,
                 "away_setpoint": 19, "schedule": {"thursday": ["06:00-22:00"]}}
        opts = {"schedule_preheat_minutes": 120, "schedule_setback_minutes": 45}
        result = app.Demand()
        target, fade = app.adjust_for_schedule(attrs, 19, 19,
                    datetime(2026, 11, 26, 5, 0, tzinfo=app.LOCAL_TZ), opts, result)
        self.assertAlmostEqual(target, 20.25)
        self.assertFalse(fade)
        self.assertEqual(result.preheat_rooms, ["Living"])


if __name__ == '__main__':
    unittest.main()
