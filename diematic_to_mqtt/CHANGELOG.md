# Changelog

## 1.1.0

- Circuit C support (Diematic 3): mode, day/night/anti-frost setpoints, calculated temperature, departure temperature, pump. New option `enable_circuit_c`.
- Automatic reconnection: waits for the ModBus gateway and restarts the bridge every 30 s instead of stopping.
- Default log level set to INFO.

## 1.0.0

- Home Assistant app packaging of [Benoit3/Diematic_to_MQTT](https://github.com/Benoit3/Diematic_to_MQTT): options configured from the UI.
