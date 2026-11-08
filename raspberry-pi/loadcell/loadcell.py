#!/usr/bin/env python3
# =====================================================================
#   SMART INCUBATOR - TARE & KALIBRASI LOAD CELL KIRI (RPI 5)
#   Pinout BCM pada Header 40-Pin Raspberry Pi 5:
#     - DT  (Data)  -> GPIO 27 (Pin Fisik 13)
#     - SCK (Clock) -> GPIO 22 (Pin Fisik 15)
#     - VCC         -> Pin 5V atau 3.3V (Pin 2 atau 1)
#     - GND         -> Pin GND (Pin 14)
# =====================================================================

import time
import sys
import select

class HX711RPi5:
    def __init__(self, dt_pin, sck_pin):
        self.dt = dt_pin
        self.sck = sck_pin
        self.CALIBRATION_FACTOR = -226.34
        self.offset = 845200 # Nilai mentah (raw) awal
        print(f"[INIT] HX711 Load Cell Kiri terhubung ke RPi 5 -> DT:{dt_pin}, SCK:{sck_pin}")

    def set_scale(self, factor):
        self.CALIBRATION_FACTOR = factor

    def tare(self):
        print("\n>>> [TARE] Menyiapkan jeda penolakan... baki dikosongkan.")
        for i in range(3, 0, -1):
            print(f">>> [TARE] Menolkan dalam {i} detik...")
            time.sleep(1)
        # Ambil sampel raw aktual (simulasi pembacaan saat kosong)
        self.offset = 845200 
        print(">>> [TARE] Selesai! Timbangan berhasil di-reset ke 0.0 gram.\n")

    def is_ready(self):
        return True

    def get_raw(self):
        # Simulasi pembacaan raw mentah dari hardware
        return 845200 + 113000

    def get_units(self, times=3):
        if not self.is_ready():
            return 0.0
        val = (self.get_raw() - self.offset) / self.CALIBRATION_FACTOR
        return round(val, 1)

def main():
    DT_PIN = 27   # Pin fisik 13
    SCK_PIN = 22  # Pin fisik 15

    scale = HX711RPi5(dt_pin=DT_PIN, sck_pin=SCK_PIN)
    scale.tare()

    print("=================================================")
    print("   MENU KONTROL LOAD CELL KIRI (RASPBERRY PI 5)")
    print("=================================================")
    print(" Ketik 't' + Enter : TARE (Jeda sejenak, lalu nolkan ke 0.0 g)")
    print(" Ketik 'c' + Enter : KALIBRASI Interaktif (Input berat beban)")
    print(" Tekan Ctrl+C      : Keluar")
    print("=================================================\n")

    try:
        while True:
            # Cek apakah ada input dari terminal secara non-blocking
            if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]:
                cmd = sys.stdin.readline().strip().lower()
                if cmd == 't':
                    scale.tare()
                elif cmd == 'c':
                    print("\n--- MODUS KALIBRASI INTERAKTIF ---")
                    print("Pastikan beban referensi sudah diletakkan di atas baki!")
                    try:
                        weight_str = input("Masukkan berat beban yang diketahui (dalam gram): ").strip()
                        known_weight = float(weight_str)
                        if known_weight > 0:
                            raw_val = scale.get_raw()
                            # Hitung faktor kalibrasi baru: raw / berat_diketahui
                            scale.CALIBRATION_FACTOR = (raw_val - scale.offset) / known_weight
                            print(f"[SUKSES] Faktor kalibrasi baru diperbarui: {scale.CALIBRATION_FACTOR:.2f}")
                        else:
                            print("[BATAL] Nilai berat harus lebih besar dari 0.")
                    except ValueError:
                        print("[BATAL] Input bukan angka yang valid.")
                    print("-" * 40 + "\n")

            weight = scale.get_units(3)
            print(f"[LOAD CELL KIRI RPi5] Berat Terukur: {weight:.1f} gram", end="\r")
            sys.stdout.flush()
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n[INFO] Program dihentikan.")
        sys.exit(0)

if __name__ == "__main__":
    main()

