"""Read-only thermal diagnostic regression tests (no Home Assistant required)."""
import pathlib
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from thermal_monitor import ThermalMonitor, DEPARTURE, RETURN, DIEMATIC


def record(value, t):
    return {"state": str(value), "last_reported": datetime.fromtimestamp(t, timezone.utc).isoformat()}


class ThermalMonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_700_000_000.0
        self.pubs = []
        self.states = {
            DEPARTURE: record(48, self.now),
            RETURN: record(42, self.now),
            DIEMATIC: record(47.5, self.now),
        }
        self.monitor = ThermalMonitor(lambda e: self.states.get(e),
                                      lambda *args: self.pubs.append(args), lambda: self.now)

    def test_signed_delta_and_diematic_discrepancy(self):
        self.monitor.cycle()
        self.assertEqual(self.monitor.current["delta_t"], 6)
        self.assertEqual(self.monitor.current["ecart_depart_diematic"], 0.5)
        self.assertEqual(self.monitor.status, "ok")
        self.assertTrue(any(x[0].endswith("/delta_t") and x[1] == "6.00" for x in self.pubs))

    def test_zero_is_real_when_both_sensors_valid(self):
        self.states[RETURN] = record(48, self.now)
        self.monitor.cycle()
        self.assertEqual(self.monitor.current["delta_t"], 0)
        self.assertEqual(self.monitor.status, "ok")

    def test_missing_or_unavailable_never_produces_false_zero(self):
        self.monitor.cycle()
        self.now += 61
        self.states[RETURN] = {
            "state": "unavailable",
            "last_reported": datetime.fromtimestamp(self.now, timezone.utc).isoformat(),
        }
        self.monitor.cycle()
        self.assertEqual(self.monitor.status, "sonde_indisponible")
        self.assertNotIn("delta_t", self.monitor.current)
        self.assertEqual(len(self.monitor.samples), 0)
        self.assertTrue(any(x[0].endswith("/availability") and x[1] == "offline" for x in self.pubs))

    def test_stale_rejected(self):
        self.states[RETURN] = record(42, self.now - 601)
        self.monitor.cycle()
        self.assertEqual(self.monitor.status, "mesure_perimee")

    def test_implausible_signed_delta_rejected(self):
        self.states[RETURN] = record(55, self.now)
        self.monitor.cycle()
        self.assertEqual(self.monitor.status, "delta_incoherent")
        self.assertEqual(self.monitor.current, {})

    def test_median_after_warmup_and_recovery(self):
        for _ in range(27):
            self.states[DEPARTURE] = record(48, self.now)
            self.states[RETURN] = record(42, self.now)
            self.states[DIEMATIC] = record(47.5, self.now)
            self.monitor.cycle()
            self.now += 30
        self.assertAlmostEqual(self.monitor.current["delta_t_5min"], 6.0)
        self.assertAlmostEqual(self.monitor.current["delta_t_15min"], 6.0)
        self.assertNotIn("delta_t_30min", self.monitor.current)
        self.states[RETURN] = None
        self.monitor.cycle()
        self.assertEqual(self.monitor.status, "sonde_indisponible")
        self.states[RETURN] = record(41, self.now)
        self.monitor.cycle()
        self.assertEqual(self.monitor.status, "ok")
        self.assertNotIn("delta_t_15min", self.monitor.current)

    def test_monitor_does_not_write_heating_setpoints(self):
        self.monitor.cycle()
        self.assertTrue(self.pubs)
        self.assertTrue(all("number/set_value" not in x[0] for x in self.pubs))


if __name__ == "__main__":
    unittest.main()
