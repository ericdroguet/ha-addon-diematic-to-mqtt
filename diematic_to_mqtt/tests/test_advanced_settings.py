import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from advanced_settings import AdvancedProbe, decode, validate_block, decode_experimental, EXPERIMENTAL_READ, RETIRED_SETTINGS

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

    def test_experimental_scaling(self):
        self.assertEqual(decode_experimental(266, 120), "12.0")
        self.assertEqual(decode_experimental(61, 0), "0")
        self.assertIsNone(decode_experimental(266, 121))
        self.assertIsNone(decode_experimental(269, 65535))
        self.assertIsNone(decode_experimental(272, 0))

    def test_experimental_failure_isolated(self):
        p = AdvancedProbe()
        p.enabled = True
        p.next_read = 1000
        p.values[678] = (750, 100)
        interface, logger = Mock(), Mock()
        interface.masterReadAnalog.side_effect = [None, {266: 120}]
        with patch("advanced_settings.time.time", return_value=100), patch("advanced_settings.time.monotonic", return_value=100):
            p.poll(interface, 10, {61: 2}, logger, 100)
            self.assertEqual(p.messages(True)["advanced/61"], "2")
            self.assertEqual(p.messages(True)["advanced/678"], "75.0")
            self.assertEqual(p.messages(True)["advanced/264/availability"], "Offline")
            self.assertFalse(p.stopped)
        with patch("advanced_settings.time.time", return_value=161), patch("advanced_settings.time.monotonic", return_value=161):
            p.poll(interface, 10, {61: 2}, logger, 161)
            self.assertEqual(p.messages(True)["advanced/266"], "12.0")
            self.assertEqual(p.messages(False)["advanced/266/availability"], "Offline")
            self.assertTrue(p.can_write_max())
        self.assertEqual(interface.masterReadAnalog.call_count, 2)
        interface.masterWriteAnalog.assert_not_called()

    def test_consolidated_core_and_retired(self):
        p = AdvancedProbe()
        p.enabled = True
        p.next_read = 1000
        p.experimental_next = 1000
        interface, logger = Mock(), Mock()
        with patch("advanced_settings.time.time", return_value=100), patch("advanced_settings.time.monotonic", return_value=100):
            p.poll(interface, 10, {426: 40, 435: 0, 438: 120}, logger, 100)
            msg = p.messages(True)
            self.assertEqual(msg["advanced/426"], "4.0")
            self.assertEqual(msg["advanced/435"], "0")
            self.assertEqual(msg["advanced/438"], "12.0")
            for address, _ in RETIRED_SETTINGS:
                self.assertNotIn(f"advanced/{address}", msg)
                self.assertNotIn(address, EXPERIMENTAL_READ)
            interface.masterReadAnalog.assert_not_called()
            interface.masterWriteAnalog.assert_not_called()
        self.assertEqual(set(EXPERIMENTAL_READ), {264, 266, 268, 269, 271, 272, 273})

if __name__ == "__main__":
    unittest.main()
