import json
import os

with open("/data/options.json") as f:
    o = json.load(f)


def b2s(v):
    return "True" if v else "False"


conf = f"""[Modbus]
ip: {o.get('modbus_ip', '192.168.1.X')}
port: {o.get('modbus_port', 20108)}
regulatorAddress:{o.get('regulator_address', '0x0A')}
interfaceAddress:{o.get('interface_address', '0x32')}

[MQTT]
brokerHost: {o.get('mqtt_broker_host', 'core-mosquitto')}
brokerPort: {o.get('mqtt_broker_port', 1883)}
brokerLogin: {o.get('mqtt_broker_login', '')}
brokerPassword: {o.get('mqtt_broker_password', '')}
topicPrefix: {o.get('topic_prefix', 'home/heater')}
clientId: {o.get('client_id', 'boiler')}
publishRawRegisters: {b2s(o.get('mqtt_publish_raw_registers', False))}

[Boiler]
regulatorType:{o.get('regulator_type', 'Diematic3')}
timezone:{o.get('timezone', 'CET')}
timeSync:{b2s(o.get('time_sync', False))}
period: {o.get('period', 10)}
enable_circuit_A: {b2s(o.get('enable_circuit_a', False))}
enable_circuit_B: {b2s(o.get('enable_circuit_b', False))}
enable_circuit_C: {b2s(o.get('enable_circuit_c', False))}

[Home Assistant]
MQTT_DiscoveryEnable:{1 if o.get('mqtt_discovery_enable', True) else 0}
discovery_prefix: {o.get('discovery_prefix', 'homeassistant')}
"""

os.makedirs("/app/conf", exist_ok=True)
os.makedirs("/app/log", exist_ok=True)

with open("/app/conf/Diematic32MQTT.conf", "w") as f:
    f.write(conf)

# Keep normal INFO logging by default; enable frame logs only for diagnostics.
log_level = o.get('log_level', 'INFO')
if log_level not in ('DEBUG', 'INFO', 'WARNING', 'ERROR'):
    log_level = 'INFO'
with open('/app/conf/logging.conf', 'r') as f:
    logging_conf = f.read()
logging_conf = logging_conf.replace('[logger_root]\nlevel=INFO', f'[logger_root]\nlevel={log_level}')
with open('/app/conf/logging.conf', 'w') as f:
    f.write(logging_conf)
