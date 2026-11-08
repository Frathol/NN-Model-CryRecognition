// Smart Incubator dashboard configuration
window.DASHBOARD_CONFIG = {
  // MQTT over WebSockets through nginx (ws://<pi-ip>/mqtt). No change needed
  // when the dashboard is served by the Raspberry Pi itself.
  mqttUrl: (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/mqtt",

  // Pre-filled on the sign-in page. The password is never stored in this file:
  // users type it on login.html and the broker checks it.
  defaultUsername: "dashboard",

  topics: {
    sensors:    "incubator/sensors",
    esp32:      "incubator/esp32/status",
    cryResult:  "incubator/cry/result",
    cryState:   "incubator/cry/state",
    cryStatus:  "incubator/cry/status",
  },

  historyMinutes: 10,        // time span shown on the trend charts
  sensorTimeoutSec: 10,      // no data for this long -> sensor node treated as offline

  // Alarm limits. ADJUST to the clinical protocol / doctor's recommendation.
  limits: {
    temp: { min: 32.0, max: 37.5 },   // °C incubator air temperature (primary sensor: SHT31)
    hum:  { min: 40,   max: 80 },     // %RH
    bpm:  { min: 100,  max: 180 },    // neonatal heart rate
    soc:  { min: 30 },                // % battery, warning below this
  },
};
