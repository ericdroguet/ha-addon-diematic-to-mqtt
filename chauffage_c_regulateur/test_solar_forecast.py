import unittest
from solar_forecast import archive_forecast, verify, simulate, sum_south, build_report

class ForecastTests(unittest.TestCase):
    def test_shadow_safety(self):
        self.assertEqual(simulate(2,[.4],12)[0],0)
        self.assertEqual(simulate(2,[.2],6)[0],0)
        self.assertEqual(simulate(2,None,12)[0],None)
        self.assertEqual(simulate(1.2,[.1],12)[0],-.5)
        self.assertEqual(simulate(.6,[.1],12)[0],-.25)
        self.assertEqual(simulate(0,[0],12)[0],0)
    def test_archive_never_replace(self):
        rows={t:[100,50,50,0,8,4,70] for t in range(3600,80*3600,3600)}
        a=archive_forecast({},rows,100)
        self.assertEqual(len(a),4)
        self.assertEqual(a["1:7200"]["ghi"],100)
        rows[7200][0]=900
        b=archive_forecast(a,rows,200)
        self.assertEqual(b["1:7200"]["ghi"],100)
    def test_verification(self):
        a={"x":dict(lead=1,target=7200,issued=0,ghi=100,temp=8)}
        h={"7200":[80,40,40,0,7,4,70]}
        self.assertEqual(verify(a,h,8000),[])
        v=verify(a,h,100000)[0]
        self.assertEqual(v["mae"],20)
        self.assertEqual(v["temp_mae"],1)
    def test_south_partial(self):
        self.assertEqual(sum_south({3600:500},1800,3600),.25)
        self.assertIsNone(sum_south({3600:500},0,7200))
    def test_full_forecast(self):
        rows={t:[100,50,50,0,8,4,70] for t in range(3600,80*3600,3600)}
        south={t:200 for t in rows}
        r=build_report(rows,south,100)
        self.assertEqual(len(r["hourly"]),72)
        self.assertEqual(r["latest"]["solar_72h"],7.2)
        self.assertEqual(r["latest"]["south_6h"],1.2)
