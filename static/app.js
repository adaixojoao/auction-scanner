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
  /* The app window has no download bar. A court PDF opened here asks where to
     save it. Outside links go to the normal browser, and a PDF to the reader. */
  function openOutside(e) {
    const a = e.target.closest ? e.target.closest("a[href]") : null;
    if (!a) return;
    let u;
    try { u = new URL(a.href, location.href); } catch (err) { return; }
    if (u.host === location.host) return;
    if (u.protocol !== "http:" && u.protocol !== "https:") return;
    e.preventDefault();
    fetch("/api/open", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url: u.href})})
      .then(r => r.json().then(d => ({ok: r.ok, d})))
      .then(({ok, d}) => { if (!ok) toast(d.error || "Could not open that link", "bad"); })
      .catch(() => toast("Could not open that link", "bad"));
  }
  document.addEventListener("click", openOutside, true);
  document.addEventListener("auxclick", openOutside, true);
  function ago(iso) {
    if (!iso) return "never";
    const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`;
    return `${Math.round(mins / 1440)} days ago`;
  }

  /* Screen-reader announcements go to persistent live regions (base.html),
     emptied first so the same message twice is still read. */
  const announcementTimers = new Map();
  function announce(message, regionId = "ui-announcement") {
    const region = document.getElementById(regionId);
    if (!region) return;
    clearTimeout(announcementTimers.get(regionId));
    region.textContent = "";
    announcementTimers.set(regionId, setTimeout(() => {
      region.textContent = String(message ?? "");
      announcementTimers.delete(regionId);
    }, 60));
  }

  let toastTimer;
  function toast(msg, kind = "ok", ms = 2800, shouldAnnounce = true) {
    if (shouldAnnounce) announce(msg);
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.style.background = kind === "bad" ? "#7f1d1d" : kind === "warn" ? "#78350f" : "#14532d";
    t.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.style.display = "none"; }, ms);
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

  /* Latest-request-wins JSON reads, for GETs only. A newer run, or cancel(),
     turns an older answer into {kind: "obsolete"}; an aborted read is never
     shown as a failure. Not for POSTs: aborting one does not undo it. */
  function latestRead() {
    let version = 0;
    let controller = null;
    function cancel() {
      version += 1;
      if (controller) controller.abort();
      controller = null;
    }
    async function run(url, options = {}) {
      const requestVersion = ++version;
      if (controller) controller.abort();
      const currentController = new AbortController();
      controller = currentController;
      const current = () => requestVersion === version;
      try {
        const response = await fetch(url, {...options, signal: currentController.signal});
        const data = await response.json().catch(() => null);
        if (!current()) return {kind: "obsolete"};
        if (!response.ok) {
          return {kind: "error", message: data && typeof data.error === "string"
            ? data.error : `Request failed (${response.status})`};
        }
        if (data == null) return {kind: "error", message: "The server returned an unreadable response."};
        return {kind: "success", data};
      } catch (error) {
        if (!current() || error.name === "AbortError") return {kind: "obsolete"};
        return {kind: "error", message: "Could not reach the local application server."};
      } finally {
        if (current()) controller = null;
      }
    }
    return {run, cancel};
  }

  /* ── Scan button & progress (top bar) ───────────────────── */
  let wasRunning = false, pollTimer;
  // Announced only at lifecycle transitions, never on every polling tick.
  let scanAnnouncementState = "idle";
  function announceScanState(s) {
    const next = s.running ? (s.stop ? "stopping" : "running") : "idle";
    if (next === scanAnnouncementState) return;
    const previous = scanAnnouncementState;
    scanAnnouncementState = next;
    if (next === "running") {
      announce("Scan started.", "scan-announcement");
    } else if (next === "stopping") {
      announce("Scan will stop after the current source.", "scan-announcement");
    } else if (previous !== "idle") {
      const summary = s.summary;
      announce(summary && summary.stopped ? "Scan stopped."
        : `Scan finished: ${summary ? summary.listings : 0} listings.`, "scan-announcement");
    }
  }
  function renderScan(s) {
    const el = document.getElementById("scan-status");
    const btn = document.getElementById("scan-btn");
    if (!el) return;
    announceScanState(s);
    const stop = document.getElementById("scan-stop");
    if (s.running) {
      const total = Number(s.total);
      const known = Number.isFinite(total) && total > 0;
      const pct = known ? Math.min(100, Math.max(0, Math.round(100 * (Number(s.done) || 0) / total))) : 0;
      const verb = s.stop ? "Stopping after" : "Scanning";
      el.innerHTML = `${verb} ${esc(s.label || "")} · ${s.done || 0}/${s.total || "?"}` +
        `${s.current ? " · " + esc(s.current) : ""}<div class="scan-bar" role="progressbar" aria-label="Scan progress"` +
        ` aria-valuemin="0" aria-valuemax="100"${known ? ` aria-valuenow="${pct}"` : ""}><div style="width:${pct}%"></div></div>`;
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
      if (sum && sum.stopped) toast("Scan stopped", "ok", 2800, false);
      else toast(`Scan finished: ${sum ? sum.listings : 0} listings`, "ok", 2800, false);
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
    document.querySelectorAll(".menu.open").forEach(menu => {
      setMenuOpen(menu, false);
    });
    await api("/api/scan", {method: "POST", body});
    toast("Scan started", "ok", 2800, false);
    wasRunning = true;
    pollScan();
  }
  async function stopScan() {
    const stop = document.getElementById("scan-stop");
    if (stop) stop.disabled = true;
    await api("/api/scan/stop", {method: "POST"});
    toast("Stopping after the current source", "ok", 2800, false);
    pollScan();
  }

  /* Disclosure dropdowns (Scan now, Export report): plain lists of links and
     buttons, not ARIA menus, so Tab stays native. */
  function setMenuOpen(menu, open, returnFocus = false) {
    if (!menu) return;
    menu.classList.toggle("open", open);
    const toggle = menu.querySelector("[data-menu]");
    if (toggle) {
      toggle.setAttribute("aria-expanded", String(open));
      if (returnFocus) toggle.focus();
    }
  }
  function initMenus() {
    document.querySelectorAll("[data-menu]").forEach(button => {
      button.setAttribute("aria-expanded", "false");
    });
    document.addEventListener("click", event => {
      const toggle = event.target.closest("[data-menu]");
      const menu = toggle ? toggle.closest(".menu") : null;
      document.querySelectorAll(".menu.open").forEach(openMenu => {
        if (openMenu !== menu && !openMenu.contains(event.target)) setMenuOpen(openMenu, false);
      });
      if (menu) setMenuOpen(menu, !menu.classList.contains("open"));
      // An activated item closes its list; focus goes back to the toggle so it
      // is not left on a hidden item.
      const item = event.target.closest(".menu-list a, .menu-list button");
      if (item) setMenuOpen(item.closest(".menu"), false, true);
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") {
        const menu = event.target.closest(".menu.open");
        if (menu) {
          event.preventDefault();
          setMenuOpen(menu, false, true);
        }
      }
      if (event.key === "ArrowDown") {
        const toggle = event.target.closest("[data-menu]");
        const menu = toggle && toggle.closest(".menu");
        if (!menu) return;
        event.preventDefault();
        setMenuOpen(menu, true);
        const first = menu.querySelector(".menu-list button:not(:disabled), .menu-list a[href]");
        if (first) first.focus();
      }
    });
    document.addEventListener("focusin", event => {
      document.querySelectorAll(".menu.open").forEach(menu => {
        if (!menu.contains(event.target)) setMenuOpen(menu, false);
      });
    });
  }

  /* Tabs with manual activation: arrows, Home and End move focus only;
     Enter, Space or a click activates. */
  function initGoalTabs(tablist, activate) {
    if (!tablist) return;
    const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
    tablist.addEventListener("keydown", event => {
      const index = tabs.indexOf(event.target.closest('[role="tab"]'));
      if (index < 0) return;
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate(tabs[index]);
        return;
      } else return;
      event.preventDefault();
      tabs.forEach(tab => { tab.tabIndex = tab === tabs[next] ? 0 : -1; });
      tabs[next].focus();
    });
  }

  /* Sticky offsets follow the top bar, which wraps on narrow windows. */
  function initTopbarHeight() {
    const topbar = document.querySelector(".topbar");
    if (!topbar) return;
    const update = () => {
      const height = Math.ceil(topbar.getBoundingClientRect().height);
      document.documentElement.style.setProperty("--topbar-height", `${height}px`);
    };
    update();
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(update).observe(topbar);
    else window.addEventListener("resize", update, {passive: true});
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

  function dismiss(id) {
    const dlg = document.getElementById("dismiss-dialog");
    const reject = document.getElementById("dismiss-ok");
    if (!dlg || !dlg.showModal || !reject) return Promise.resolve(false);
    return new Promise(resolve => {
      const finish = (ok) => {
        reject.removeEventListener("click", onOk);
        dlg.removeEventListener("close", onClose);
        if (!ok) { resolve(false); return; }
        const reason = document.getElementById("dismiss-reason").value;
        api("/api/listings/status", {method: "POST", body: {id, status: "dismissed", reason}})
          .then(() => resolve(true))
          .catch(() => resolve(false));
      };
      const onOk = () => dlg.close("ok");
      const onClose = () => finish(dlg.returnValue === "ok");
      reject.addEventListener("click", onOk);
      dlg.addEventListener("close", onClose);
      // A previous confirmation must not make Cancel or Escape count as one.
      dlg.returnValue = "";
      dlg.showModal();
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initMenus();
    initTopbarHeight();
    pollScan();
    heartbeat();
    setInterval(heartbeat, 15000);
    announceUpdate();
  });

  return {esc, money, scoreBadge, wishesHtml, climateBadge, climateHtml, bidCapHtml, flag, link, ago, toast, announce, api, latestRead, startScan, stopScan, pollScan, dismiss, initGoalTabs};
})();
