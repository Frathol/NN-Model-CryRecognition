/* Sign-in session for the dashboard.
 *
 * The credentials are a Mosquitto account (see mosquitto/smart-incubator.acl).
 * The broker is the real gatekeeper: without a valid account the dashboard
 * receives no data. Credentials live in sessionStorage (cleared when the tab
 * closes) or in localStorage when "Keep me signed in" is ticked.
 */
(() => {
  "use strict";
  const KEY = "incubator.auth";
  // "Keep me signed in" lasts one 12-hour shift; the password is not kept on the device forever.
  const REMEMBER_MS = 12 * 60 * 60 * 1000;

  function read(store) {
    try {
      const raw = store.getItem(KEY);
      if (!raw) return null;
      const v = JSON.parse(raw);
      if (!v || typeof v.username !== "string" || typeof v.password !== "string") return null;
      if (typeof v.exp === "number" && Date.now() > v.exp) { remove(store); return null; }
      return v;
    } catch { return null; }
  }

  function remove(store) { try { store.removeItem(KEY); } catch { /* storage blocked */ } }

  window.IncubatorAuth = {
    get() { return read(sessionStorage) || read(localStorage); },

    save(username, password, remember) {
      const v = JSON.stringify(remember ? { username, password, exp: Date.now() + REMEMBER_MS }
                                        : { username, password });
      this.clear();
      try { (remember ? localStorage : sessionStorage).setItem(KEY, v); return true; }
      catch { return false; }
    },

    clear() { remove(sessionStorage); remove(localStorage); },

    lastUsername() {
      try { return localStorage.getItem(KEY + ".user") || ""; } catch { return ""; }
    },
    rememberUsername(u) {
      try { localStorage.setItem(KEY + ".user", u); } catch { /* storage blocked */ }
    },
  };
})();
