# Aturan Klinis Finite State Machine (FSM) & Code Blue

Dokumen ini menjelaskan logika evaluasi kondisi klinis bayi pada modul `fsm.py` untuk mencegah alarm palsu (*false alarm*) sekaligus memastikan respons cepat saat kondisi gawat darurat.

---

## 1. Daftar State FSM

| State | Tingkat Keparahan | Deskripsi Kondisi |
| :--- | :--- | :--- |
| **`NORMAL`** | Aman | Seluruh parameter fisiologis (suhu ruang, suhu kulit, kelembapan, BPM) berada dalam rentang normal dan tidak ada tangisan berkelanjutan. |
| **`CRY_WATCH`** | Observasi | Terdeteksi tangisan bayi, namun parameter vital (suhu & detak jantung) masih dalam batas aman. |
| **`PRE_ALARM`** | Peringatan Dini | Salah satu parameter mendekati/sedikit melewati batas ambang normal atau tangisan berlangsung cukup lama. |
| **`ALARM`** | Darurat (*Code Blue*) | Parameter vital melewati batas kritis (mis. takikardia/bradikardia pada BPM, hipotermia/hipertermia kulit) atau kombinasi tangisan + anomali vital. |
| **`THERMAL_CUTOFF`** | Proteksi Termal | Suhu ruang atau suhu kulit melampaui batas aman maksimum sehingga SSR pemanas diputus otomatis (`heater_cutoff = true`). |

---

## 2. Alur Evaluasi Multi-Sensor

```
[Data Sensor ESP32-S3] ──┐
                         ├──▶ [ Validator & Hysteresis (fsm.py) ] ──▶ [ State & Code Blue ]
[Deteksi Tangis AI]    ──┘
```

1. **Validasi Kontak Sensor**: Nilai BPM hanya memicu eskalasi jika `heart.contact == true` dan `heart.sensor_ok == true` guna menghindari alarm palsu saat sensor lepas.
2. **Proteksi Over-Heat Prioritas Utama**: Jika suhu melewati ambang batas termal kritis, sistem langsung masuk ke `THERMAL_CUTOFF` dan menginstruksikan pemutusan pemanas.
3. **Integrasi Tangisan & Tanda Vital**: Tangisan yang disertai anomali detak jantung atau suhu akan mempercepat transisi menuju `ALARM` (*Code Blue*).
