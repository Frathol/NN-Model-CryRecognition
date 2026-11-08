# Dokumentasi Skema Topik & Payload MQTT

Dokumen ini merangkum seluruh topik MQTT yang digunakan dalam ekosistem **Smart Incubator** beserta contoh struktur JSON dan aturan *Access Control List* (ACL).

---

## 1. Ringkasan Topik MQTT

| Topik | Publisher | Subscriber | Retained | Interval / Trigger |
| :--- | :--- | :--- | :---: | :--- |
| `incubator/sensors` | ESP32-S3 | Dashboard, `fsm.py`, CSV Logger | Tidak | Setiap 2 detik |
| `incubator/esp32/status` | ESP32-S3 | Dashboard, `fsm.py` | Ya | Saat konek / terputus (LWT) |
| `incubator/cry/result` | `cry_service.py` | Dashboard, `fsm.py`, CSV Logger | Tidak | Setiap jendela inferensi (~0.5–1.0s) |
| `incubator/cry/state` | `cry_service.py` | Dashboard, `fsm.py` | Ya | Saat status tangisan berubah |
| `incubator/cry/status` | `cry_service.py` | Dashboard | Ya | Status layanan (`online`/`no_mic`/`offline`) |
| `incubator/status` | `fsm.py` | Dashboard | Ya | Evaluasi status FSM klinis |
| `incubator/alarm` | `fsm.py` | Dashboard, ESP32-S3 | Ya | Saat terjadi eskalasi alarm / Code Blue |

---

## 2. Contoh Payload JSON

### A. `incubator/sensors`
```json
{
  "ntc": { "temp": 36.65 },
  "sht31": { "temp": 34.20, "hum": 62.5 },
  "dht11": { "temp": 34.00, "hum": 61.0 },
  "heart": {
    "bpm": 138.5,
    "contact": true,
    "ir": 84210,
    "sensor_ok": true
  },
  "weight": {
    "g": 2850.0,
    "raw": 412950,
    "calibrated": true,
    "sensor_ok": true
  },
  "power": {
    "heater_on": true,
    "heater_pct": 45,
    "fan_on": true,
    "mode": "AUTO_PID"
  },
  "alarm": {
    "heater_cutoff": false
  },
  "uptime_s": 3620,
  "rssi": -58
}
```

### B. `incubator/cry/result`
```json
{
  "label": "hungry",
  "confidence": 0.84,
  "cry_prob": 0.91,
  "is_cry": true,
  "level_db": -24.3,
  "latency_ms": 18.4,
  "ts": 1731112000.12
}
```

### C. `incubator/status` (FSM Output)
```json
{
  "state": "NORMAL",
  "code_blue": false,
  "heater_cutoff": false,
  "reasons": [],
  "ts": 1731112002.05
}
```
