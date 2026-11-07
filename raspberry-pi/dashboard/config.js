// Konfigurasi dashboard Smart Incubator
window.DASHBOARD_CONFIG = {
  // MQTT over WebSockets lewat nginx (ws://<ip-pi>/mqtt). Tidak perlu diubah
  // bila dashboard dibuka dari Raspberry Pi itu sendiri.
  mqttUrl: (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/mqtt",
  username: "dashboard",                 // akun READ-ONLY (lihat ACL Mosquitto)
  password: 12345678,    // diisi otomatis oleh setup_pi.sh

  topics: {
    sensors:    "incubator/sensors",
    esp32:      "incubator/esp32/status",
    cryResult:  "incubator/cry/result",
    cryState:   "incubator/cry/state",
    cryStatus:  "incubator/cry/status",
  },

  historyMinutes: 10,        // panjang data pada grafik
  sensorTimeoutSec: 10,      // tanpa data selama ini -> sensor dianggap offline

  // Batas alarm — SESUAIKAN dengan protokol klinis / rekomendasi dokter.
  limits: {
    temp: { min: 32.0, max: 37.5 },   // °C suhu udara inkubator (sensor utama: SHT31)
    hum:  { min: 40,   max: 80 },     // %RH
    bpm:  { min: 100,  max: 180 },    // detak jantung neonatus
  },
};
