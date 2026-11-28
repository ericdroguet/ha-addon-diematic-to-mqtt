# Changelog

## 1.1.14

- Isolate failed advanced blocks and individual installer reads, retry after 60/120/240/480/900 seconds, keep healthy telemetry and existing MQTT IDs. Normal poll intervals and timeouts unchanged; log register, reason, retry and recovery.
- Expire outstanding manual readback after 150 seconds; never replay a write; lock the affected installer write until restart if confirmation expires or mismatches. Confirmed block failures inhibit installer writes until recovery.
- Prepare physical validation of C slope (669), building inertia (264), minimum burner run (269), and burner delay (271); these remain read-only pending verified manufacturer ranges and write-address confirmation. No automatic tuning.
- No recorder migration, entity deletion, unique ID or MQTT topic change. Regression tests cover recovery, rejection, write expiry and historical sensor IDs.

## 1.1.13

- Manual V3V band register 266 only, 4–16 K in integer steps; no automatic tuning. Ignore retained commands and stale data. Fresh preflight read, one write, independent readback and fail-block on mismatch. Other installer writes unchanged.
- Test readback, invalid input, changed preflight, and mismatch in the image build.

## 1.1.12

- Consolidate Diematic 3 installer settings against the physical panel (CTRL 1422, M2: OUI). Keep legacy object IDs, change discovery labels only. See REGISTER_MAP.md.
- Read 426 (offset readback) and 435 (unidentified diagnostic word) from existing core reads. Keep 438 at historical scaling and label as unverified, not boiler bandwidth. 72 remains unverified.
- Retire 73, 267, 306: no further reads/discovery, invalidate retained availability on reconnect. No automatic deletion of entity registry/history; remove obsolete HA entries separately if desired.
- Stop the raw exploration; retain only seven additional installer reads at the existing one-per-minute maximum. No new writable addresses or commands; 678 stays unchanged.

## 1.1.11

- Diematic 3: log the existing raw register map once after the first successful core refresh (including 73, 426, 435, 436, 438, 454).
- Read eight additional candidate words (265, 274, 277, 278, 604, 622, 623, 625) once per app start, interleaved with existing experimental polling AFTER the first experimental round. The same one-per-minute budget and 0.75 s timeout apply, with confirmed reads taking precedence. Raw values, including 65535, are logged without assigning a meaning.
- Enabled only via the existing advanced-read option; no new controls, no new sensors, no change to existing control or discovery. Existing experimental sensors remain for comparison until the mappings are verified.

## 1.1.10

- Read-only experimental Delta-table candidates on Diematic 3, enabled with the existing advanced-read option. Six candidates reuse core reads; other candidates are probed singly, no more often than once per minute, only between confirmed advanced reads and within the existing master window.
- Candidate meanings are unverified until compared with the physical installer menu; invalid/missing values are unavailable, and failed extra addresses are skipped until restart. The confirmed registers and existing write to 678 are unchanged.
- No new commands or automatic tuning. Delta table: https://community.jeedom.com/uploads/short-url/luej1LlG3Wu6xGjpEOxCXbrPCoq.pdf (pages 1, 4, 6).

## 1.1.8

- Optional `diematic3_advanced_read` (default false): read-only probe for calculated boiler demand (620), C room influence/slope/min/max (668–671), boiler min/max (677–678). Sofrel S500 p.4. No writes or change to existing controls.
- Probe only in existing master windows after core reads, one block every 30 seconds, short timeout and fail-stop on missing responses/alias mismatch. Values outside documented bounds or stale/offline are unavailable. Enable explicitly and validate on the physical controller before exposing any writes.
- No automatic tuning of boiler safety limits, pumps, burner or mixing valve.

## 1.1.7

- Diematic 3: six read-only diagnostic binary sensors from existing registers 427 (stage 1/2 burner commands, burner alarm contact, auxiliary pump command) and 428 (circuit C valve open/close commands), using Sofrel S500 table D1 bit mapping. These are output orders, not measured flame or valve position; verify on the actual boiler before using for control or fuel integration.
- Invalid/missing words and offline regulator mark diagnostics unavailable, not active or zero. Existing entity IDs and control logic unchanged.
- Optional `mqtt_publish_raw_registers` (default false): retained JSON snapshot of registers already polled; no additional Modbus reads, no writes, clears retained snapshot on reconnect/offline/disable. Reuses the existing changed-value MQTT buffer.
- Optional `log_level` (default INFO): DEBUG/INFO/WARNING/ERROR. DEBUG may be verbose; use temporarily.
- Keep existing circuit C, ECS, two-stage counters and fuel monitoring unchanged. No extra register polling in this version.
- Source for bit definitions: https://community.jeedom.com/uploads/short-url/qjNF6pFIcvSXRka7EFug2vIEKZE.pdf (Table D1, p.6).

## 1.1.6

- Fix Diematic 3 circuit C mode control from Home Assistant: process `zoneC/mode/set` commands and write register 38 (`MODE_C`).
- Follow the existing mode A/B remote-control refresh procedure; other temperature and ECS controls are unchanged.


## 1.1.0

- Circuit C support (Diematic 3): mode, day/night/anti-frost setpoints, calculated temperature, departure temperature, pump. New option `enable_circuit_c`.
- Automatic reconnection: waits for the ModBus gateway and restarts the bridge every 30 s instead of stopping.
- Default log level set to INFO.

## 1.0.0

- Home Assistant app packaging of [Benoit3/Diematic_to_MQTT](https://github.com/Benoit3/Diematic_to_MQTT): options configured from the UI.
