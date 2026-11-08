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

  function read(store) {
    try {
      const raw = store.getItem(KEY);
      if (!raw) return null;
      const v = JSON.parse(raw);
      return v && typeof v.username === "string" && typeof v.password === "string" ? v : null;
    } catch { return null; }
  }

  function remove(store) { try { store.removeItem(KEY); } catch { /* storage blocked */ } }

  window.IncubatorAuth = {
    get() { return read(sessionStorage) || read(localStorage); },

    save(username, password, remember) {
      const v = JSON.stringify({ username, password });
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
