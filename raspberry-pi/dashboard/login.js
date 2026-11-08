/* Sign-in: the broker checks the account. If Mosquitto accepts the MQTT
 * connection, the credentials are stored for this session and the dashboard opens. */
(() => {
  "use strict";
  const CFG = window.DASHBOARD_CONFIG;
  const Auth = window.IncubatorAuth;
  const $ = (id) => document.getElementById(id);
  const form = $("login-form"), user = $("username"), pass = $("password");
  const submit = $("submit"), notice = $("notice"), remember = $("remember"), reveal = $("reveal");

  // Already signed in -> straight to the dashboard (it bounces back here if the broker refuses).
  if (Auth.get()) { location.replace("index.html"); return; }

  const REASONS = {
    auth: "The broker no longer accepts the saved account. Sign in again.",
    signedout: "You are signed out.",
  };
  const reason = new URLSearchParams(location.search).get("reason");
  if (reason && REASONS[reason]) say(REASONS[reason], reason === "auth" ? "error" : "");

  user.value = Auth.lastUsername() || CFG.defaultUsername || "";
  (user.value ? pass : user).focus();

  function say(text, kind = "") {
    notice.textContent = text;
    notice.className = "notice" + (kind ? " " + kind : "");
  }

  function fieldError(input, hintId, bad) {
    input.setAttribute("aria-invalid", String(bad));
    $(hintId).hidden = !bad;
  }

  reveal.addEventListener("click", () => {
    const show = pass.type === "password";
    pass.type = show ? "text" : "password";
    reveal.textContent = show ? "Hide" : "Show";
    reveal.setAttribute("aria-pressed", String(show));
    reveal.setAttribute("aria-label", show ? "Hide password" : "Show password");
    pass.focus();
  });
  reveal.setAttribute("aria-label", "Show password");

  user.addEventListener("input", () => fieldError(user, "username-hint", false));
  pass.addEventListener("input", () => fieldError(pass, "password-hint", false));

  function setBusy(busy) {
    submit.disabled = busy;
    submit.textContent = busy ? "Checking account…" : "Sign in";
    user.readOnly = busy; pass.readOnly = busy;
  }

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const u = user.value.trim(), p = pass.value;
    fieldError(user, "username-hint", !u);
    fieldError(pass, "password-hint", !p);
    if (!u) { user.focus(); return; }
    if (!p) { pass.focus(); return; }

    if (!window.mqtt) {
      say("The MQTT library did not load. Run setup_pi.sh again, or connect this device to the internet once.", "error");
      return;
    }

    setBusy(true);
    say("Contacting the broker on the Raspberry Pi…", "busy");

    let done = false;
    const client = mqtt.connect(CFG.mqttUrl, {
      username: u, password: p,
      clientId: "dashboard-login-" + Math.random().toString(16).slice(2, 10),
      reconnectPeriod: 0, connectTimeout: 8000, clean: true,
    });

    const finish = (ok, message) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { client.end(true); } catch { /* already closed */ }
      if (ok) {
        if (!Auth.save(u, p, remember.checked)) {
          setBusy(false);
          say("This browser blocks site storage, so the sign-in cannot be kept. Allow storage for this site and try again.", "error");
          return;
        }
        Auth.rememberUsername(u);
        say("Signed in. Opening the dashboard…", "busy");
        location.replace("index.html");
      } else {
        setBusy(false);
        say(message, "error");
        if (/password/i.test(message)) { pass.select(); pass.focus(); }
      }
    };

    const timer = setTimeout(() => finish(false,
      "No answer from the broker after 10 seconds. Check that the Raspberry Pi is on and that this device is on the same network."), 10000);

    client.on("connect", () => finish(true));
    client.on("error", (e) => {
      const code = e && e.code;
      if (code === 4 || code === 5 || code === 134 || code === 135) {
        finish(false, "Username or password is not correct.");
      } else {
        finish(false, `The broker refused the connection (${(e && e.message) || "unknown error"}).`);
      }
    });
    client.on("close", () => finish(false,
      `Cannot reach the broker at ${CFG.mqttUrl}. Check that Mosquitto and nginx are running on the Pi.`));
  });
})();
