import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from advanced_settings import AdvancedProbe, decode, validate_block, decode_experimental, EXPERIMENTAL_READ, RETIRED_SETTINGS, SETTINGS, EXPERIMENTAL_SETTINGS, retry_delay

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

    def test_default_off_and_backoff(self):
        p = AdvancedProbe()
        interface = Mock()
        logger = Mock()
        with patch("advanced_settings.time.time", return_value=1), patch("advanced_settings.time.monotonic", return_value=1):
            p.poll(interface,10,{},logger,1)
            interface.masterReadAnalog.assert_not_called()
            p.enabled = True
            interface.masterReadAnalog.return_value = None
            p.poll(interface,10,{},logger,1)
            self.assertTrue(p.stopped)
            self.assertEqual(p.messages(True)["advanced/678/availability"],"Offline")
            p.poll(interface,10,{},logger,1)
            self.assertEqual(interface.masterReadAnalog.call_count,1)
            self.assertEqual(p.block_retry_at[619], 61)

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

    def test_band_write_and_readback(self):
        p = AdvancedProbe()
        p.enabled = True
        p.experimental_values[266] = (120, 100)
        interface, logger = Mock(), Mock()
        interface.masterReadAnalog.side_effect = [{266: 120}, {266: 140}]
        interface.masterWriteAnalog.return_value = True
        with patch("advanced_settings.time.time", return_value=100), patch("advanced_settings.time.monotonic", return_value=100):
            self.assertTrue(p.can_write_band())
            p.band_queued_at = 100
            self.assertTrue(p.write_band(interface, 10, [140], logger, 100))
            self.assertFalse(p.can_write_band())
            self.assertEqual(p.messages(True)["advanced/266/availability"], "Offline")
            p.verify_band(interface, 10, logger)
            self.assertEqual(p.messages(True)["advanced/266"], "14.0")
            self.assertTrue(p.can_write_band())
        interface.masterWriteAnalog.assert_called_once_with(10, 266, [140])

    def test_band_rejects_invalid_or_changed_preflight(self):
        p = AdvancedProbe()
        p.enabled = True
        p.experimental_values[266] = (120, 100)
        interface, logger = Mock(), Mock()
        with patch("advanced_settings.time.time", return_value=100), patch("advanced_settings.time.monotonic", return_value=100):
            p.band_queued_at = 100
            p.write_band(interface, 10, [170], logger, 100)
            interface.masterReadAnalog.assert_not_called()
            p.band_queued_at = 100
            interface.masterReadAnalog.return_value = {266: 130}
            p.write_band(interface, 10, [140], logger, 100)
            self.assertEqual(p.messages(True)["advanced/266"], "13.0")
        interface.masterWriteAnalog.assert_not_called()

    def test_band_failed_readback_blocks_writes(self):
        p = AdvancedProbe()
        p.enabled = True
        p.expected_band = 140
        interface, logger = Mock(), Mock()
        interface.masterReadAnalog.return_value = {266: 120}
        p.verify_band(interface, 10, logger)
        self.assertTrue(p.band_write_blocked)
        self.assertFalse(p.can_write_band())
        interface.masterWriteAnalog.assert_not_called()

    def test_failed_block_recovers_and_other_data_survives(self):
        p = AdvancedProbe()
        p.enabled = True
        p.experimental_next = 10000
        p.values[678] = (750, 100)
        interface, logger = Mock(), Mock()
        interface.masterReadAnalog.side_effect = [
            None,
            dict(zip(range(664,672), [215,190,60,8,3,18,200,750])),
            {677:450,678:750},
            {619:500,620:720},
        ]
        for t in (100,130,160,190):
            with patch("advanced_settings.time.time", return_value=t), patch("advanced_settings.time.monotonic", return_value=t):
                p.poll(interface,10,{35:215,36:190,44:500,61:4},logger,t)
                msg = p.messages(True)
                self.assertEqual(msg["advanced/678"], "75.0")
                self.assertEqual(msg["advanced/61"], "4")
                if t < 190:
                    self.assertEqual(msg["advanced/620/availability"], "Offline")
                    self.assertFalse(p.can_write_max())
        with patch("advanced_settings.time.monotonic", return_value=190):
            self.assertEqual(p.messages(True)["advanced/620"], "72.0")
            self.assertEqual(p.messages(True)["advanced/669"], "1.8")
            self.assertTrue(p.can_write_max())
        self.assertFalse(p.stopped)
        self.assertEqual(p.block_failures, {})
        interface.masterWriteAnalog.assert_not_called()

    def test_backoff_and_alias_mismatch(self):
        p = AdvancedProbe()
        p.enabled = True
        p.experimental_next = 10000
        interface, logger = Mock(), Mock()
        interface.masterReadAnalog.return_value = {619:500,620:720}
        for t, delay in ((100,60),(160,120),(280,240),(520,480),(1000,900),(1900,900)):
            p.index = 0
            with patch("advanced_settings.time.time", return_value=t), patch("advanced_settings.time.monotonic", return_value=t):
                p.poll(interface,10,{44:400},logger,t)
                self.assertEqual(p.block_retry_at[619], t+delay)
                self.assertEqual(p.messages(True)["advanced/620/availability"], "Offline")
        self.assertEqual(retry_delay(99),900)
        interface.masterWriteAnalog.assert_not_called()

    def test_experimental_retry_recovers(self):
        p = AdvancedProbe()
        p.enabled = True
        p.next_read = 10000
        interface, logger = Mock(), Mock()
        interface.masterReadAnalog.side_effect = [None,{264:3}]
        for t in (100,160):
            p.experimental_index = 0
            with patch("advanced_settings.time.time", return_value=t), patch("advanced_settings.time.monotonic", return_value=t):
                p.poll(interface,10,{},logger,t)
                if t == 100:
                    self.assertIn(264,p.experimental_failed)
                else:
                    self.assertNotIn(264,p.experimental_failed)
                    self.assertEqual(p.messages(True)["advanced/264"], "3")
        interface.masterWriteAnalog.assert_not_called()

    def test_expired_confirmation_does_not_stop_reads(self):
        p = AdvancedProbe()
        p.enabled = True
        p.expected_band = 140
        p.band_deadline = 100
        p.expected_max = 750
        p.max_deadline = 100
        interface, logger = Mock(), Mock()
        interface.masterReadAnalog.return_value = {619:500,620:720}
        with patch("advanced_settings.time.time", return_value=101), patch("advanced_settings.time.monotonic", return_value=101):
            p.poll(interface,10,{44:500},logger,101)
            self.assertEqual(p.messages(True)["advanced/620"], "72.0")
            self.assertTrue(p.band_write_blocked)
            self.assertTrue(p.max_write_blocked)
            self.assertFalse(p.can_write_band())
            self.assertFalse(p.can_write_max())
        interface.masterWriteAnalog.assert_not_called()

    def test_maximum_mismatch_blocks_writes(self):
        p = AdvancedProbe()
        p.enabled = True
        interface, logger = Mock(), Mock()
        interface.masterReadAnalog.return_value = {677:450,678:800}
        with patch("advanced_settings.time.time", return_value=100), patch("advanced_settings.time.monotonic", return_value=100):
            p.verify_max(750)
            p.poll(interface,10,{},logger,100)
            self.assertEqual(p.messages(True)["advanced/678"], "80.0")
            self.assertTrue(p.max_write_blocked)
            self.assertFalse(p.can_write_max())
        interface.masterWriteAnalog.assert_not_called()

    def test_legacy_sensor_ids_and_topics(self):
        expected = {620:"boiler_calculated_temp",668:"zone_c_room_influence",669:"zone_c_heating_slope",670:"zone_c_min_departure",671:"zone_c_max_departure",677:"boiler_min_temp",678:"boiler_max_temp",11:"trial_pump_heating_delay",60:"trial_ecs_priority",61:"trial_ecs_pump_delay",72:"trial_differential_a",264:"trial_building_inertia",266:"trial_valve_band",268:"trial_antilegionella",269:"trial_burner_min_run",271:"trial_burner_delay",272:"trial_boiler_pump_delay",273:"trial_boiler_unload",426:"boiler_valve_offset_readback",435:"diematic_register_435_raw",438:"trial_boiler_band"}
        self.assertEqual({s[0]:s[1] for s in SETTINGS+EXPERIMENTAL_SETTINGS},expected)
        self.assertEqual(set(AdvancedProbe().messages(True)),{topic for a in expected for topic in (f"advanced/{a}",f"advanced/{a}/availability")})

if __name__ == "__main__":
    unittest.main()
