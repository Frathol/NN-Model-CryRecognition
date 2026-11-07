/* Smart Incubator dashboard — MQTT over WebSockets + Chart.js */
(() => {
  "use strict";
  const CFG = window.DASHBOARD_CONFIG;
  const T = CFG.topics;
  const $ = (id) => document.getElementById(id);
  const root = document.querySelector(".viz-root");
  const css = (v) => getComputedStyle(root).getPropertyValue(v).trim();

  const state = {
    lastSensorAt: 0,
    lastCryAt: 0,
    esp32: "unknown",
    cryService: "unknown",
    crying: false,
    alarms: new Map(),     // key -> {level, text}
  };

  // ------------------------------------------------------------------
  //  Format & util
  // ------------------------------------------------------------------
  const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
  const hhmmss = (t) => new Date(t).toLocaleTimeString("en-GB", { hour12: false });
  const setText = (id, txt) => { const el = $(id); if (el) el.textContent = txt; };

  function setPill(id, cls, text) {
    const el = $(id);
    el.classList.remove("ok", "warn", "bad");
    if (cls) el.classList.add(cls);
    el.querySelector(".txt").textContent = text;
  }

  function addEvent(kind, detail) {
    const tr = document.createElement("tr");
    [hhmmss(Date.now()), kind, detail].forEach((t) => {
      const td = document.createElement("td"); td.textContent = t; tr.appendChild(td);
    });
    const tb = $("events");
    tb.insertBefore(tr, tb.firstChild);
    while (tb.children.length > 50) tb.removeChild(tb.lastChild);
  }

  function setAlarm(key, active, level, text) {
    const had = state.alarms.has(key);
    if (active) {
      state.alarms.set(key, { level, text });
      if (!had) addEvent(level === "critical" ? "ALARM" : "Warning", text);
    } else if (had) {
      state.alarms.delete(key);
      addEvent("Normal", text.split(":")[0] + " back to normal");
    }
    renderAlarms();
  }

  function renderAlarms() {
    const box = $("alarms");
    box.replaceChildren();
    for (const { level, text } of state.alarms.values()) {
      const div = document.createElement("div");
      div.className = "alarm " + level;
      const icon = document.createElement("span");
      icon.className = "icon"; icon.textContent = level === "critical" ? "⛔" : "⚠️";
      const lbl = document.createElement("strong");
      lbl.textContent = level === "critical" ? "Critical" : "Warning";
      const txt = document.createElement("span"); txt.textContent = text;
      div.append(icon, lbl, txt);
      box.appendChild(div);
    }
  }

  function checkRange(key, label, value, lim, unit, tileEl, dec = 1) {
    const bad = value !== null && value !== undefined && (value < lim.min || value > lim.max);
    if (tileEl) tileEl.classList.toggle("out-of-range", bad);
    setAlarm(key, bad, "critical",
      `${label}: ${fmt(value, dec)} ${unit} (limit ${lim.min}–${lim.max} ${unit})`);
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
    const ds = series.map((s) => ({
      label: s.label, data: [], borderWidth: 2, pointRadius: 0, pointHoverRadius: 4,
      tension: 0.25, spanGaps: false, _colorVar: s.color,
    }));
    return new Chart($(canvasId), {
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
              label: (it) => ` ${fmt(it.parsed.y, yOpts._decimals ?? 1)}  ${it.dataset.label}`,
            },
          },
        },
      },
    });
  }

  let charts = {};
  function initCharts() {
    charts.temp = makeChart("chart-temp", [
      { label: "NTC", color: "--series-1" },
      { label: "SHT31", color: "--series-2" },
      { label: "DHT11", color: "--series-3" },
    ], { suggestedMin: 30, suggestedMax: 38 });
    charts.hum = makeChart("chart-hum", [
      { label: "SHT31", color: "--series-2" },   // colour follows the sensor, consistent across charts
      { label: "DHT11", color: "--series-3" },
    ], { suggestedMin: 30, suggestedMax: 90 });
    charts.bpm = makeChart("chart-bpm", [{ label: "BPM", color: "--series-1" }],
      { suggestedMin: 80, suggestedMax: 200, _decimals: 0 });
    charts.cry = makeChart("chart-cry", [{ label: "P(cry)", color: "--series-1" }],
      { min: 0, max: 1, _decimals: 2 });
    applyTheme();
  }

  function applyTheme() {
    Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
    Chart.defaults.font.size = 11;
    for (const c of Object.values(charts)) {
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
    const d = chart.data.datasets[idx].data;
    d.push({ x: t, y: y === undefined ? null : y });
    while (d.length && d[0].x < t - HIST_MS) d.shift();
  }

  let dirty = false;
  setInterval(() => {
    if (!dirty) return;
    dirty = false;
    const now = Date.now();
    for (const c of Object.values(charts)) {
      c.options.scales.x.min = now - HIST_MS; c.options.scales.x.max = now;
      c.update("none");
    }
  }, 1000);

  // ------------------------------------------------------------------
  //  Message handlers
  // ------------------------------------------------------------------
  function onSensors(m) {
    const t = Date.now();
    state.lastSensorAt = t;
    const ntc = m.ntc?.temp ?? null;
    const shtT = m.sht31?.temp ?? null, shtH = m.sht31?.hum ?? null;
    const dhtT = m.dht11?.temp ?? null, dhtH = m.dht11?.hum ?? null;
    const bpm = m.heart?.bpm ?? null, contact = !!m.heart?.contact;

    setText("v-ntc-temp", fmt(ntc));
    setText("v-sht-temp", fmt(shtT)); setText("v-sht-hum", fmt(shtH, 0));
    setText("v-dht-temp", fmt(dhtT)); setText("v-dht-hum", fmt(dhtH, 0));
    setText("v-bpm", fmt(bpm, 0));
    setText("v-contact", m.heart?.sensor_ok === false ? "Sensor: not detected"
      : contact ? (bpm === null ? "Sensor attached · measuring…" : "Sensor attached")
      : "Sensor not attached");

    push(charts.temp, 0, t, ntc); push(charts.temp, 1, t, shtT); push(charts.temp, 2, t, dhtT);
    push(charts.hum, 0, t, shtH); push(charts.hum, 1, t, dhtH);
    push(charts.bpm, 0, t, bpm);
    dirty = true;

    // Primary temperature/humidity sensor = SHT31; DHT11 as fallback
    const temp = shtT ?? dhtT, hum = shtH ?? dhtH;
    checkRange("temp", "Temperature", temp, CFG.limits.temp, "°C", $("v-sht-temp").closest(".tile"));
    checkRange("hum", "Humidity", hum, CFG.limits.hum, "%RH", null);
    if (contact) checkRange("bpm", "Heart rate", bpm, CFG.limits.bpm, "BPM", $("v-bpm").closest(".tile"), 0);
    else checkRange("bpm", "Heart rate", null, CFG.limits.bpm, "BPM", $("v-bpm").closest(".tile"));
    setAlarm("hr-contact", m.heart?.sensor_ok !== false && !contact, "warning",
      "Heart rate sensor is not attached to the baby");
    setAlarm("sht-fail", shtT === null, "warning", "SHT31 is not returning data");
  }

  // Cry reason from the stage-2 model (sent only by audio_detector.py)
  const REASON_TEXT = {
    hungry: "Hungry",
    belly_pain: "Belly pain",
    burping: "Needs to burp",
    discomfort: "Discomfort",
    tired: "Tired / sleepy",
  };

  function showReason(m) {
    const has = state.crying && m && m.reason && m.reason !== "none";
    $("cry-reason").hidden = !has;
    if (!has) return;
    setText("cry-reason-label", REASON_TEXT[m.reason] ?? m.reason);
    setText("cry-reason-conf", m.reason_conf != null ? `(${Math.round(m.reason_conf * 100)}%)` : "");
  }

  function onCryResult(m) {
    const t = Date.now();
    state.lastCryAt = t;
    const p = m.cry_prob ?? 0;
    setText("cry-prob", `P(cry) ${fmt(p, 2)}`);
    setText("cry-level", `Level ${fmt(m.level_db, 0)} dB`);
    $("cry-bar").style.width = `${Math.round(p * 100)}%`;
    if (!state.crying) {
      setText("cry-label", m.label === "silence" ? "Calm (silent)" : "Calm");
      setText("cry-icon", "🙂");
    }
    if ("reason" in m) showReason(m);
    push(charts.cry, 0, t, p);
    dirty = true;
  }

  function onCryState(m) {
    const was = state.crying;
    state.crying = !!m.crying;
    $("tile-cry").classList.toggle("crying", state.crying);
    setText("cry-label", state.crying ? "BABY IS CRYING" : "Calm");
    setText("cry-icon", state.crying ? "😭" : "🙂");
    setText("cry-since", state.crying && m.since ? `Since ${hhmmss(m.since * 1000)}` : "");
    const reasonTxt = m.reason && m.reason !== "none"
      ? `, possibly ${(REASON_TEXT[m.reason] ?? m.reason).toLowerCase()}` : "";
    setAlarm("cry", state.crying, "critical", `Baby crying: detected (p=${fmt(m.cry_prob, 2)}${reasonTxt})`);
    showReason(m);
    if (was !== state.crying && !state.crying) setText("cry-since", "");
  }

  // ------------------------------------------------------------------
  //  Status online / offline
  // ------------------------------------------------------------------
  function refreshStatus() {
    const now = Date.now(), to = CFG.sensorTimeoutSec * 1000;
    const espFresh = now - state.lastSensorAt < to;
    if (state.esp32 === "online" && espFresh) setPill("pill-esp32", "ok", "ESP32: online");
    else if (state.esp32 === "online") setPill("pill-esp32", "warn", "ESP32: no data");
    else setPill("pill-esp32", "bad", `ESP32: ${state.esp32 === "unknown" ? "—" : state.esp32}`);

    const cs = state.cryService;
    if (cs === "online") setPill("pill-cry", now - state.lastCryAt < to ? "ok" : "warn",
      now - state.lastCryAt < to ? "Cry service: online" : "Cry service: waiting for audio");
    else if (cs === "no_mic") setPill("pill-cry", "warn", "Cry service: mic disconnected");
    else setPill("pill-cry", "bad", `Cry service: ${cs === "unknown" ? "—" : cs}`);

    setAlarm("esp-offline", state.esp32 !== "unknown" && !espFresh, "warning",
      "ESP32 sensor node is not sending data");
    setAlarm("mic", cs === "no_mic", "warning", "Bluetooth microphone is not connected");
  }
  setInterval(refreshStatus, 1000);
  setInterval(() => setText("clock", hhmmss(Date.now())), 1000);

  // ------------------------------------------------------------------
  //  MQTT
  // ------------------------------------------------------------------
  function connect() {
    const client = mqtt.connect(CFG.mqttUrl, {
      username: CFG.username, password: CFG.password,
      clientId: "dashboard-" + Math.random().toString(16).slice(2, 10),
      reconnectPeriod: 3000, connectTimeout: 8000, clean: true,
    });
    client.on("connect", () => {
      setPill("pill-broker", "ok", "Broker: connected");
      client.subscribe(Object.values(T), { qos: 0 });
    });
    client.on("reconnect", () => setPill("pill-broker", "warn", "Broker: reconnecting…"));
    client.on("close", () => setPill("pill-broker", "bad", "Broker: disconnected"));
    client.on("error", (e) => setPill("pill-broker", "bad", "Broker: " + (e?.message || "error")));

    client.on("message", (topic, payload) => {
      const s = payload.toString();
      try {
        switch (topic) {
          case T.sensors:   onSensors(JSON.parse(s)); break;
          case T.cryResult: onCryResult(JSON.parse(s)); break;
          case T.cryState:  onCryState(JSON.parse(s)); break;
          case T.esp32:     state.esp32 = s; break;
          case T.cryStatus: state.cryService = s; break;
        }
      } catch (e) { console.warn("Invalid message on", topic, e); }
    });
  }

  initCharts();
  connect();
})();
