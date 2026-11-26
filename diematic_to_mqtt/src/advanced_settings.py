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

class AdvancedProbe:
    def __init__(self):
        self.enabled = False
        self.stopped = False
        self.index = 0
        self.next_read = 0.0
        self.values = {}
        self.expected_max = None

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
        if not self.enabled or self.stopped or time.monotonic() < self.next_read:
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

    def messages(self, online):
        now = time.monotonic()
        result = {}
        for address, _, _, _, _, _, _ in SETTINGS:
            item = self.values.get(address)
            v = decode(address, item[0]) if self.enabled and online and not self.stopped and item and now-item[1] < 150 else None
            root = f"advanced/{address}"
            result[root] = v or ""
            result[root+"/availability"] = "Online" if v is not None else "Offline"
        return result
