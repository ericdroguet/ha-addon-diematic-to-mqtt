"""Optional Sofrel S500 p.4 probe for Diematic 3.
Only explicitly allowed installer registers 678 and 266 can be written; published values come from reads.
Reject invalid words; isolate failures and retry with bounded backoff.
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

READ_INTERVAL = 30
EXPERIMENTAL_INTERVAL = 60
CONFIRM_TIMEOUT = 150

def retry_delay(failures):
    return min(900, 60 * 2 ** min(failures - 1, 4))

def decode(address, word):
    _, _, _, lo, hi, divisor, _ = SPECS[address]
    if type(word) is not int or not lo <= word <= hi:
        return None
    return str(word) if divisor == 1 else f"{word / divisor:.1f}"

def block_error(start, count, reply, core):
    if not isinstance(reply, dict):
        return "no reply or invalid response type"
    if any(type(reply.get(a)) is not int or not 0 <= reply[a] < 65535
           for a in range(start, start + count)):
        return "missing or invalid word"
    if start == 619 and (type(core.get(44)) is not int or abs(reply[619] - core[44]) > 30):
        return "alias 619/44 mismatch"
    if start == 664 and any(reply[a] != core.get(b) for a,b in ((664,35),(665,36))):
        return "alias 664/35 or 665/36 mismatch"
    return None

def validate_block(start, count, reply, core):
    if block_error(start, count, reply, core) is not None:
        return None
    return {a: reply[a] for a in range(start,start+count) if a in SPECS}

# Diematic 3 CTRL 1422: panel values cross-checked on 2026-11-27.
# Keep legacy MQTT IDs to preserve consumers and history.
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
        self.max_deadline = None
        self.max_write_blocked = False
        self.block_failures = {}
        self.block_retry_at = {}
        self.experimental_failures = {}
        self.experimental_retry_at = {}
        self.experimental_values = {}
        self.experimental_index = 0
        self.experimental_next = 0.0
        self.experimental_failed = set()
        self.band_queued_at = None
        self.expected_band = None
        self.band_deadline = None
        self.band_write_blocked = False

    def can_write_band(self):
        item = self.experimental_values.get(266)
        return (self.enabled and not self.stopped and not self.band_write_blocked
                and self.expected_band is None and self.band_queued_at is None
                and 266 not in self.experimental_failed and item is not None
                and time.monotonic() - item[1] < 900
                and decode_experimental(266, item[0]) is not None)

    def write_band(self, interface, regulator, data, logger, master_time):
        queued = self.band_queued_at
        self.band_queued_at = None
        if (queued is None or time.monotonic() - queued > 30
                or not self.can_write_band() or len(data) != 1
                or type(data[0]) is not int or data[0] not in range(40, 161, 10)
                or time.time() - master_time > 0.6):
            logger.warning("Bande V3V: stale or unsafe command rejected")
            return True
        old = self.experimental_values[266][0]
        try:
            reply = interface.masterReadAnalog(regulator, 266, 1, timeout=0.75)
            actual = reply.get(266) if isinstance(reply, dict) else None
        except Exception:
            actual = None
        if decode_experimental(266, actual) is None:
            self.band_write_blocked = True
            self.experimental_values.pop(266, None)
            logger.warning("Bande V3V: invalid preflight read; no write")
            return False
        self.experimental_values[266] = (actual, time.monotonic())
        if actual != old or time.time() - master_time > 1.4:
            logger.warning("Bande V3V: changed readback or insufficient bus window; no write")
            return True
        if actual == data[0]:
            logger.info("Bande V3V already at requested value")
            return True
        self.expected_band = data[0]
        self.band_deadline = time.monotonic() + CONFIRM_TIMEOUT
        self.experimental_values.pop(266, None)
        logger.info("Bande V3V: single write %s -> %s", actual, data[0])
        try:
            return bool(interface.masterWriteAnalog(regulator, 266, data))
        except Exception:
            logger.exception("Bande V3V: write outcome unknown; readback required")
            return False

    def verify_band(self, interface, regulator, logger):
        expected = self.expected_band
        try:
            reply = interface.masterReadAnalog(regulator, 266, 1, timeout=0.75)
            actual = reply.get(266) if isinstance(reply, dict) else None
        except Exception:
            actual = None
        self.expected_band = None
        self.band_deadline = None
        if decode_experimental(266, actual) is not None:
            self.experimental_values[266] = (actual, time.monotonic())
        if actual == expected:
            logger.info("Bande V3V confirmed by readback: %.1f K", actual / 10)
        else:
            self.band_write_blocked = True
            logger.warning("Bande V3V NOT confirmed: expected %s, actual %s; writes blocked until restart", expected, actual)

    def can_write_max(self):
        item = self.values.get(678)
        return (self.enabled and not self.stopped and not self.max_write_blocked and self.expected_max is None
                and item is not None and time.monotonic() - item[1] < 150
                and decode(678, item[0]) is not None)

    def verify_max(self, expected):
        self.expected_max = expected
        self.max_deadline = time.monotonic() + CONFIRM_TIMEOUT
        self.values.pop(678, None)
        self.index = BLOCKS.index((677, 2))
        self.next_read = 0.0

    def poll(self, interface, regulator, core, logger, master_time):
        if not self.enabled:
            return
        # Core values are independent of the optional block probes.
        stamp = time.monotonic()
        for address in EXPERIMENTAL_CORE:
            self.experimental_values[address] = (core.get(address), stamp)
        # Never retry an unconfirmed write. Expire the readback priority,
        # latch the relevant write lock, and resume normal telemetry.
        if self.expected_max is not None and self.max_deadline is not None and stamp >= self.max_deadline:
            logger.warning("Maximum chaudière readback expired (678); writes blocked until restart")
            self.expected_max = None
            self.max_deadline = None
            self.max_write_blocked = True
            self.values.pop(678, None)
        if self.expected_band is not None:
            if self.band_deadline is not None and stamp >= self.band_deadline:
                logger.warning("Bande V3V readback expired (266); writes blocked until restart")
                self.expected_band = None
                self.band_deadline = None
                self.band_write_blocked = True
                self.experimental_values.pop(266, None)
            else:
                if time.time() - master_time <= 1.0:
                    self.verify_band(interface, regulator, logger)
                return
        if time.time() - master_time > 1.0:
            return
        if stamp < self.next_read:
            self.poll_experimental(interface, regulator, logger, master_time)
            return
        # One block per normal 30 s budget, even while another block is cooling down.
        for _ in BLOCKS:
            start, count = BLOCKS[self.index]
            if stamp >= self.block_retry_at.get(start, 0):
                break
            self.index = (self.index + 1) % len(BLOCKS)
        else:
            self.poll_experimental(interface, regulator, logger, master_time)
            return
        self.index = (self.index + 1) % len(BLOCKS)
        self.next_read = stamp + READ_INTERVAL
        try:
            reply = interface.masterReadAnalog(regulator, start, count, timeout=0.75)
            reason = block_error(start, count, reply, core)
            result = validate_block(start, count, reply, core) if reason is None else None
        except Exception:
            logger.exception("Advanced Modbus exception (register %s, count %s)", start, count)
            reason, result = "exception", None
        if result is None:
            failures = self.block_failures.get(start, 0) + 1
            self.block_failures[start] = failures
            delay = retry_delay(failures)
            self.block_retry_at[start] = time.monotonic() + delay
            for address in range(start, start + count):
                self.values.pop(address, None)
            # Keep installer writes locked while any confirmed block is unhealthy.
            self.stopped = True
            logger.warning("Advanced read failed (register %s, count %s): %s; attempt %s, retry in %ss", start, count, reason, failures, delay)
            return
        if self.block_failures.pop(start, 0):
            logger.info("Advanced read recovered (register %s, count %s)", start, count)
        self.block_retry_at.pop(start, None)
        self.stopped = bool(self.block_failures)
        stamp = time.monotonic()
        self.values.update({a: (v,stamp) for a,v in result.items()})
        if 678 in result and self.expected_max is not None:
            actual = result[678]
            if actual == self.expected_max:
                logger.info("Maximum chaudière confirmed: %.1f °C", actual / 10)
            else:
                self.max_write_blocked = True
                logger.warning("Maximum chaudière not confirmed: requested %s, read %s; writes blocked until restart", self.expected_max, actual)
            self.expected_max = None
            self.max_deadline = None

    def poll_experimental(self, interface, regulator, logger, master_time):
        now = time.monotonic()
        if now < self.experimental_next or time.time() - master_time > 1.0:
            return
        for _ in EXPERIMENTAL_READ:
            address = EXPERIMENTAL_READ[self.experimental_index]
            self.experimental_index = (self.experimental_index + 1) % len(EXPERIMENTAL_READ)
            if now >= self.experimental_retry_at.get(address, 0):
                break
        else:
            return
        self.experimental_next = now + EXPERIMENTAL_INTERVAL
        try:
            reply = interface.masterReadAnalog(regulator, address, 1, timeout=0.75)
            word = reply.get(address) if isinstance(reply, dict) else None
        except Exception:
            logger.exception("Experimental Modbus read failed (register %s)", address)
            word = None
        if decode_experimental(address, word) is None:
            self.experimental_values.pop(address, None)
            self.experimental_failed.add(address)
            failures = self.experimental_failures.get(address, 0) + 1
            self.experimental_failures[address] = failures
            delay = retry_delay(failures)
            self.experimental_retry_at[address] = time.monotonic() + delay
            logger.warning("Experimental register %s unavailable (raw=%r); attempt %s, retry in %ss", address, word, failures, delay)
            return
        if self.experimental_failures.pop(address, 0):
            logger.info("Experimental register %s recovered", address)
        self.experimental_retry_at.pop(address, None)
        self.experimental_failed.discard(address)
        self.experimental_values[address] = (word, time.monotonic())
        logger.debug("Installer register %s: raw=%s, decoded=%s",
                     address, word, decode_experimental(address, word))

    def messages(self, online):
        now = time.monotonic()
        result = {}
        for address, _, _, _, _, _, _ in SETTINGS:
            item = self.values.get(address)
            v = decode(address, item[0]) if self.enabled and online and item and now-item[1] < 150 else None
            root = f"advanced/{address}"
            result[root] = v or ""
            result[root+"/availability"] = "Online" if v is not None else "Offline"
        for address, *_ in EXPERIMENTAL_SETTINGS:
            item = self.experimental_values.get(address)
            ttl = 150 if address in EXPERIMENTAL_CORE else 1800
            v = decode_experimental(address, item[0]) if self.enabled and online and item and now-item[1] < ttl else None
            root = f"advanced/{address}"
            result[root] = v or ""
            result[root+"/availability"] = "Online" if v is not None else "Offline"
        return result
