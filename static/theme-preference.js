// theme-preference.js — Appearance: System, Light or Dark, kept in this browser only.
// Loaded in <head> by base.html before the stylesheet applies, so a page is
// drawn in its theme from the start. It is the one owner of the preference:
// it stores only "system", "light" or "dark", follows the system setting while
// System is chosen, and follows a change made in another tab. app.css maps
// :root[data-theme] to its colour tokens.
(function (root) {
  "use strict";
  const KEY = "auction-scanner:theme:v1";
  const VALID = new Set(["system", "light", "dark"]);
  const media = root.matchMedia("(prefers-color-scheme: dark)");
  let preference = "system";
  let warning = "";

  try {
    const stored = root.localStorage.getItem(KEY);
    if (VALID.has(stored)) preference = stored;
  } catch (error) {
    warning = "Theme preference could not be read; using the system setting.";
  }

  function snapshot() {
    const resolved = preference === "system" ? (media.matches ? "dark" : "light") : preference;
    return {preference, resolved, warning};
  }

  function apply() {
    const state = snapshot();
    document.documentElement.dataset.theme = state.resolved;
    document.documentElement.style.colorScheme = state.resolved;
    document.querySelectorAll("[data-theme-control]").forEach(control => {
      control.value = state.preference;
    });
    // Only a warning is shown, and only when it changes: no announcement per system change.
    const message = document.getElementById("theme-preference-message");
    if (message && message.textContent !== state.warning) message.textContent = state.warning;
    document.dispatchEvent(new CustomEvent("theme-changed", {detail: state}));
    return state;
  }

  function set(value) {
    if (!VALID.has(value)) throw new Error("Theme must be system, light, or dark.");
    preference = value;
    warning = "";
    try { root.localStorage.setItem(KEY, value); }
    catch (error) { warning = "Theme changed for this page, but could not be saved in this browser."; }
    return apply();
  }

  const onSystemChange = () => { if (preference === "system") apply(); };
  if (media.addEventListener) media.addEventListener("change", onSystemChange);
  else media.addListener(onSystemChange);
  root.addEventListener("storage", event => {
    if (event.key !== KEY) return;
    preference = VALID.has(event.newValue) ? event.newValue : "system";
    warning = "";
    apply();
  });
  document.addEventListener("change", event => {
    if (event.target.matches && event.target.matches("[data-theme-control]")) set(event.target.value);
  });
  document.addEventListener("DOMContentLoaded", apply);
  root.AuctionTheme = {set, snapshot, apply};
  apply();
})(window);
