#!/bin/sh
# Genere la configuration depuis les options de l'add-on
python3 /render_config.py
cd /app

PID=""
trap 'echo "Arret demande"; [ -n "$PID" ] && kill -TERM "$PID" 2>/dev/null; wait "$PID" 2>/dev/null; exit 0' TERM INT

module_joignable() {
  python3 -c "import socket,json;o=json.load(open('/data/options.json'));socket.create_connection((o['modbus_ip'],int(o['modbus_port'])),5).close()" 2>/dev/null
}

while true; do
  # Attend que le module RS485/WiFi reponde avant de lancer la passerelle
  until module_joignable; do
    echo "Module ModBus injoignable, nouvel essai dans 30 s"
    sleep 30 &
    wait $!
  done
  python3 Diematic32MQTT.py &
  PID=$!
  wait "$PID"
  echo "Diematic32MQTT arrete (code $?), relance dans 30 s"
  PID=""
  sleep 30 &
  wait $!
done