import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from diagnostics import OUTPUTS, diagnostic_messages, raw_snapshot


class TestDiagnostics(unittest.TestCase):
    def test_each_bit_independently(self):
        for address, bit, _, _, topic, _ in OUTPUTS:
            with self.subTest(topic=topic):
                messages = diagnostic_messages({427: 0, 428: 0, address: 1 << bit}, True)
                for _, _, _, _, other, _ in OUTPUTS:
                    self.assertEqual(messages[other], '1' if other == topic else '0')
                self.assertEqual(messages['diagnostic/427/availability'], 'Online')
                self.assertEqual(messages['diagnostic/428/availability'], 'Online')

    def test_invalid_word_is_not_false_or_on(self):
        for value in (None, -1, 65535, 65536, '12', True):
            messages = diagnostic_messages({427: value, 428: 4}, True)
            self.assertEqual(messages['diagnostic/427/availability'], 'Offline')
            self.assertEqual(messages['diagnostic/burner2'], '')
            self.assertEqual(messages['diagnostic/valveCOpen'], '1')

    def test_missing_and_offline(self):
        for registers, online in (({}, True), ({427: 12, 428: 12}, False)):
            messages = diagnostic_messages(registers, online)
            for _, _, _, _, topic, _ in OUTPUTS:
                self.assertEqual(messages[topic], '')
            self.assertEqual(messages['diagnostic/427/availability'], 'Offline')
            self.assertEqual(messages['diagnostic/428/availability'], 'Offline')

    def test_both_valve_commands_not_hidden(self):
        messages = diagnostic_messages({427: 0, 428: 12}, True)
        self.assertEqual(messages['diagnostic/valveCOpen'], '1')
        self.assertEqual(messages['diagnostic/valveCClose'], '1')

    def test_raw_snapshot(self):
        registers = {428: 4, 427: 65535}
        self.assertEqual(raw_snapshot(registers, True, True), '{"427":65535,"428":4}')
        self.assertEqual(raw_snapshot(registers, False, True), '')
        self.assertEqual(raw_snapshot(registers, True, False), '')
        self.assertEqual(raw_snapshot({}, True, True), '')
        self.assertEqual(json.loads(raw_snapshot(registers, True, True)), {'427': 65535, '428': 4})


if __name__ == '__main__':
    unittest.main()
