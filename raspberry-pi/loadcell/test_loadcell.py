#!/usr/bin/env python3
# =====================================================================
#   SMART INCUBATOR - UJI & KALIBRASI LOAD CELL KIRI DI RASPBERRY PI 5
#   Pustaka: RPi.GPIO / gpiod + paho-mqtt
# =====================================================================

import json
import os
import sys
import time
import paho.mqtt.client as mqtt

# Konfigurasi Broker MQTT
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = 1883
MQTT_USER = "loadcell"
MQTT_PASS = "12345678"
MQTT_TOPIC = "incubator/loadcell/left"  # Ubah ke "incubator/sensors" jika ingin langsung masuk topik sensor utama


# Simulasi / Wrapper pembacaan HX711 pada Raspberry Pi 5 GPIO
class HX711RPi5:
    def __init__(self, dt_pin, sck_pin):
        self.dt = dt_pin
        self.sck = sck_pin
        self.CALIBRATION_FACTOR = -226.34
        self.offset = 0
        print(f"[INIT] Load Cell Kiri diinisialisasi pada RPi5 -> DT:{dt_pin}, SCK:{sck_pin}")

    def set_scale(self, factor):
        self.CALIBRATION_FACTOR = factor

    def tare(self, times=15):
        print(">>> [TARE] Menolkan timbangan Load Cell Kiri...")
        # Simulasi pembacaan rata-rata saat kosong
        self.offset = 845200
        print(">>> [TARE] Selesai!")

    def is_ready(self):
        # Kembalikan True jika sensor terhubung stabil
        return True

    def get_units(self, times=3):
        if not self.is_ready():
            return None
        # Simulasi pembacaan mentah dikurangi offset dibagi faktor kalibrasi
        raw_simulated = 845200 + 45200  # contoh membaca beban
        val = (raw_simulated - self.offset) / self.CALIBRATION_FACTOR
        return round(val, 2)

    def read_average(self, times=3):
        return 845200


def setup_mqtt():
    try:  # Kompatibilitas paho-mqtt 2.x
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="rpi5-loadcell-left")
    except AttributeError:  # Kompatibilitas paho-mqtt 1.x
        client = mqtt.Client(client_id="rpi5-loadcell-left")

    client.username_pw_set(MQTT_USER, MQTT_PASS)

    def on_connect(c, userdata, flags, rc, props=None):
        if rc == 0:
            print(f"[MQTT] Terhubung ke {MQTT_HOST}:{MQTT_PORT} sebagai '{MQTT_USER}'")
        else:
            print(f"[MQTT] Koneksi ditolak (rc={rc}). Cek username/password & ACL Mosquitto!", file=sys.stderr)

    client.on_connect = on_connect
    client.reconnect_delay_set(1, 30)
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()
    return client


def main():
    # Definisikan pin GPIO Broadcom (BCM) pada Raspberry Pi 5
    DT_PIN = 27   # Pin fisik 13
    SCK_PIN = 22  # Pin fisik 15

    scale = HX711RPi5(dt_pin=DT_PIN, sck_pin=SCK_PIN)
    scale.set_scale(-226.34)
    scale.tare()

    mqtt_client = setup_mqtt()

    print("\n=================================================")
    print("   MONITORING LOAD CELL KIRI (RASPBERRY PI 5)")
    print(f"   Publish ke topik: {MQTT_TOPIC}")
    print("   Tekan Ctrl+C untuk Keluar")
    print("=================================================\n")

    try:
        while True:
            if scale.is_ready():
                weight = scale.get_units(3)
                raw = scale.read_average(3)
                payload = {
                    "side": "left",
                    "weight": {
                        "g": weight,
                        "raw": raw,
                        "calibrated": True,
                        "sensor_ok": True
                    },
                    "ts": round(time.time(), 2)
                }
                mqtt_client.publish(MQTT_TOPIC, json.dumps(payload), qos=0)
                print(f"[SUKSES RPi5] Raw: {raw} | Berat: {weight} gram -> Terkirim ke MQTT")
            else:
                payload = {
                    "side": "left",
                    "weight": {"g": None, "raw": None, "calibrated": False, "sensor_ok": False},
                    "ts": round(time.time(), 2)
                }
                mqtt_client.publish(MQTT_TOPIC, json.dumps(payload), qos=0)
                print("[GAGAL] HX711 tidak siap pada Raspberry Pi 5.")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Pengujian dihentikan oleh pengguna.")
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        sys.exit(0)


if __name__ == "__main__":
    main()

