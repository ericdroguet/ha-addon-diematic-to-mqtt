import unittest
from datetime import datetime, timezone
from solar_monitor import aggregate, decode, report, FIELDS

class SolarTests(unittest.TestCase):
    def test_hour(self):
        a = aggregate({3600: [500, 300, 200, 1800, 8, 12, 50]}, 0, 3600)
        self.assertEqual(a["global_kwh"], 0.5)
        self.assertEqual(a["sun_h"], 0.5)
        self.assertEqual(a["wind_kmh"], 12)
    def test_partial(self):
        a = aggregate({3600: [500, 300, 200, 1800, 8, 12, 50]}, 1800, 3600)
        self.assertEqual(a["global_kwh"], 0.25)
        self.assertEqual(a["sun_h"], 0.25)
    def test_gap(self):
        self.assertIsNone(aggregate({3600: [500, 300, 200, 1800, 8, 12, 50]}, 0, 7200))
    def test_zero_not_missing(self):
        self.assertEqual(aggregate({3600: [0,0,0,0,8,0,100]}, 0, 3600)["global_kwh"], 0)
    def test_decode(self):
        units = dict(zip(FIELDS, ("W/m²","W/m²","W/m²","s","°C","km/h","%")))
        h = dict(time=[3600,7200], **{k:[0,None] for k in FIELDS})
        self.assertEqual(list(decode(dict(hourly=h,hourly_units=units))), [3600])
    def test_palier(self):
        rows = {t: [500,300,200,1800,8,12,50] for t in range(3600,86401,3600)}
        fuel = [dict(debut="1970-01-01T00:00:00+00:00",fin="1970-01-02T00:00:00+00:00",date="02/01",allure="1",litres=46.4,l_j=46.4)]
        self.assertEqual(report(rows, 90000, fuel)["paliers"][0]["global_kwh_j"],12)
