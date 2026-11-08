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
    fsm:        "incubator/fsm/state",   // alarm decision from fsm.py (retained)
  },

  historyMinutes: 10,        // time span shown on the trend charts
  sensorTimeoutSec: 10,      // no data for this long -> sensor node treated as offline
  fsmTimeoutSec: 15,         // fsm.py republishes every 5 s; older than this -> FSM treated as not running

  // Alarm limits shown on the range tracks, and used only when fsm.py is not running.
  // Keep them EQUAL to fsm.py: THERMAL_HAZARD = setpoint 33 ± 1 °C, HUMIDITY_HAZARD = 50–80 %RH.
  // (Paper Table 2 lists 31–34 °C [confirm]; agree on one value and change fsm.py and this file together.)
  // ADJUST to the clinical protocol / doctor's recommendation.
  limits: {
    temp: { min: 32.0, max: 34.0 },   // °C incubator air temperature (primary sensor: SHT31)
    hum:  { min: 50,   max: 80 },     // %RH
    // CODE BLUE (skin): outside 36.2–37.5 °C, no delay. One profile for all infants.
    //   36.2 °C = NHS West of Scotland, "Thermal Care of Neonates": preterm skin 36.2–37.2 °C
    //   37.5 °C = CMNRP "Newborn Thermoregulation" p.11: hyperthermia above 37.3 or 37.5 °C
    //             (NHS WoS: skin upper alarm limit at most 37.5 °C)
    skin: { min: 36.2, max: 37.5 },   // °C skin / axilla temperature (NTC)
    soc:  { min: 30 },                // % battery, warning below this
  },

  // Heart rate (pulse sensor). Sources: see the doc "Sumber Rujukan Batas Alarm".
  heartRate: {
    normal:   { min: 100, max: 160 },   // ASU / CAHS: 120–160 normal, down to 100 asleep
    cryingMax: 180,                     // ASU: "while crying ... as high as 180" -> no warning while crying
    codeBlue: { below: 100, above: 180 },
    // Seconds a value must stay abnormal before the alarm shows.
    //   high: 180 s = Erasmus MC–Sophia NICU delayed the high-HR alarm 180 s with no rise in critical events
    //   low:  no delay in the NICU studies; 10 s (two ESP32 messages) filters pulse-sensor motion noise
    // high is normally taken from the ESP32 (alarm.hr_high_delay_s, set by DEMO_FAST_DELAY in the
    // firmware: 30 s demo / 180 s paper), so the dashboard, fsm.py and the ESP32 always use the same
    // delay. The value below is used only until/unless the ESP32 sends it.
    delaySec: { low: 10, high: 180 },
  },
};
