import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from advanced_settings import AdvancedProbe, decode, validate_block

class TestAdvancedSettings(unittest.TestCase):
    def test_scaling_and_bounds(self):
        self.assertEqual(decode(678, 800), "80.0")
        self.assertEqual(decode(669, 15), "1.5")
        self.assertEqual(decode(668, 3), "3")
        for v in (65535, -1, 0, None, 951, True):
            self.assertIsNone(decode(678, v))

    def test_alias_check(self):
        data = dict(zip(range(664,672), [215,190,60,8,3,15,200,750]))
        self.assertEqual(validate_block(664,8,data,{35:215,36:190})[669],15)
        self.assertIsNone(validate_block(664,8,data,{35:220,36:190}))
        self.assertIsNone(validate_block(619,2,{619:500,620:800},{44:430}))

    def test_default_off_and_fail_stop(self):
        p = AdvancedProbe()
        interface = Mock()
        logger = Mock()
        with patch("advanced_settings.time.time", return_value=1):
            p.poll(interface,10,{},logger,1)
            interface.masterReadAnalog.assert_not_called()
            p.enabled = True
            interface.masterReadAnalog.return_value = None
            p.poll(interface,10,{},logger,1)
            self.assertTrue(p.stopped)
            self.assertEqual(p.messages(True)["advanced/678/availability"],"Offline")
            p.poll(interface,10,{},logger,1)
            self.assertEqual(interface.masterReadAnalog.call_count,1)

if __name__ == "__main__":
    unittest.main()
