# Changelog

## 1.1.6

- Fix Diematic 3 circuit C mode control from Home Assistant: process `zoneC/mode/set` commands and write register 38 (`MODE_C`).
- Follow the existing mode A/B remote-control refresh procedure; other temperature and ECS controls are unchanged.


## 1.1.0

- Circuit C support (Diematic 3): mode, day/night/anti-frost setpoints, calculated temperature, departure temperature, pump. New option `enable_circuit_c`.
- Automatic reconnection: waits for the ModBus gateway and restarts the bridge every 30 s instead of stopping.
- Default log level set to INFO.

## 1.0.0

- Home Assistant app packaging of [Benoit3/Diematic_to_MQTT](https://github.com/Benoit3/Diematic_to_MQTT): options configured from the UI.
