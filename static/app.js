/* Auction Scanner — helpers shared by every page: escaping, formatting, API
   calls, toasts, the scan button in the top bar, and the heartbeat that lets
   the desktop app know its window is still open. */
"use strict";

const AS = (() => {
  const FLAGS = window.FLAGS || {};

  function esc(s) {
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function money(v) {
    return v ? "€" + Number(v).toLocaleString("de-DE", {maximumFractionDigits: 0}) : "?";
  }
  function scoreBadge(sc) {
    const s = Math.round(sc || 0);
    return `<span class="score ${s >= 70 ? "s-high" : s >= 50 ? "s-mid" : "s-low"}">${s}</span>`;
  }
  function flag(code) { return FLAGS[code] ? `<span title="${esc(code)}">${FLAGS[code]}</span>` : esc(code || ""); }
  function link(url, text) {
    return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text);
  }
  function ago(iso) {
    if (!iso) return "never";
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
    return `${Math.round(mins / 1440)} days ago`;
  }

  let toastTimer;
  function toast(msg, kind = "ok") {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.style.background = kind === "bad" ? "#7f1d1d" : kind === "warn" ? "#78350f" : "#14532d";
    t.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.style.display = "none"), 2800);
  }

  async function api(url, opts = {}) {
    const init = {...opts, headers: {"Content-Type": "application/json", ...(opts.headers || {})}};
    if (init.body && typeof init.body !== "string") init.body = JSON.stringify(init.body);
    const r = await fetch(url, init);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      toast(data.error || `Request failed (${r.status})`, "bad");
      throw new Error(data.error || r.status);
    }
    return data;
  }

  /* ── Scan button & progress (top bar) ───────────────────── */
  let wasRunning = false, pollTimer;
  function renderScan(s) {
    const el = document.getElementById("scan-status");
    const btn = document.getElementById("scan-btn");
    if (!el) return;
    if (s.running) {
      const pct = s.total ? Math.round(100 * (s.done || 0) / s.total) : 0;
      el.innerHTML = `Scanning ${esc(s.label || "")} · ${s.done || 0}/${s.total || "?"}` +
        `${s.current ? " · " + esc(s.current) : ""}<div class="scan-bar"><div style="width:${pct}%"></div></div>`;
      btn.disabled = true;
    } else {
      const sum = s.summary;
      el.innerHTML = `Last scan ${ago(s.finished_at || s.last_scrape)}` +
        (sum && sum.errors ? ` · <a href="/sources" class="bad">${sum.errors} failing</a>` : "");
      btn.disabled = false;
    }
    if (wasRunning && !s.running) {
      toast(`Scan finished: ${s.summary ? s.summary.listings : 0} listings`);
      document.dispatchEvent(new CustomEvent("scan-finished"));
    }
    wasRunning = s.running;
  }
  async function pollScan() {
    clearTimeout(pollTimer);
    try { renderScan(await (await fetch("/api/scan")).json()); } catch (e) { /* app closing */ }
    pollTimer = setTimeout(pollScan, wasRunning ? 2000 : 20000);
  }
  async function startScan(body) {
    document.querySelectorAll(".menu.open").forEach(m => m.classList.remove("open"));
    await api("/api/scan", {method: "POST", body});
    toast("Scan started");
    wasRunning = true;
    pollScan();
  }

  function initMenus() {
    document.addEventListener("click", e => {
      const toggle = e.target.closest("[data-menu]");
      const menu = toggle ? toggle.parentElement : null;
      document.querySelectorAll(".menu.open").forEach(m => {
        if (m !== menu && !m.contains(e.target)) m.classList.remove("open");
      });
      if (menu) menu.classList.toggle("open");
    });
  }

  /* The desktop app shuts itself down a few minutes after the last window
     stops sending this. */
  function heartbeat() {
    fetch("/api/heartbeat", {method: "POST"}).catch(() => {});
  }

  document.addEventListener("DOMContentLoaded", () => {
    initMenus();
    pollScan();
    heartbeat();
    setInterval(heartbeat, 15000);
  });

  return {esc, money, scoreBadge, flag, link, ago, toast, api, startScan, pollScan};
})();
