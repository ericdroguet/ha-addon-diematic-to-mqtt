import unittest
from datetime import datetime, timezone
from salon_tv_shadow import Shadow

def stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()

def records(t, base=21.5, internal=20.5, external=23.5, eve_time=None, mode="auto"):
    dan = {"state": mode, "last_reported": stamp(t), "attributes": {
        "temperature": base, "current_temperature": internal,
        "at_home_setpoint": 21.5, "away_setpoint": 19,
        "min_temp": 6, "max_temp": 28, "room_state": "Normal",
        "setpoint_type": "Adjustable"}}
    eve = {"state": str(external), "last_reported": stamp(t if eve_time is None else eve_time)}
    return dan, eve

class ShadowTest(unittest.TestCase):
    def advance(self, shadow, start, end, **kwargs):
        results = []
        for t in range(start, end+1, 60):
            results.append(shadow.evaluate(t, *records(t, **kwargs)))
        return results

    def test_limited_and_slow(self):
        s = Shadow()
        results = self.advance(s, 0, 3600)
        self.assertEqual([i*60 for i,r in enumerate(results) if r["candidate"]], [900,1800,2700,3600])
        self.assertEqual(results[-1]["simulated"], 19.5)
        self.assertEqual(results[-1]["base"], 21.5)
        self.assertEqual(results[-1]["proposed"], 19.5)

    def test_positive_correction(self):
        s = Shadow()
        r = self.advance(s, 0, 900, internal=23, external=20)[-1]
        self.assertEqual(r["simulated"],22)
        self.assertEqual(r["proposed"],23.5)

    def test_stale_even_with_fresh_cov(self):
        s = Shadow()
        self.advance(s, 0, 900)
        t = 2160
        r = s.evaluate(t, *records(t, eve_time=900), {"last_reported":stamp(t)})
        self.assertEqual(r["reason"], "température Eve périmée")
        self.assertEqual(r["simulated"],21.5)
        self.assertFalse(r["candidate"])

    def test_manual_and_schedule(self):
        s = Shadow()
        self.advance(s, 0, 900)
        r = s.evaluate(960, *records(960,base=22))
        self.assertEqual(r["reason"],"consigne manuelle ou dérogation")
        self.assertEqual(r["simulated"],22)
        r = s.evaluate(1020,*records(1020,base=19))
        self.assertEqual(r["simulated"],19)
        self.assertFalse(r["candidate"])

    def test_unavailable_and_restart(self):
        s = Shadow()
        self.advance(s,0,900)
        d,e=records(960)
        e["state"]="unavailable"
        r=s.evaluate(960,d,e)
        self.assertEqual(r["simulated"],21.5)
        self.assertFalse(r["candidate"])
        self.assertEqual(Shadow().evaluate(1000,*records(1000))["simulated"],21.5)

    def test_gap_resets(self):
        s=Shadow()
        self.advance(s,0,900)
        r=s.evaluate(1200,*records(1200))
        self.assertEqual(r["simulated"],21.5)
        self.assertFalse(r["candidate"])

if __name__ == "__main__":
    unittest.main()
