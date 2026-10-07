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
  // The unclamped score (rank) when given: several listings reach 100, and the
  // points above it say which of them is better.
  function scoreBadge(sc, rank, excellent) {
    // The score is squeezed near the top (never quite 100); rank only orders the list.
    const s = Math.round(sc || 0);
    const star = excellent ? ` <span class="excellent" title="Excellent: ${esc(excellent.join(" · "))}">★ Excellent</span>` : "";
    return `<span class="score ${s >= 70 ? "s-high" : s >= 50 ? "s-mid" : "s-low"}">${s}</span>${star}`;
  }
  // The Climate panel (scoring.climate_score via listing_info.climate_panel),
  // the same on Listings and Offers.
  // The owner's five wishes (scoring.wishes): airport, space, swim, condition, water on the land.
  const WISH_MARK = {yes: "✓", part: "~", no: "✗", unknown: "?"};
  function wishesHtml(ws, compact) {
    if (!ws || !ws.length) return "";
    return `<div class="wishes${compact ? " compact" : ""}">${ws.map(w =>
      `<span class="wish w-${esc(w.state)}" title="${esc(w.label)}: ${esc(w.text)}">${compact
        ? `${esc(w.label.split(" ")[0])} ${WISH_MARK[w.state] || "?"}`
        : `<b>${esc(w.label)}</b> ${WISH_MARK[w.state] || "?"} <span class="small">${esc(w.text)}</span>`}</span>`).join("")}</div>`;
  }
  function climateBadge(grade) {
    return grade && grade !== "unknown" ? `<span class="badge c-${esc(grade)}" title="Climate grade">climate ${esc(grade)}</span>` : "";
  }
  function climateHtml(c) {
    if (!c) return "";
    const head = `<b>Climate</b> <span class="badge c-${esc(c.grade)}">${esc(c.grade)}</span>`
      + (c.score != null ? ` <span class="small muted">${c.score}/100 · ${esc(c.location || (c.confidence === "exact" ? "exact position" : "approximate position"))}</span>` : "");
    const body = c.grade === "unknown"
      ? `<p class="small muted">${esc(c.missing || "No climate data for this place.")}</p>`
      : `<ul>${(c.reasons || []).map(r => `<li>${esc(r)}</li>`).join("")}</ul>`;
    return `<div class="climate">${head}${body}
      <p class="small faint">A planning risk indicator from public climate maps, not a survey, an insurance assessment or due diligence.</p></div>`;
  }
  // The Maximum bid waterfall (bidcap.calculate_bid_cap), same on Listings and Offers.
  function bidCapHtml(b, opts) {
    if (!b) return "";
    opts = opts || {};
    const eur = v => (v == null || v === "") ? "—" : money(v);
    const rows = (b.waterfall || []).map(r => `<div class="fact${r.total ? " cost-total" : ""}">
        <span class="k">${esc(r.label)}</span>
        <span class="v">${eur(r.amount)}${r.note ? ` <span class="small muted">${esc(r.note)}</span>` : ""}</span></div>`).join("");
    const head = b.recommended_bid != null
      ? `recommended ${eur(b.recommended_bid)} · absolute max ${eur(b.absolute_max_bid)}`
      : "no figure yet";
    const compare = opts.compare
      ? `<p class="small ${opts.compare.level === "above_max" ? "bad" : opts.compare.level === "above_rec" ? "warn" : "ok"}">${esc(opts.compare.text)}</p>`
      : "";
    const use = !opts.done && b.recommended_bid
      ? `<button class="btn btn-sm" style="margin-top:6px" data-bid="${esc((Math.round(b.recommended_bid)).toLocaleString("de-DE") + ",00")}" onclick="typeof setBid === 'function' && setBid(this.dataset.bid)">Use recommended bid</button>`
      : "";
    const kept = opts.kept
      ? `<p class="small muted">Calculator when it was sent: recommended ${eur(opts.kept.recommended)} · absolute max ${eur(opts.kept.absolute)}${opts.kept.note ? `. ${esc(opts.kept.note)}` : ""}</p>`
      : "";
    const reasons = (b.reasons || []).map(r => `<li>${esc(r)}</li>`).join("");
    const unknowns = (b.unknowns || []).length
      ? `<p class="small muted">Missing or approximate: ${esc(b.unknowns.join("; "))}.</p>` : "";
    return `<div class="guide bid-cap">
      <b>Maximum bid</b> <span class="small muted">${esc(head)} · confidence ${esc(b.market_value_confidence || "unknown")}</span>
      ${rows ? `<div class="facts">${rows}</div>` : ""}
      ${compare}${kept}
      ${reasons ? `<ul class="small" style="margin:6px 0 0 18px">${reasons}</ul>` : ""}
      ${unknowns}${use}
      <p class="small muted">${esc(b.note || "")}</p></div>`;
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
  function toast(msg, kind = "ok", ms = 2800) {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.style.background = kind === "bad" ? "#7f1d1d" : kind === "warn" ? "#78350f" : "#14532d";
    t.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.style.display = "none"), ms);
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
    const stop = document.getElementById("scan-stop");
    if (s.running) {
      const pct = s.total ? Math.round(100 * (s.done || 0) / s.total) : 0;
      const verb = s.stop ? "Stopping after" : "Scanning";
      el.innerHTML = `${verb} ${esc(s.label || "")} · ${s.done || 0}/${s.total || "?"}` +
        `${s.current ? " · " + esc(s.current) : ""}<div class="scan-bar"><div style="width:${pct}%"></div></div>`;
      btn.disabled = true;
      if (stop) { stop.hidden = false; stop.disabled = !!s.stop; }
    } else {
      const sum = s.summary;
      const when = ago(s.finished_at || s.last_scrape);
      el.innerHTML = `${sum && sum.stopped ? "Scan stopped" : "Last scan"} ${when}` +
        (sum && sum.errors ? ` · <a href="/sources" class="bad">${sum.errors} failing</a>` : "");
      btn.disabled = false;
      if (stop) stop.hidden = true;
    }
    if (wasRunning && !s.running) {
      const sum = s.summary;
      if (sum && sum.stopped) toast("Scan stopped");
      else toast(`Scan finished: ${sum ? sum.listings : 0} listings`);
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
  async function stopScan() {
    const stop = document.getElementById("scan-stop");
    if (stop) stop.disabled = true;
    await api("/api/scan/stop", {method: "POST"});
    toast("Stopping after the current source");
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

  /* After the app updated itself from GitHub, say so once. */
  async function announceUpdate() {
    try {
      const last = await (await fetch("/api/update/last")).json();
      if (!last || !last.at) return;
      let seen = null;
      try { seen = localStorage.getItem("seenUpdate"); } catch (e) {}
      if (seen === last.at) return;
      const n = (last.changes || []).length;
      if (last.rolled_back) {
        toast("The newest version did not start on this PC, so the app went back to the previous one. See Settings → Updates.", "warn", 12000);
      } else {
        toast(`Updated to the latest version (${n} change${n === 1 ? "" : "s"}). See Settings → Updates.`);
      }
      try { localStorage.setItem("seenUpdate", last.at); } catch (e) {}
    } catch (e) {}
  }

  document.addEventListener("DOMContentLoaded", () => {
    initMenus();
    pollScan();
    heartbeat();
    setInterval(heartbeat, 15000);
    announceUpdate();
  });

  return {esc, money, scoreBadge, wishesHtml, climateBadge, climateHtml, bidCapHtml, flag, link, ago, toast, api, startScan, stopScan, pollScan};
})();
