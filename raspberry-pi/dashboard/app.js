/* Smart Incubator dashboard: MQTT over WebSockets + Chart.js */
(() => {
  "use strict";
  const CFG = window.DASHBOARD_CONFIG;
  const Auth = window.IncubatorAuth;
  const creds = Auth.get();
  if (!creds) { location.replace("login.html"); return; }

  const T = CFG.topics;
  const $ = (id) => document.getElementById(id);
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  const state = {
    broker: "connecting",  // connecting | connected | reconnecting | disconnected | error
    lastSensorAt: 0,
    lastCryAt: 0,
    esp32: "unknown",
    cryService: "unknown",
    crying: false,
    fsm: null,             // last message from fsm.py: {state, active_alerts, advisory, ...}
    lastFsmAt: 0,
    espLocal: null,        // NORMAL / WARN / CRITICAL computed on the ESP32 (fsm_state field)
    codeBlue: [],          // reasons from the dashboard's own Code Blue check (skin temp / heart rate)
    hrSince: { low: null, high: null, elevated: null },  // when each heart-rate condition started (ms)
    hrHighDelay: null,     // high heart-rate delay sent by the ESP32 (alarm.hr_high_delay_s); null = use config.js
    espMuted: null,        // in-cabin buzzer silenced with the physical button (alarm.buzzer_muted)
    alarms: new Map(),     // key -> {level, text}
  };

  // ------------------------------------------------------------------
  //  Format & util
  // ------------------------------------------------------------------
  const NA = "--";
  const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? NA : Number(v).toFixed(d));
  const hhmmss = (t) => new Date(t).toLocaleTimeString("en-GB", { hour12: false });
  const setText = (id, txt) => { const el = $(id); if (el) el.textContent = txt; };
  const show = (id, visible) => { const el = $(id); if (el) el.hidden = !visible; };

  setText("account-name", creds.username);
  setText("trends-span", `Last ${CFG.historyMinutes} minutes, since this page was opened`);

  function setPill(id, cls, text) {
    const el = $(id);
    el.classList.remove("ok", "warn", "bad");
    if (cls) el.classList.add(cls);
    el.querySelector(".txt").textContent = text;
  }

  const KIND_CLASS = { Alarm: "critical", Warning: "warning", Normal: "normal" };
  function addEvent(kind, detail) {
    const tr = document.createElement("tr");
    const cells = [[hhmmss(Date.now()), "when"], [kind, "kind " + (KIND_CLASS[kind] || "")], [detail, ""]];
    for (const [t, cls] of cells) {
      const td = document.createElement("td"); td.textContent = t; td.className = cls; tr.appendChild(td);
    }
    const tb = $("events");
    tb.insertBefore(tr, tb.firstChild);
    while (tb.children.length > 50) tb.removeChild(tb.lastChild);
    show("events-empty", false);
  }

  // log=false: the alarm is shown, but not written to the event log (logged elsewhere)
  function setAlarm(key, active, level, text, log = true) {
    const had = state.alarms.has(key);
    if (active) {
      state.alarms.set(key, { level, text });
      if (!had && log) addEvent(level === "critical" ? "Alarm" : "Warning", text);
    } else if (had) {
      state.alarms.delete(key);
      if (log) addEvent("Normal", text.split(":")[0] + " back to normal");
    }
    renderAlarms();
  }

  function renderAlarms() {
    const list = [...state.alarms.values()].sort((a, b) =>
      (a.level === "critical" ? 0 : 1) - (b.level === "critical" ? 0 : 1));
    $("alarms").replaceChildren(...list.map(({ level, text }) => {
      const li = document.createElement("li");
      li.className = "alarm " + level;
      const sev = document.createElement("span");
      sev.className = "sev"; sev.textContent = level === "critical" ? "Critical" : "Warning";
      const txt = document.createElement("span"); txt.textContent = text;
      li.append(sev, txt);
      return li;
    }));
    renderOverall(list);
  }

  // ------------------------------------------------------------------
  //  FSM (fsm.py on the Pi) is the alarm decision maker. While it is running,
  //  the overall status shows its state; the browser checks below are only a
  //  fallback for when fsm.py is not running.
  // ------------------------------------------------------------------
  const FSM_INFO = {
    CODE_BLUE:        ["critical", "Code Blue"],
    VITAL_SIGN_ALERT: ["warning",  "Heart rate above normal"],   // fsm.py: 161–180 BPM, not crying, for 3 min
    THERMAL_HAZARD:   ["critical", "Temperature out of range"],
    HUMIDITY_HAZARD:  ["critical", "Humidity out of range"],
    MAINS_FAIL:       ["warning",  "Mains power lost"],
    SENSOR_FAULT:     ["warning",  "Sensor fault"],
    CRYING_DISTRESS:  ["critical", "Baby crying"],
    INACTIVITY_INFO:  ["warning",  "No movement"],
    POWER_WARNING:    ["warning",  "Battery low"],
    IDLE_NORMAL:      ["normal",   "All readings within limits"],
  };
  const fsmName = (s) => (FSM_INFO[s] ? FSM_INFO[s][1] : s);
  const fsmFresh = () => state.fsm !== null && Date.now() - state.lastFsmAt < CFG.fsmTimeoutSec * 1000;

  // The one answer this screen gives: is the incubator all right?
  function renderOverall(list = [...state.alarms.values()]) {
    // CODE BLUE (computed here from NTC skin temperature and heart rate) outranks every other state
    if (state.codeBlue.length) {
      $("overview").dataset.level = "critical";
      setText("overall-text", "Code Blue");
      setText("overall-detail", `${state.codeBlue.join(". ")}. Check the baby now.`);
      return;
    }
    // fsm.py uses the same Code Blue / heart-rate rules as config.js (see fsm.py VITAL LIMITS)
    const fsmAlerts = fsmFresh() ? (state.fsm.active_alerts || []) : [];
    const fsmTop = fsmFresh() ? state.fsm.state : "IDLE_NORMAL";
    const localWarn = list.length > 0;
    // fsm.py normal but the dashboard has its own alarms (e.g. heart rate above normal) -> show those
    if (state.broker === "connected" && fsmFresh() && !(fsmTop === "IDLE_NORMAL" && localWarn)) {
      const f = { ...state.fsm, state: fsmTop, active_alerts: fsmAlerts };
      const level = (FSM_INFO[f.state] || ["warning"])[0];
      const others = (f.active_alerts || []).filter((a) => a !== f.state).map(fsmName);
      let detail = others.length ? `Also active: ${others.join(", ")}. ` : "";
      if (f.advisory && f.advisory !== "Normal monitoring.") detail += f.advisory;
      if (!detail) detail = level === "normal" ? "No alarms from the alarm engine."
        : "Reported by the alarm engine (fsm.py). Check the baby and the incubator.";
      $("overview").dataset.level = level;
      setText("overall-text", fsmName(f.state));
      setText("overall-detail", detail);
      return;
    }
    const crit = list.filter((a) => a.level === "critical").length;
    const warn = list.length - crit;
    let level, title, detail;
    if (state.broker !== "connected" && state.lastSensorAt === 0) {
      level = "idle";
      title = state.broker === "connecting" ? "Connecting" : "Not connected";
      detail = state.broker === "connecting"
        ? "Connecting to the broker on the Raspberry Pi."
        : "The dashboard cannot reach the broker. Readings below are not live.";
    } else if (crit) {
      level = "critical"; title = "Needs attention now";
      detail = `${crit} critical alarm${crit > 1 ? "s" : ""}${warn ? ` and ${warn} warning${warn > 1 ? "s" : ""}` : ""}. Check the baby and the incubator.`;
    } else if (warn) {
      level = "warning"; title = "Check the system";
      detail = list.slice(0, 2).map((a) => a.text).join(". ") + (warn > 2 ? `. And ${warn - 2} more.` : ".");
    } else if (state.lastSensorAt === 0) {
      level = "idle"; title = "Waiting for data";
      detail = "Connected. No reading from the sensor node yet.";
    } else {
      level = "normal"; title = "All readings within limits";
      detail = "No alarms. Temperature and humidity are inside the configured limits.";
    }
    $("overview").dataset.level = level;
    setText("overall-text", title);
    setText("overall-detail", detail);
  }

  // ------------------------------------------------------------------
  //  Range tracks: where a reading sits against its alarm limits
  // ------------------------------------------------------------------
  function initRange(id, lim, dec = 1) {
    const el = $(id);
    if (!el || !lim) return;
    const span = lim.max - lim.min, lo = lim.min - span * 0.3, hi = lim.max + span * 0.3;
    el._scale = { lo, hi };
    const band = el.querySelector(".range-band");
    band.style.left = `${((lim.min - lo) / (hi - lo)) * 100}%`;
    band.style.right = `${((hi - lim.max) / (hi - lo)) * 100}%`;
    const loEl = el.querySelector(".lo"), hiEl = el.querySelector(".hi");
    loEl.textContent = fmt(lim.min, dec); loEl.style.left = band.style.left;
    hiEl.textContent = fmt(lim.max, dec); hiEl.style.left = `${((lim.max - lo) / (hi - lo)) * 100}%`;
  }
  function setRange(id, value) {
    const el = $(id);
    if (!el || !el._scale) return;
    const mark = el.querySelector(".range-mark");
    if (value === null || value === undefined) { mark.hidden = true; return; }
    const { lo, hi } = el._scale;
    const pct = Math.min(100, Math.max(0, ((value - lo) / (hi - lo)) * 100));
    mark.style.left = `${pct}%`;
    mark.hidden = false;
  }
  initRange("range-temp", CFG.limits.temp);
  initRange("range-hum", CFG.limits.hum, 0);
  initRange("range-bpm", CFG.heartRate.normal, 0);
  initRange("range-skin", CFG.limits.skin);

  function checkRange(key, label, value, lim, unit, tileId, flagId, dec = 1) {
    const has = value !== null && value !== undefined;
    const low = has && value < lim.min, high = has && value > lim.max;
    $(tileId)?.classList.toggle("is-critical", low || high);
    if (flagId) {
      show(flagId, low || high);
      setText(flagId, low ? "Below limit" : high ? "Above limit" : "");
    }
    setAlarm(key, low || high, "critical",
      `${label}: ${fmt(value, dec)} ${unit} (limit ${lim.min} to ${lim.max} ${unit})`);
  }

  // ------------------------------------------------------------------
  //  Charts
  // ------------------------------------------------------------------
  const HIST_MS = CFG.historyMinutes * 60 * 1000;

  const crosshair = {
    id: "crosshair",
    afterDraw(chart) {
      const a = chart.tooltip?.getActiveElements?.();
      if (!a || !a.length) return;
      const x = a[0].element.x, { top, bottom } = chart.chartArea, ctx = chart.ctx;
      ctx.save(); ctx.strokeStyle = css("--axis"); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
    },
  };

  function makeChart(canvasId, series, yOpts = {}) {
    const canvas = $(canvasId);
    if (!canvas || !window.Chart) return null;
    const ds = series.map((s) => ({
      label: s.label, data: [], borderWidth: 2, pointRadius: 0, pointHoverRadius: 4,
      tension: 0.25, spanGaps: false, _colorVar: s.color,
    }));
    const chart = new Chart(canvas, {
      type: "line",
      data: { datasets: ds },
      plugins: [crosshair],
      options: {
        animation: false, responsive: true, maintainAspectRatio: false, parsing: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { type: "linear", ticks: { maxTicksLimit: 6, callback: (v) => hhmmss(v).slice(0, 5) } },
          y: { ...yOpts },
        },
        plugins: {
          legend: { display: series.length > 1, position: "top", align: "end",
                    labels: { boxWidth: 14, boxHeight: 2, usePointStyle: false } },
          tooltip: {
            callbacks: {
              title: (items) => (items.length ? hhmmss(items[0].parsed.x) : ""),
              label: (it) => ` ${it.dataset.label}: ${fmt(it.parsed.y, yOpts._decimals ?? 1)}`,
            },
          },
        },
      },
    });
    chart._empty = canvas.parentElement.querySelector(".chart-empty");
    return chart;
  }

  const charts = {};
  function initCharts() {
    if (!window.Chart) {
      document.querySelectorAll(".chart-empty").forEach((p) => {
        p.textContent = "Charts are unavailable: Chart.js did not load. Run setup_pi.sh again or connect this device to the internet once.";
      });
      return;
    }
    Chart.defaults.font.family = css("--font");
    Chart.defaults.font.size = 12;
    charts.temp = makeChart("chart-temp", [
      { label: "NTC", color: "--series-1" },
      { label: "SHT31", color: "--series-2" },
      { label: "DHT11", color: "--series-3" },
    ], { suggestedMin: 30, suggestedMax: 38 });
    charts.hum = makeChart("chart-hum", [
      { label: "SHT31", color: "--series-2" },   // colour follows the sensor, the same on every chart
      { label: "DHT11", color: "--series-3" },
    ], { suggestedMin: 30, suggestedMax: 90 });
    charts.bpm = makeChart("chart-bpm", [{ label: "BPM", color: "--series-1" }],
      { suggestedMin: 80, suggestedMax: 200, _decimals: 0 });
    charts.weight = makeChart("chart-weight", [
      { label: "Total", color: "--series-1" },
      { label: "Left", color: "--series-2" },
      { label: "Right", color: "--series-3" },
    ], { _decimals: 0 });
    charts.cry = makeChart("chart-cry", [{ label: "Crying probability", color: "--series-1" }],
      { min: 0, max: 1, _decimals: 2 });

    for (const c of Object.values(charts)) {
      if (!c) continue;
      c.data.datasets.forEach((d) => { d.borderColor = css(d._colorVar); d.backgroundColor = css(d._colorVar); });
      for (const ax of Object.values(c.options.scales)) {
        ax.grid = { color: css("--grid") };
        ax.border = { color: css("--axis") };
        ax.ticks.color = css("--text-muted");
      }
      c.options.plugins.legend.labels.color = css("--text-secondary");
      c.update("none");
    }
  }

  function push(chart, idx, t, y) {
    if (!chart) return;
    const d = chart.data.datasets[idx].data;
    d.push({ x: t, y: y === undefined ? null : y });
    while (d.length && d[0].x < t - HIST_MS) d.shift();
    if (chart._empty && y !== null && y !== undefined) { chart._empty.hidden = true; chart._empty = null; }
  }

  let dirty = false;
  setInterval(() => {
    if (!dirty) return;
    dirty = false;
    const now = Date.now();
    for (const c of Object.values(charts)) {
      if (!c) continue;
      c.options.scales.x.min = now - HIST_MS; c.options.scales.x.max = now;
      c.update("none");
    }
  }, 1000);

  // ------------------------------------------------------------------
  //  Message handlers
  // ------------------------------------------------------------------
  // A card is never hidden: a sensor that stops reporting must stay visible as "no data".
  const NOT_SENT = "Not sent by the current sensor node firmware";
  const noData = (id, missing) => $(id)?.classList.toggle("no-data", !!missing);
  function chartNote(chart, text) {
    const el = $(chart)?.parentElement.querySelector(".chart-empty");
    if (el && text) { el.textContent = text; el.hidden = false; }
  }

  function onSensors(m) {
    const t = Date.now();
    state.lastSensorAt = t;
    setText("last-update", `Updated ${hhmmss(t)}`);
    const hasSht = "sht31" in m, hasDht = "dht11" in m, hasNtc = "ntc" in m, hasHeart = "heart" in m;
    const ntc = m.ntc?.temp ?? null;
    const shtT = m.sht31?.temp ?? null, shtH = m.sht31?.hum ?? null;
    const dhtT = m.dht11?.temp ?? null, dhtH = m.dht11?.hum ?? null;
    const bpm = m.heart?.bpm ?? null, contact = !!m.heart?.contact;

    // Primary sensor = SHT31; DHT11 is the fallback for the alarm and the big number
    const temp = shtT ?? dhtT, hum = shtH ?? dhtH;
    const airInfo = (primary, backup) => !hasSht && backup === null ? "No air sensor is reporting"
      : !hasSht ? "SHT31 not sent by the sensor node, showing the DHT11 backup"
      : primary === null && backup === null ? "SHT31 and DHT11 are not responding"
      : primary === null ? "SHT31 not responding, showing the DHT11 backup"
      : "Primary air sensor";

    // --- Air temperature & humidity (SHT31, DHT11 backup) -------------
    setText("v-sht-temp", fmt(temp)); setText("v-sht-hum", fmt(hum, 0));
    setText("src-temp", shtT === null && dhtT !== null ? "DHT11 (backup)" : "SHT31");
    setText("src-hum", shtH === null && dhtH !== null ? "DHT11 (backup)" : "SHT31");
    setText("v-temp-info", airInfo(shtT, dhtT)); setText("v-hum-info", airInfo(shtH, dhtH));
    setRange("range-temp", temp); setRange("range-hum", hum);
    noData("tile-temp", temp === null); noData("tile-hum", hum === null);

    // --- DHT11 (own card) ---------------------------------------------
    setText("v-dht-temp", fmt(dhtT)); setText("v-dht-hum", fmt(dhtH, 0));
    setText("v-dht-info", !hasDht ? NOT_SENT
      : dhtT === null ? "DHT11 not responding"
      : "Cross-check for the SHT31 (lower accuracy: ±2 °C, ±5 %RH)");
    noData("tile-dht", dhtT === null);

    // --- Skin temperature (NTC) ---------------------------------------
    setText("v-ntc-temp", fmt(ntc));
    setRange("range-skin", ntc);
    setText("v-ntc-info", !hasNtc ? NOT_SENT : ntc === null ? "Probe not responding" : "Skin / mattress probe");
    noData("tile-ntc", ntc === null);

    push(charts.temp, 0, t, ntc); push(charts.temp, 1, t, shtT); push(charts.temp, 2, t, dhtT);
    push(charts.hum, 0, t, shtH); push(charts.hum, 1, t, dhtH);

    // --- Heart rate ---------------------------------------------------
    setText("v-bpm", fmt(hasHeart ? bpm : null, 0));
    setRange("range-bpm", hasHeart && contact ? bpm : null);
    setText("v-contact", !hasHeart ? NOT_SENT
      : m.heart.sensor_ok === false ? "Sensor not detected"
      : contact ? (bpm === null ? "Sensor attached, measuring" : "Sensor attached")
      : "Sensor not attached to the baby");
    noData("tile-heart", !hasHeart || bpm === null);
    if (hasHeart) push(charts.bpm, 0, t, bpm);
    else chartNote("chart-bpm", "No heart rate data: the current sensor node firmware does not send it.");

    // --- Weight: format {g} or {total_g, left_g, right_g} ------------
    const w = m.weight;
    // Sensor not detected -> firmware still sends 0 g; show "--" instead of a fake 0
    const wOk = !!w && w.sensor_ok !== false;
    const wg = wOk ? (w.g ?? w.total_g ?? null) : null;
    setText("v-weight", fmt(wg, 0));
    if (w) {
      const info = [];
      if (w.sensor_ok === false) info.push("Sensor not detected");
      else if (w.calibrated === false) info.push("Not calibrated");
      if (wOk && ("left_g" in w || "right_g" in w))
        info.push(`Left ${fmt(w.left_g, 0)} g · Right ${fmt(w.right_g, 0)} g`);
      setText("v-weight-info", info.join(" · ") || "Load cell and HX711");
      push(charts.weight, 0, t, wg);
      push(charts.weight, 1, t, wOk ? (w.left_g ?? null) : null);
      push(charts.weight, 2, t, wOk ? (w.right_g ?? null) : null);
    } else {
      setText("v-weight-info", NOT_SENT);
      chartNote("chart-weight", "No weight data: the current sensor node firmware does not send it.");
    }
    noData("tile-weight", wg === null || w?.sensor_ok === false);

    // --- Power / battery ---------------------------------------------
    const pw = m.power;
    const socMin = CFG.limits.soc?.min ?? 30;
    const soc = pw ? (pw.soc_pct ?? null) : null;
    setText("v-soc", fmt(soc, 0));
    setText("soc-limit", `warning below ${socMin}%`);
    document.querySelector("#range-soc .range-fill").style.width =
      `${soc === null ? 0 : Math.min(100, Math.max(0, soc))}%`;
    $("range-soc").classList.toggle("low", soc !== null && soc < socMin);
    setText("v-power-src", !pw ? "--" : pw.mains_ok === false ? "on battery" : "mains");
    setText("v-power-info", !pw ? NOT_SENT : pw.mains_ok === false
      ? "Mains power disconnected. Running on battery."
      : "Mains power connected.");
    noData("tile-power", soc === null);

    // --- Local status computed by the ESP32 itself (shown on its OLED) -
    if (typeof m.fsm_state === "string" && m.fsm_state !== state.espLocal) {
      addEvent("Sensor node", state.espLocal === null ? `Local status: ${m.fsm_state}`
        : `Local status: ${state.espLocal} → ${m.fsm_state}`);
      state.espLocal = m.fsm_state;
    }
    // In-cabin buzzer on the ESP32: muted only with the physical button next to the incubator
    const muted = typeof m.alarm?.buzzer_muted === "boolean" ? m.alarm.buzzer_muted : null;
    if (muted !== null && muted !== state.espMuted) {
      if (state.espMuted !== null || muted)
        addEvent("Sensor node", muted ? "In-cabin buzzer muted with the button at the incubator"
                                      : "In-cabin buzzer active again");
      state.espMuted = muted;
    }
    // High heart-rate delay set in the firmware (DEMO_FAST_DELAY), so all three use the same number
    const d = m.alarm?.hr_high_delay_s;
    if (typeof d === "number" && d >= 5 && d <= 600 && d !== state.hrHighDelay) {
      addEvent("Sensor node", `High heart-rate delay set by the sensor node: ${d} s`);
      state.hrHighDelay = d;
    }
    dirty = true;

    // --- Alarms ------------------------------------------------------
    checkRange("temp", "Temperature", temp, CFG.limits.temp, "°C", "tile-temp", "flag-temp");
    checkRange("hum", "Humidity", hum, CFG.limits.hum, "%RH", "tile-hum", "flag-hum", 0);
    // --- CODE BLUE: skin temperature, outside the safe range -> immediately -------
    const sk = CFG.limits.skin;
    state.codeBlue = [];
    if (ntc !== null && (ntc < sk.min || ntc > sk.max))
      state.codeBlue.push(`Skin temperature ${fmt(ntc)} °C is ${ntc < sk.min ? "below" : "above"} the safe range ${sk.min}–${sk.max} °C`);
    checkRange("skin", "Code Blue, skin temperature", ntc, sk, "°C", "tile-ntc", "flag-skin");

    // --- Heart rate: Code Blue / warning only after the value stays abnormal -----
    evaluateHeartRate(hasHeart && contact ? bpm : null, t);
    setAlarm("hr-contact", hasHeart && m.heart.sensor_ok !== false && !contact, "warning",
      "Heart rate sensor is not attached to the baby");
    setAlarm("sht-fail", shtT === null, "warning",
      dhtT === null ? "SHT31: no data, and the DHT11 backup is not reporting either"
                    : "SHT31: no data, showing the DHT11 backup reading");
    setAlarm("weight-fail", !!w && w.sensor_ok === false, "warning",
      "Weight sensor (HX711): not returning data");
    if (pw) {
      setAlarm("mains", pw.mains_ok === false, "warning",
        "Mains power: disconnected, system running on battery");
      // 5% hysteresis so the alarm does not flicker around the threshold
      const soc = pw.soc_pct ?? null;
      const low = soc !== null && soc < (state.alarms.has("soc-low") ? socMin + 5 : socMin);
      setAlarm("soc-low", low, "warning", `Battery: ${fmt(soc, 0)}%, below ${socMin}%`);
    }
  }

  // Heart rate rules (CFG.heartRate):
  //   < codeBlue.below for delaySec.low          -> Code Blue "too slow"
  //   > codeBlue.above for delaySec.high         -> Code Blue "too fast" (crying does NOT cancel it)
  //   normal.max .. codeBlue.above, not crying,
  //     for delaySec.high                         -> Warning
  //   same band while the baby is crying          -> normal, with a note (cry detection only explains,
  //                                                  it never triggers or clears an alarm)
  //   delaySec.high is replaced by alarm.hr_high_delay_s when the ESP32 sends it
  function evaluateHeartRate(hr, now) {
    const c = CFG.heartRate, since = state.hrSince;
    const highSec = state.hrHighDelay ?? c.delaySec.high;   // from the ESP32, else config.js
    const low = hr !== null && hr < c.codeBlue.below;
    const high = hr !== null && hr > c.codeBlue.above;
    const elevated = hr !== null && !high && hr > c.normal.max;
    const elevatedAlone = elevated && !(state.crying && hr <= c.cryingMax);
    // start or reset each timer
    since.low = low ? (since.low ?? now) : null;
    since.high = high ? (since.high ?? now) : null;
    since.elevated = elevatedAlone ? (since.elevated ?? now) : null;
    // 1 s tolerance: messages arrive every 5 s with a little jitter (10 s = the 3rd message)
    const held = (key, sec) => since[key] !== null && now - since[key] >= sec * 1000 - 1000;
    const lowCB = held("low", c.delaySec.low);
    const highCB = held("high", highSec);
    const warn = held("elevated", highSec);

    if (lowCB) state.codeBlue.push(`Heart rate ${fmt(hr, 0)} BPM is too slow (Code Blue below ${c.codeBlue.below} BPM)`);
    if (highCB) state.codeBlue.push(`Heart rate ${fmt(hr, 0)} BPM is too fast (Code Blue above ${c.codeBlue.above} BPM)`);
    setAlarm("bpm", lowCB || highCB, "critical",
      `Code Blue, heart rate: ${fmt(hr, 0)} BPM (${lowCB ? "below " + c.codeBlue.below : "above " + c.codeBlue.above} BPM)`);
    setAlarm("bpm-high", warn, "warning",
      `Heart rate: ${fmt(hr, 0)} BPM, above ${c.normal.max} BPM for more than ${highSec} s`);

    // card: flag, colour and a line that says what is happening
    const tile = $("tile-heart");
    tile?.classList.toggle("is-critical", lowCB || highCB);
    tile?.classList.toggle("is-warning", warn && !(lowCB || highCB));
    const flag = lowCB ? "Too slow" : highCB ? "Too fast" : warn ? "Above normal" : "";
    show("flag-bpm", !!flag); setText("flag-bpm", flag);
    const left = (key, sec) => Math.max(0, Math.ceil(sec - (now - since[key]) / 1000));
    let note = null;
    if (low && !lowCB) note = `Below ${c.codeBlue.below} BPM, Code Blue in ${left("low", c.delaySec.low)} s if it stays`;
    else if (high && !highCB) note = `Above ${c.codeBlue.above} BPM, Code Blue in ${left("high", highSec)} s if it stays`;
    else if (elevated && !elevatedAlone) note = "Above normal, probably because the baby is crying";
    else if (elevatedAlone && !warn) note = `Above normal, warning in ${left("elevated", highSec)} s if it stays`;
    if (note) setText("v-contact", note);
  }

  // FSM decision from fsm.py: every state change is written to the event log

  function onFsm(m) {
    if (!m || typeof m.state !== "string") return;
    const prev = state.fsm ? state.fsm.state : null;
    state.fsm = m;
    // Arrival time, not m.ts: the Pi clock can be wrong when it has no internet.
    // fsm.py republishes every 5 s, so a stopped fsm.py is noticed within fsmTimeoutSec.
    state.lastFsmAt = Date.now();
    if (m.state !== prev) {
      addEvent("Alarm engine", prev === null ? `Current state: ${fsmName(m.state)}`
        : `${fsmName(prev)} → ${fsmName(m.state)}`);
    }
    refreshStatus();
  }

  // Cry reason from the stage-2 model (cry_service.py two_stage / audio_detector.py)
  const REASON_TEXT = {
    hungry: "Hungry",
    belly_pain: "Belly pain",
    burping: "Needs to burp",
    discomfort: "Discomfort",
    tired: "Tired or sleepy",
  };

  function showReason(m) {
    const has = state.crying && m && m.reason && m.reason !== "none";
    show("cry-reason", has);
    if (!has) return;
    setText("cry-reason-label", REASON_TEXT[m.reason] ?? m.reason);
    setText("cry-reason-conf", m.reason_conf != null ? `(${Math.round(m.reason_conf * 100)}%)` : "");
  }

  function onCryResult(m) {
    const t = Date.now();
    state.lastCryAt = t;
    const p = m.cry_prob ?? 0;
    setText("cry-prob", `Crying probability ${Math.round(p * 100)}%`);
    setText("cry-level", `Sound level ${fmt(m.level_db, 0)} dB`);
    $("cry-bar").style.width = `${Math.round(p * 100)}%`;
    $("cry-meter").setAttribute("aria-valuenow", String(Math.round(p * 100)));
    if (!state.crying) setText("cry-label", m.label === "silence" ? "Quiet" : "Not crying");
    if ("reason" in m) showReason(m);
    if (typeof m.source === "string") {
      setText("cry-source", m.source.startsWith("replay:") ? `test file ${m.source.slice(7)}` : "microphone");
    }
    push(charts.cry, 0, t, p);
    dirty = true;
  }

  function onCryState(m) {
    state.crying = !!m.crying;
    $("tile-cry").classList.toggle("crying", state.crying);
    setText("cry-label", state.crying ? "Crying" : "Not crying");
    setText("cry-since", state.crying && m.since ? `Crying since ${hhmmss(m.since * 1000)}` : "");
    const reasonTxt = m.reason && m.reason !== "none"
      ? `, possibly ${(REASON_TEXT[m.reason] ?? m.reason).toLowerCase()}` : "";
    setAlarm("cry", state.crying, "critical",
      `Baby crying: detected with ${Math.round((m.cry_prob ?? 0) * 100)}% probability${reasonTxt}`);
    showReason(m);
  }

  // ------------------------------------------------------------------
  //  Online / offline status
  // ------------------------------------------------------------------
  function refreshStatus() {
    const now = Date.now(), to = CFG.sensorTimeoutSec * 1000;
    const espFresh = now - state.lastSensorAt < to;
    const local = !espFresh ? "" : (state.espLocal ? ` · local ${state.espLocal}` : "")
      + (state.espMuted ? " · buzzer muted" : "");
    if (state.esp32 === "online" && espFresh) setPill("pill-esp32", "ok", "sending data" + local);
    else if (state.esp32 === "online") setPill("pill-esp32", "warn", "online, no data");
    else if (state.esp32 === "unknown") setPill("pill-esp32", espFresh ? "ok" : null, espFresh ? "sending data" + local : "waiting");
    else setPill("pill-esp32", "bad", state.esp32);

    const cs = state.cryService;
    if (cs === "online") setPill("pill-cry", now - state.lastCryAt < to ? "ok" : "warn",
      now - state.lastCryAt < to ? "listening" : "online, no audio");
    else if (cs === "no_mic") setPill("pill-cry", "warn", "no microphone");
    else if (cs === "unknown") setPill("pill-cry", null, "waiting");
    else setPill("pill-cry", "bad", cs);

    if (state.fsm === null) setPill("pill-fsm", null, "waiting");
    else if (!fsmFresh()) setPill("pill-fsm", "bad", "not running");
    else setPill("pill-fsm", "ok", "running");   // its decision is shown in the status panel
    setAlarm("fsm-down", state.broker === "connected" && state.fsm !== null && !fsmFresh(), "warning",
      "Alarm engine (fsm.py): not reporting, status below is computed by this browser");
    renderOverall();

    // Grace period after page load: the first reading can take up to one publish interval
    const espLate = state.lastSensorAt > 0 || now - pageStart > to;
    setAlarm("esp-offline", state.esp32 !== "unknown" && !espFresh && espLate, "warning",
      `Sensor node: no data for more than ${CFG.sensorTimeoutSec} s`);
    setAlarm("mic", cs === "no_mic", "warning", "Cry detection: no microphone connected");
  }
  const pageStart = Date.now();
  setInterval(refreshStatus, 1000);
  const tick = () => setText("clock", hhmmss(Date.now()));
  tick(); setInterval(tick, 1000);

  // ------------------------------------------------------------------
  //  MQTT
  // ------------------------------------------------------------------
  let client = null;
  function setBroker(s, cls, text) { state.broker = s; setPill("pill-broker", cls, text); renderOverall(); }

  function connect() {
    if (!window.mqtt) {
      setBroker("error", "bad", "MQTT library not loaded");
      setText("overall-detail", "The MQTT library did not load. Run setup_pi.sh again, or connect this device to the internet once.");
      return;
    }
    client = mqtt.connect(CFG.mqttUrl, {
      username: creds.username, password: creds.password,
      clientId: "dashboard-" + Math.random().toString(16).slice(2, 10),
      reconnectPeriod: 3000, connectTimeout: 8000, clean: true,
    });
    client.on("connect", () => {
      setBroker("connected", "ok", "connected");
      client.subscribe(Object.values(T), { qos: 0 });
    });
    client.on("reconnect", () => setBroker("reconnecting", "warn", "reconnecting"));
    client.on("close", () => { if (state.broker !== "error") setBroker("disconnected", "bad", "disconnected"); });
    client.on("error", (e) => {
      const code = e && e.code;
      if (code === 4 || code === 5 || code === 134 || code === 135) {
        // Account rejected (password changed or removed): back to sign-in
        client.end(true);
        Auth.clear();
        location.replace("login.html?reason=auth");
        return;
      }
      setBroker("error", "bad", e?.message || "error");
    });

    client.on("message", (topic, payload) => {
      const s = payload.toString();
      try {
        switch (topic) {
          case T.sensors:   onSensors(JSON.parse(s)); break;
          case T.cryResult: onCryResult(JSON.parse(s)); break;
          case T.cryState:  onCryState(JSON.parse(s)); break;
          case T.esp32:     state.esp32 = s; break;
          case T.cryStatus: state.cryService = s; break;
          case T.fsm:       onFsm(JSON.parse(s)); break;
        }
      } catch (e) { console.warn("Invalid message on", topic, e); }
    });
  }

  $("sign-out").addEventListener("click", () => {
    try { client?.end(true); } catch { /* already closed */ }
    Auth.clear();
    location.replace("login.html?reason=signedout");
  });

  // ------------------------------------------------------------------
  //  Camera (camera_service.py behind nginx at /camera/, password-protected)
  // ------------------------------------------------------------------
  function initCamera() {
    const CAM = "camera/";
    const ui = {
      toggle: $("cam-toggle"), record: $("cam-record"), view: $("cam-view"), img: $("cam-img"),
      msg: $("cam-msg"), status: $("cam-status"), badge: $("cam-rec-badge"), recTime: $("cam-rec-time"),
      recs: $("cam-recs"), list: $("cam-rec-list"), count: $("cam-rec-count"), empty: $("cam-rec-empty"),
      player: $("cam-player"), video: $("cam-video"), playerName: $("cam-player-name"),
    };
    if (!ui.toggle) return;
    const cam = { watching: false, poller: null, rec: null, recAt: 0, busy: false };

    const mmss = (s) => {
      s = Math.max(0, Math.floor(s));
      const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
      return (h ? `${h}:${String(m).padStart(2, "0")}` : String(m).padStart(2, "0")) + ":" + String(r).padStart(2, "0");
    };
    const mb = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : `${(b / 1e6).toFixed(1)} MB`);

    async function api(path, opts = {}) {
      let r;
      try { r = await fetch(CAM + path, { cache: "no-store", ...opts }); }
      catch { throw new Error("cannot reach the Raspberry Pi"); }
      let body = {};
      try { body = await r.json(); } catch { /* not JSON */ }
      if (!r.ok) {
        const hint = r.status === 401 ? "wrong camera username or password"
          : (r.status === 502 || r.status === 504) ? "the camera service is not running"
          : r.status === 404 && !body.error ? "the camera is not set up on this Pi (run setup_camera.sh)"
          : body.error || `HTTP ${r.status}`;
        throw new Error(hint);
      }
      return body;
    }

    function render(st) {
      const c = st.camera, r = st.recording;
      cam.rec = r.active ? r : null;
      cam.recAt = Date.now();
      ui.record.disabled = !c.ok && !r.active;
      ui.record.classList.toggle("active", r.active);
      ui.record.textContent = r.active ? "Stop recording" : "Start recording";
      ui.badge.hidden = !r.active;
      if (c.ok) {
        const size = c.size ? `${c.size[0]}×${c.size[1]}` : "";
        let txt = `${c.camera || c.backend} · ${size} · ${c.fps} fps · ${c.viewers} viewer${c.viewers === 1 ? "" : "s"}`;
        txt += ` · ${mb(r.free_mb * 1e6)} free`;
        if (r.error) txt += ` · last recording error: ${r.error}`;
        ui.status.textContent = txt;
      } else {
        ui.status.textContent = `Camera not available: ${c.error || "starting"}`;
      }
      tickRec();
    }

    function tickRec() {
      if (!cam.rec) return;
      const el = cam.rec.elapsed_s + (Date.now() - cam.recAt) / 1000;
      ui.recTime.textContent = mmss(el) + (cam.rec.max_s ? ` / ${mmss(cam.rec.max_s)}` : "");
    }
    setInterval(tickRec, 1000);

    async function poll() {
      try { render(await api("api/status")); }
      catch (e) {
        ui.status.textContent = `Camera: ${e.message}`;
        ui.record.disabled = true;
      }
    }
    function startPolling() {
      if (cam.poller) return;
      poll();
      cam.poller = setInterval(poll, 2000);
    }

    // ---- live view ----
    function setStream(on) {
      if (on) {
        ui.msg.textContent = "Connecting to the camera…";
        ui.img.src = CAM + "stream.mjpg?t=" + Date.now();
      } else {
        ui.img.removeAttribute("src");   // closes the MJPEG connection
      }
    }
    ui.img.addEventListener("load", () => { ui.msg.textContent = ""; });
    ui.img.addEventListener("error", () => {
      if (!cam.watching) return;
      ui.msg.textContent = "Live view unavailable. Retrying in 3 seconds.";
      setTimeout(() => { if (cam.watching && !document.hidden) setStream(true); }, 3000);
    });
    ui.toggle.addEventListener("click", () => {
      cam.watching = !cam.watching;
      ui.toggle.setAttribute("aria-pressed", String(cam.watching));
      ui.toggle.textContent = cam.watching ? "Stop live view" : "Watch live";
      ui.view.hidden = !cam.watching;
      setStream(cam.watching);
      startPolling();
    });
    // Pause the stream while the tab is hidden (saves Wi-Fi bandwidth and Pi CPU).
    document.addEventListener("visibilitychange", () => {
      if (cam.watching) setStream(!document.hidden);
    });

    // ---- recording ----
    ui.record.addEventListener("click", async () => {
      if (cam.busy) return;
      cam.busy = true; ui.record.disabled = true;
      startPolling();
      try {
        if (cam.rec) {
          const r = await api("api/record/stop", { method: "POST" });
          addEvent("Camera", r.file ? `Recording saved: ${r.file}` : "Recording stopped");
          if (ui.recs.open) loadRecordings();
        } else {
          const r = await api("api/record/start", { method: "POST" });
          addEvent("Camera", `Recording started: ${r.file}`);
        }
      } catch (e) {
        addEvent("Camera", `Recording failed: ${e.message}`);
        ui.status.textContent = `Camera: ${e.message}`;
      } finally {
        cam.busy = false;
        await poll();
      }
    });

    // ---- recordings list & player ----
    function btn(label, cls, aria, onClick) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn small " + cls; b.textContent = label;
      b.setAttribute("aria-label", aria);
      b.addEventListener("click", onClick);
      return b;
    }
    async function loadRecordings() {
      ui.count.textContent = "loading";
      try {
        const { recordings } = await api("api/recordings");
        ui.count.textContent = String(recordings.length);
        ui.empty.hidden = recordings.length > 0;
        ui.list.replaceChildren(...recordings.map((r) => {
          const tr = document.createElement("tr");
          const tdT = document.createElement("td"); tdT.textContent = r.mtime;
          const tdS = document.createElement("td"); tdS.textContent = mb(r.size);
          const tdA = document.createElement("td");
          const url = CAM + "recordings/" + encodeURIComponent(r.name);
          const dl = document.createElement("a");
          dl.className = "btn small"; dl.href = url + "?download=1"; dl.textContent = "Download";
          dl.setAttribute("download", r.name);
          dl.setAttribute("aria-label", `Download recording from ${r.mtime}`);
          tdA.append(
            btn("Play", "", `Play recording from ${r.mtime}`, () => {
              ui.player.hidden = false; ui.playerName.textContent = `${r.name} · ${r.mtime}`;
              ui.video.src = url; ui.video.play().catch(() => {});
              ui.player.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }),
            dl,
            btn("Delete", "danger", `Delete recording from ${r.mtime}`, async () => {
              if (!confirm(`Delete the recording from ${r.mtime}? This cannot be undone.`)) return;
              try {
                await api("api/recordings/" + encodeURIComponent(r.name), { method: "DELETE" });
                if (ui.video.src.endsWith(encodeURIComponent(r.name))) closePlayer();
                addEvent("Camera", `Recording deleted: ${r.name}`);
              } catch (e) { alert(`Could not delete: ${e.message}`); }
              loadRecordings();
            }),
          );
          tr.append(tdT, tdS, tdA);
          return tr;
        }));
      } catch (e) {
        ui.count.textContent = "unavailable";
        ui.list.replaceChildren();
        ui.empty.hidden = false;
        ui.empty.textContent = `Could not load recordings: ${e.message}.`;
      }
    }
    function closePlayer() {
      ui.video.pause(); ui.video.removeAttribute("src"); ui.video.load();
      ui.player.hidden = true;
    }
    $("cam-player-close").addEventListener("click", closePlayer);
    ui.recs.addEventListener("toggle", () => {
      if (ui.recs.open) { loadRecordings(); startPolling(); }
    });
  }

  initCharts();
  renderAlarms();
  connect();
  try { initCamera(); } catch (e) { console.warn("Camera panel disabled:", e); }
})();
