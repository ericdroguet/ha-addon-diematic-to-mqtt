import unittest
from dataclasses import asdict
from policy import Cycle, Settings, evaluate, restore

class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.c = Cycle()
        self.s = Settings()
    def run_at(self, now=100, temp=47, color="blanc", period="hp", pump="off", allowed=True):
        return evaluate(self.c, self.s, now=now, temp=temp, color=color, period=period,
                        pump=pump, allowed=allowed)
    def arm(self):
        self.run_at()
        self.assertTrue(self.run_at(now=200, temp=50).electric)
    def test_tariff(self):
        for color in ("bleu", "blanc", "rouge"):
            for period in ("hp", "hc"):
                self.c = Cycle()
                p = self.run_at(color=color, period=period)
                self.assertEqual(p.fuel_target, 55 if color == "rouge" and period == "hp" else 50)
                self.assertEqual(self.run_at(now=200, temp=50, color=color, period=period).electric,
                                 not (color == "rouge" and period == "hp"))
    def test_max_duration_and_no_restart(self):
        self.arm()
        self.assertTrue(self.run_at(now=7399, temp=52).electric)
        self.assertFalse(self.run_at(now=7400, temp=52).electric)
        self.assertFalse(self.run_at(now=7500, temp=52).electric)
        self.run_at(now=7600, temp=47)
        self.assertTrue(self.run_at(now=7700, temp=50).electric)
    def test_target(self):
        self.arm()
        self.assertFalse(self.run_at(now=300, temp=55).electric)
        self.assertFalse(self.run_at(now=400, temp=54).electric)
    def test_tariff_interrupt(self):
        self.arm()
        self.assertFalse(self.run_at(now=300, temp=52, color="rouge").electric)
        self.assertFalse(self.run_at(now=400, temp=52, color="rouge", period="hc").electric)
    def test_pump_and_invalid(self):
        self.run_at()
        self.assertFalse(self.run_at(now=200, temp=50, pump="on").electric)
        self.assertTrue(self.run_at(now=300, temp=50).electric)
        self.assertFalse(self.run_at(now=400, temp=None).electric)
        self.assertFalse(self.run_at(now=500, temp=52).electric)
    def test_restart(self):
        self.arm()
        self.c = restore(asdict(self.c), 400, self.s)
        self.assertEqual(self.run_at(now=400, temp=52).remaining_seconds, 7000)
        self.c = restore(asdict(self.c), 7401, self.s)
        self.assertFalse(self.run_at(now=7401, temp=52).electric)
    def test_start_mid_cycle(self):
        self.assertFalse(self.run_at(temp=52).electric)
    def test_permission(self):
        self.arm()
        self.assertFalse(self.run_at(now=300, temp=52, allowed=False).electric)
    def test_clock_reverse(self):
        self.arm()
        self.c = restore(asdict(self.c), 100, self.s)
        self.assertFalse(self.run_at(now=100, temp=52).electric)

if __name__ == "__main__":
    unittest.main()
