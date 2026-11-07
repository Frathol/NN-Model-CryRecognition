#!/usr/bin/env bash
# =====================================================================
#  Simulator ESP32: publish data sensor dummy ke incubator/sensors tiap 2 detik
#  (format JSON sama persis dengan firmware), untuk menguji dashboard tanpa ESP32.
#
#  Pemakaian:
#     MQTT_PASSWORD='<pw admin>' ./dummy_esp32.sh                 # di Raspberry Pi
#     MQTT_PASSWORD='<pw admin>' ./dummy_esp32.sh 10.223.183.158  # dari laptop
#  Opsi env: MQTT_USER (default admin), INTERVAL (default 2 detik)
#  Hentikan dengan Ctrl+C (status ESP32 otomatis kembali "offline").
# =====================================================================
set -euo pipefail

HOST="${1:-localhost}"
USER_="${MQTT_USER:-admin}"
PASS="${MQTT_PASSWORD:?Set MQTT_PASSWORD dulu, mis. MQTT_PASSWORD='xxx' $0}"
INTERVAL="${INTERVAL:-2}"

pub() { mosquitto_pub -h "$HOST" -u "$USER_" -P "$PASS" "$@"; }

cleanup() {
  pub -t incubator/esp32/status -m offline -r || true
  echo -e "\nSelesai. Status ESP32 di-set offline."
}
trap cleanup EXIT
trap "exit 0" INT TERM

pub -t incubator/esp32/status -m online -r
echo "Mengirim data dummy ke $HOST (topic incubator/sensors) tiap ${INTERVAL}s. Ctrl+C untuk berhenti."

start=$(date +%s)
while true; do
  t=$(( $(date +%s) - start ))
  # Nilai berubah perlahan (gelombang sinus + noise kecil) supaya grafik terlihat hidup
  json=$(awk -v t="$t" -v seed="$RANDOM" 'BEGIN {
    srand(seed)
    pi = 3.14159265
    ntc  = 36.6 + 0.3 * sin(2*pi*t/120) + (rand()-0.5)*0.05
    sT   = 33.0 + 0.6 * sin(2*pi*t/90)  + (rand()-0.5)*0.1
    sH   = 60.0 + 5.0 * sin(2*pi*t/150) + (rand()-0.5)*0.5
    dT   = sT + (rand()-0.5)*0.8
    dH   = sH + (rand()-0.5)*3
    bpm  = 135 + 12 * sin(2*pi*t/60) + (rand()-0.5)*4
    ir   = 80000 + int(rand()*5000)
    rssi = -50 - int(rand()*10)
    printf "{\"ntc\":{\"temp\":%.2f},\"sht31\":{\"temp\":%.2f,\"hum\":%.2f},\"dht11\":{\"temp\":%.1f,\"hum\":%.1f},\"heart\":{\"bpm\":%d,\"contact\":true,\"ir\":%d,\"sensor_ok\":true},\"uptime_s\":%d,\"rssi\":%d}",
           ntc, sT, sH, dT, dH, bpm, ir, t, rssi
  }')
  pub -t incubator/sensors -m "$json"
  echo "[$(date +%H:%M:%S)] $json"
  sleep "$INTERVAL"
done
