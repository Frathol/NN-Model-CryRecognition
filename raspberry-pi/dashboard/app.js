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
    fsm: null,             // last fsm_state from ESP32
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

  // log=false: banner is still shown, but not written to event log (logged elsewhere)
  function setAlarm(key, active, level, text, log = true) {
    const had = state.alarms.has(key);
    if (active) {
      state.alarms.set(key, { level, text });
      if (!had && log) addEvent(level === "critical" ? "ALARM" : "Warning", text);
    } else if (had) {
      state.alarms.delete(key);
      if (log) addEvent("Normal", text.split(":")[0] + " back to normal");
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
    const canvas = $(canvasId);
    if (!canvas) {   // older index.html without this element -> skip without breaking dashboard
      console.warn(`#${canvasId} missing in index.html, skipping chart`);
      return null;
    }
    return new Chart(canvas, {
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
    charts.weight = makeChart("chart-weight", [
      { label: "Total", color: "--series-1" },
      { label: "Left", color: "--series-2" },
      { label: "Right", color: "--series-3" },
    ], { _decimals: 0 });
    charts.cry = makeChart("chart-cry", [{ label: "P(cry)", color: "--series-1" }],
      { min: 0, max: 1, _decimals: 2 });
    applyTheme();
  }

  function applyTheme() {
    Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
    Chart.defaults.font.size = 11;
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
  // Show / hide tiles & charts depending on fields actually sent by ESP32
  function show(id, visible) { const el = $(id); if (el) el.hidden = !visible; }

  function onSensors(m) {
    const t = Date.now();
    state.lastSensorAt = t;
    const hasNtc = "ntc" in m, hasHeart = "heart" in m;
    const ntc = m.ntc?.temp ?? null;
    const shtT = m.sht31?.temp ?? null, shtH = m.sht31?.hum ?? null;
    const dhtT = m.dht11?.temp ?? null, dhtH = m.dht11?.hum ?? null;
    const bpm = m.heart?.bpm ?? null, contact = !!m.heart?.contact;

    // --- Temperature & humidity --------------------------------------
    show("tile-ntc", hasNtc);
    setText("v-ntc-temp", fmt(ntc));
    setText("v-sht-temp", fmt(shtT)); setText("v-sht-hum", fmt(shtH, 0));
    setText("v-dht-temp", fmt(dhtT)); setText("v-dht-hum", fmt(dhtH, 0));
    push(charts.temp, 0, t, ntc); push(charts.temp, 1, t, shtT); push(charts.temp, 2, t, dhtT);
    push(charts.hum, 0, t, shtH); push(charts.hum, 1, t, dhtH);

    // --- Heart rate (only if firmware sends "heart") -----------------
    show("tile-heart", hasHeart); show("card-bpm", hasHeart);
    if (hasHeart) {
      setText("v-bpm", fmt(bpm, 0));
      setText("v-contact", m.heart.sensor_ok === false ? "Sensor: not detected"
        : contact ? (bpm === null ? "Sensor attached · measuring…" : "Sensor attached")
        : "Sensor not attached");
      push(charts.bpm, 0, t, bpm);
    }

    // --- Weight: format {g} or {total_g, left_g, right_g} ------------
    const w = m.weight;
    const wg = w ? (w.g ?? w.total_g ?? null) : null;
    show("tile-weight", !!w); show("card-weight", !!w);
    if (w) {
      setText("v-weight", fmt(wg, 0));
      const info = [];
      if (w.sensor_ok === false) info.push("Sensor: not detected");
      else if (w.calibrated === false) info.push("Not calibrated");
      if ("left_g" in w || "right_g" in w)
        info.push(`Left ${fmt(w.left_g, 0)} g · Right ${fmt(w.right_g, 0)} g`);
      setText("v-weight-info", info.join(" · ") || "Load cell + HX711");
      push(charts.weight, 0, t, wg);
      push(charts.weight, 1, t, w.left_g ?? null);
      push(charts.weight, 2, t, w.right_g ?? null);
    }

    // --- Power / battery ---------------------------------------------
    const pw = m.power;
    show("tile-power", !!pw);
    if (pw) {
      const soc = pw.soc_pct ?? null;
      setText("v-soc", fmt(soc, 0));
      setText("v-power-info",
        (pw.mains_ok === false ? "Mains power DISCONNECTED" : "Mains power OK")
        + (pw.status ? ` · ${pw.status}` : ""));
    }

    // --- FSM state from ESP32 ----------------------------------------
    if (typeof m.fsm_state === "string") onFsmState(m.fsm_state);
    dirty = true;

    // --- Alarms ------------------------------------------------------
    // Primary temperature/humidity sensor = SHT31; DHT11 as fallback
    const temp = shtT ?? dhtT, hum = shtH ?? dhtH;
    checkRange("temp", "Temperature", temp, CFG.limits.temp, "°C", $("v-sht-temp").closest(".tile"));
    checkRange("hum", "Humidity", hum, CFG.limits.hum, "%RH", null);
    checkRange("bpm", "Heart rate", hasHeart && contact ? bpm : null, CFG.limits.bpm, "BPM",
      $("v-bpm").closest(".tile"), 0);
    setAlarm("hr-contact", hasHeart && m.heart.sensor_ok !== false && !contact, "warning",
      "Heart rate sensor is not attached to the baby");
    setAlarm("sht-fail", shtT === null, "warning", "SHT31 is not returning data");
    setAlarm("weight-fail", !!w && w.sensor_ok === false, "warning",
      "Weight sensor (HX711) is not returning data");
    if (pw) {
      setAlarm("mains", pw.mains_ok === false, "warning",
        "Mains power disconnected: system running on battery");
      // 5% hysteresis to prevent chattering around threshold
      const socMin = CFG.limits.soc?.min ?? 30;
      const soc = pw.soc_pct ?? null;
      const low = soc !== null && soc < (state.alarms.has("soc-low") ? socMin + 5 : socMin);
      setAlarm("soc-low", low, "warning", `Low battery: ${fmt(soc, 0)}% (limit ${socMin}%)`);
    }
  }

  // ESP32 FSM state: logged to event history EVERY time it changes
  function fsmLevel(s) {
    const u = s.toUpperCase();
    if (/CRIT|ALARM|ALERT|HAZARD|FAULT|EMERG|BLUE/.test(u)) return "bad";
    if (/WARN|CAUTION|CRY/.test(u)) return "warn";
    return "ok";
  }

  function onFsmState(s) {
    const prev = state.fsm;
    if (s === prev) return;
    state.fsm = s;
    addEvent("ESP32 status", prev === null ? `Initial status: ${s}` : `${prev} → ${s}`);
    setAlarm("fsm", fsmLevel(s) === "bad", "critical", `ESP32 reported status: ${s}`, false);
  }

  // Cry reason from the stage-2 model (sent by cry_service.py two_stage / audio_detector.py)
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
    if (typeof m.source === "string") {
      setText("cry-source", m.source.startsWith("replay:")
        ? `Source: offline test file · ${m.source.slice(7)}`
        : "Source: live microphone");
    }
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
  //  Online / offline status
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

    if (state.fsm === null) setPill("pill-fsm", null, "Status: —");
    else if (!espFresh) setPill("pill-fsm", null, `Status: ${state.fsm} (stale)`);
    else setPill("pill-fsm", fsmLevel(state.fsm), `Status: ${state.fsm}`);

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

  // ------------------------------------------------------------------
  //  Camera (camera_service.py behind nginx at /camera/, password-protected)
  // ------------------------------------------------------------------
  function initCamera() {
    const CAM = "camera/";
    const ui = {
      toggle: $("cam-toggle"), record: $("cam-record"), view: $("cam-view"), img: $("cam-img"),
      msg: $("cam-msg"), status: $("cam-status"), badge: $("cam-rec-badge"), recTime: $("cam-rec-time"),
      recs: $("cam-recs"), list: $("cam-rec-list"), count: $("cam-rec-count"),
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
        const hint = r.status === 401 ? "wrong camera username/password"
          : (r.status === 502 || r.status === 504) ? "camera service is not running"
          : r.status === 404 && !body.error ? "camera is not set up on this Pi (run setup_camera.sh)"
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
      ui.record.textContent = r.active ? "■ Stop recording" : "● Start recording";
      ui.badge.hidden = !r.active;
      if (c.ok) {
        const size = c.size ? `${c.size[0]}×${c.size[1]}` : "";
        let txt = `Camera: ${c.backend} · ${size} · ${c.fps} fps · ${c.viewers} viewer${c.viewers === 1 ? "" : "s"}`;
        txt += ` · ${mb(r.free_mb * 1e6)} free`;
        if (r.error) txt += ` · last recording error: ${r.error}`;
        ui.status.textContent = txt;
      } else {
        ui.status.textContent = `Camera not available: ${c.error || "starting…"}`;
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
        ui.msg.textContent = "Connecting to camera…";
        ui.img.src = CAM + "stream.mjpg?t=" + Date.now();
      } else {
        ui.img.removeAttribute("src");   // closes the MJPEG connection
      }
    }
    ui.img.addEventListener("load", () => { ui.msg.textContent = ""; });
    ui.img.addEventListener("error", () => {
      if (!cam.watching) return;
      ui.msg.textContent = "Live view unavailable. Retrying…";
      setTimeout(() => { if (cam.watching && !document.hidden) setStream(true); }, 3000);
    });
    ui.toggle.addEventListener("click", () => {
      cam.watching = !cam.watching;
      ui.toggle.setAttribute("aria-pressed", String(cam.watching));
      ui.toggle.textContent = cam.watching ? "Stop watching" : "Watch camera";
      ui.view.hidden = !cam.watching;
      setStream(cam.watching);
      startPolling();
    });
    // Pause the stream when the tab is hidden (saves Wi-Fi bandwidth and Pi CPU).
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
          loadRecordings();
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
    function btn(label, cls, onClick) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn small " + cls; b.textContent = label;
      b.addEventListener("click", onClick);
      return b;
    }
    async function loadRecordings() {
      try {
        const { recordings } = await api("api/recordings");
        ui.count.textContent = recordings.length;
        ui.list.replaceChildren(...recordings.map((r) => {
          const tr = document.createElement("tr");
          const tdT = document.createElement("td"); tdT.textContent = r.mtime;
          const tdS = document.createElement("td"); tdS.textContent = mb(r.size);
          const tdA = document.createElement("td");
          const url = CAM + "recordings/" + encodeURIComponent(r.name);
          const dl = document.createElement("a");
          dl.className = "btn small"; dl.href = url + "?download=1"; dl.textContent = "Download";
          dl.setAttribute("download", r.name);
          tdA.append(
            btn("Play", "", () => {
              ui.player.hidden = false; ui.playerName.textContent = `${r.name} · ${r.mtime}`;
              ui.video.src = url; ui.video.play().catch(() => {});
              ui.player.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }),
            dl,
            btn("Delete", "danger", async () => {
              if (!confirm(`Delete recording ${r.name}?`)) return;
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
        ui.count.textContent = "?";
        ui.list.replaceChildren();
        ui.status.textContent = `Camera: ${e.message}`;
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
  connect();
  try { initCamera(); } catch (e) { console.warn("Camera panel disabled:", e); }
})();
