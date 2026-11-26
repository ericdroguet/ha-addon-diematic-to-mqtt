"""Diematic 3 read-only diagnostic outputs (Sofrel S500, table D1)."""
import json

# (register, bit, object_id, label, state_topic, device_class)
OUTPUTS = (
    (427, 3, 'burner_stage1_command', 'Brûleur allure 1 - commande', 'diagnostic/burner1', None),
    (427, 2, 'burner_stage2_command', 'Brûleur allure 2 - commande', 'diagnostic/burner2', None),
    (427, 6, 'burner_alarm_contact', 'Alarme brûleur - contact', 'diagnostic/burnerAlarm', 'problem'),
    (427, 0, 'auxiliary_pump_command', 'Pompe auxiliaire - commande', 'diagnostic/auxPump', None),
    (428, 2, 'zone_c_valve_open_command', 'Vanne C - commande ouverture', 'diagnostic/valveCOpen', None),
    (428, 3, 'zone_c_valve_close_command', 'Vanne C - commande fermeture', 'diagnostic/valveCClose', None),
)


def read_word(registers, address, online):
    value = registers.get(address)
    return value if online and type(value) is int and 0 <= value < 0xFFFF else None


def diagnostic_messages(registers, online):
    words = {address: read_word(registers, address, online) for address in (427, 428)}
    messages = {}
    for address, bit, _, _, topic, _ in OUTPUTS:
        word = words[address]
        messages[topic] = '' if word is None else str((word >> bit) & 1)
    for address, word in words.items():
        messages[f'diagnostic/{address}/availability'] = 'Online' if word is not None else 'Offline'
    return messages


def raw_snapshot(registers, online, enabled):
    if not online or not enabled or not registers:
        return ''
    return json.dumps({str(k): v for k, v in sorted(registers.items())}, separators=(',', ':'))
