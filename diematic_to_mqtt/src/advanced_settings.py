"""Optional Sofrel S500 p.4 probe for Diematic 3.
Only register 678 can be written by the panel; published values come from reads.
Reject invalid words and stop additional reads on a failed probe.
"""
import time

# address, entity id, label, raw min, raw max, divisor, unit
SETTINGS = (
    (620, "boiler_calculated_temp", "Consigne calculée chaudière (Modbus 620)", 0, 1500, 10, "°C"),
    (668, "zone_c_room_influence", "Influence ambiance C (Modbus 668)", 0, 10, 1, None),
    (669, "zone_c_heating_slope", "Pente chauffage C (Modbus 669)", 0, 40, 10, None),
    (670, "zone_c_min_departure", "Minimum départ C (Modbus 670)", 100, 300, 10, "°C"),
    (671, "zone_c_max_departure", "Maximum départ C (Modbus 671)", 500, 950, 10, "°C"),
    (677, "boiler_min_temp", "Minimum chaudière (Modbus 677)", 300, 500, 10, "°C"),
    (678, "boiler_max_temp", "Maximum chaudière (Modbus 678)", 500, 950, 10, "°C"),
)
# Check known aliases from the already-working low-register map before
# accepting the new high-register values. Calculated C may change between reads.
BLOCKS = ((619, 2), (664, 8), (677, 2))
SPECS = {item[0]: item for item in SETTINGS}

def decode(address, word):
    _, _, _, lo, hi, divisor, _ = SPECS[address]
    if type(word) is not int or not lo <= word <= hi:
        return None
    return str(word) if divisor == 1 else f"{word / divisor:.1f}"

def validate_block(start, count, reply, core):
    if not isinstance(reply, dict):
        return None
    if any(type(reply.get(a)) is not int or not 0 <= reply[a] < 65535
           for a in range(start, start + count)):
        return None
    if start == 619 and (44 not in core or abs(reply[619] - core[44]) > 30):
        return None
    if start == 664 and any(reply[a] != core.get(b) for a,b in ((664,35),(665,36))):
        return None
    return {a: reply[a] for a in range(start,start+count) if a in SPECS}

# Diematic 3 CTRL 1422: panel values cross-checked on 2026-11-27.
# Keep legacy MQTT IDs to preserve consumers and history. No new write targets.
EXPERIMENTAL_SETTINGS = (
    (11, "trial_pump_heating_delay", "Tempo pompe chauffage (11)", 0, 15, 1, "min"),
    (60, "trial_ecs_priority", "Priorité ECS (60, code)", 0, 2, 1, None),
    (61, "trial_ecs_pump_delay", "Tempo pompe ECS (61)", 0, 15, 1, "min"),
    (72, "trial_differential_a", "Registre 72 - différentiel candidat (non validé)", 40, 100, 10, "K"),
    (264, "trial_building_inertia", "Inertie bâtiment (264)", 0, 10, 1, None),
    (266, "trial_valve_band", "Bande V3V (266)", 40, 160, 10, "K"),
    (268, "trial_antilegionella", "Antilégionelles (268, code)", 0, 1, 1, None),
    (269, "trial_burner_min_run", "Marche mini brûleur (269)", 0, 4, 1, "min"),
    (271, "trial_burner_delay", "Tempo brûleur (271)", 0, 10, 1, "min"),
    (272, "trial_boiler_pump_delay", "Tempo pompe chaudière (272)", 1, 30, 1, "min"),
    (273, "trial_boiler_unload", "Délestage chaudière (273, code)", 0, 1, 1, None),
    (426, "boiler_valve_offset_readback", "Décalage chaudière V3V (426, lecture)", 0, 160, 10, "K"),
    (435, "diematic_register_435_raw", "Registre 435 brut (défauts candidats)", 0, 65534, 1, None),
    (438, "trial_boiler_band", "Registre 438 (échelle /10, fonction non validée)", 100, 300, 10, "K"),
)
RETIRED_SETTINGS = (
    (73, "trial_differential_b"),
    (267, "trial_boiler_valve_offset"),
    (306, "trial_vario_306_raw"),
)
EXPERIMENTAL_CORE = (11, 60, 61, 72, 426, 435, 438)
EXPERIMENTAL_READ = tuple(s[0] for s in EXPERIMENTAL_SETTINGS if s[0] not in EXPERIMENTAL_CORE)
EXPERIMENTAL_SPECS = {s[0]: s for s in EXPERIMENTAL_SETTINGS}

def decode_experimental(address, word):
    _, _, _, lo, hi, divisor, _ = EXPERIMENTAL_SPECS[address]
    if type(word) is not int or not lo <= word <= hi:
        return None
    if address in (72, 266, 438) and word % 10:
        return None
    return str(word) if divisor == 1 else f"{word / divisor:.1f}"

class AdvancedProbe:
    def __init__(self):
        self.enabled = False
        self.stopped = False
        self.index = 0
        self.next_read = 0.0
        self.values = {}
        self.expected_max = None
        self.experimental_values = {}
        self.experimental_index = 0
        self.experimental_next = 0.0
        self.experimental_failed = set()

    def can_write_max(self):
        item = self.values.get(678)
        return (self.enabled and not self.stopped and self.expected_max is None
                and item is not None and time.monotonic() - item[1] < 150
                and decode(678, item[0]) is not None)

    def verify_max(self, expected):
        self.expected_max = expected
        self.values.pop(678, None)
        self.index = BLOCKS.index((677, 2))
        self.next_read = 0.0

    def poll(self, interface, regulator, core, logger, master_time):
        if not self.enabled or self.stopped:
            return
        # Reuse words already obtained by the successful core refresh.
        stamp = time.monotonic()
        for address in EXPERIMENTAL_CORE:
            self.experimental_values[address] = (core.get(address), stamp)
        if stamp < self.next_read:
            self.poll_experimental(interface, regulator, logger, master_time)
            return
        # Keep the original poll and writes first; skip busy master windows.
        if time.time() - master_time > 1.0:
            return
        start, count = BLOCKS[self.index]
        self.next_read = time.monotonic() + 30
        try:
            reply = interface.masterReadAnalog(regulator, start, count, timeout=0.75)
            result = validate_block(start, count, reply, core)
        except Exception:
            logger.exception("Advanced read failed")
            result = None
        if result is None:
            self.stopped = True
            self.values.clear()
            logger.warning("Advanced read stopped (register %s): no valid reply or alias mismatch; core polling unchanged", start)
            return
        stamp = time.monotonic()
        self.values.update({a: (v,stamp) for a,v in result.items()})
        if 678 in result and self.expected_max is not None:
            actual = result[678]
            if actual == self.expected_max:
                logger.info("Maximum chaudière confirmed: %.1f °C", actual / 10)
            else:
                logger.warning("Maximum chaudière not confirmed: requested %s, read %s", self.expected_max, actual)
            self.expected_max = None
        self.index = (self.index + 1) % len(BLOCKS)

    def poll_experimental(self, interface, regulator, logger, master_time):
        # Never compete with a confirmed advanced read or exceed the master window.
        now = time.monotonic()
        if now < self.experimental_next or time.time() - master_time > 1.0:
            return
        for _ in EXPERIMENTAL_READ:
            address = EXPERIMENTAL_READ[self.experimental_index]
            self.experimental_index = (self.experimental_index + 1) % len(EXPERIMENTAL_READ)
            if address not in self.experimental_failed:
                break
        else:
            return
        self.experimental_next = now + 60
        try:
            reply = interface.masterReadAnalog(regulator, address, 1, timeout=0.75)
            word = reply.get(address) if isinstance(reply, dict) else None
        except Exception:
            logger.exception("Experimental Modbus read failed (register %s)", address)
            word = None
        if decode_experimental(address, word) is None:
            self.experimental_values.pop(address, None)
            self.experimental_failed.add(address)
            logger.warning("Experimental register %s unavailable (raw=%r); skipping until restart", address, word)
            return
        self.experimental_values[address] = (word, time.monotonic())
        logger.debug("Installer register %s: raw=%s, decoded=%s",
                     address, word, decode_experimental(address, word))

    def messages(self, online):
        now = time.monotonic()
        result = {}
        for address, _, _, _, _, _, _ in SETTINGS:
            item = self.values.get(address)
            v = decode(address, item[0]) if self.enabled and online and not self.stopped and item and now-item[1] < 150 else None
            root = f"advanced/{address}"
            result[root] = v or ""
            result[root+"/availability"] = "Online" if v is not None else "Offline"
        for address, *_ in EXPERIMENTAL_SETTINGS:
            item = self.experimental_values.get(address)
            ttl = 150 if address in EXPERIMENTAL_CORE else 1800
            v = decode_experimental(address, item[0]) if self.enabled and online and not self.stopped and item and now-item[1] < ttl else None
            root = f"advanced/{address}"
            result[root] = v or ""
            result[root+"/availability"] = "Online" if v is not None else "Offline"
        return result
