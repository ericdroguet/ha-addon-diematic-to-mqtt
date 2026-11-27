import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from advanced_settings import AdvancedProbe, decode, validate_block, decode_experimental

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

    def test_survey_shares_experimental_slot_and_preserves_raw_marker(self):
        p = AdvancedProbe()
        p.enabled = True
        p.next_read = 1000
        p.survey_ready = True
        p.survey_turn = True
        p.values[678] = (750, 100)
        interface, logger = Mock(), Mock()
        interface.masterReadAnalog.side_effect = [{265: 65535}, {264: 3}]
        with patch("advanced_settings.time.time", return_value=100), patch("advanced_settings.time.monotonic", return_value=100):
            p.poll(interface, 10, {73: 65535, 426: 40}, logger, 100)
            self.assertEqual(p.survey_pending[0], 274)
            self.assertTrue(p.survey_core_logged)
            self.assertEqual(p.messages(True)["advanced/678"], "75.0")
            self.assertFalse(p.stopped)
            logger.info.assert_any_call("Diematic survey register %s raw=%r (no mapping assumed)", 265, 65535)
            p.poll(interface, 10, {}, logger, 100)
            self.assertEqual(interface.masterReadAnalog.call_count, 1)
        with patch("advanced_settings.time.time", return_value=161), patch("advanced_settings.time.monotonic", return_value=161):
            p.poll(interface, 10, {}, logger, 161)
            self.assertEqual(p.messages(True)["advanced/264"], "3")
        self.assertEqual(interface.masterReadAnalog.call_count, 2)
        interface.masterWriteAnalog.assert_not_called()

if __name__ == "__main__":
    unittest.main()
