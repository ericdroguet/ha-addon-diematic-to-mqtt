# Changelog

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
