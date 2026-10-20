// listings-comparison.js — the Listings page: up to four properties side by side, for research.
// Plain scripts sharing one global scope, loaded in order by templates/listings.html
// (listings-saved-searches.js, listings-view.js, listings-summary.js, listings-details.js,
//  listings-cards.js, listings-notes.js, listings-drawer.js, listings-comparison-store.js,
//  listings-comparison-render.js, listings-comparison.js, listings.js).
//
// The selection (listings-comparison-store.js) is page-session memory: it
// survives filters, pages and the Table / Cards switch, and is never stored or
// put in the address. Selecting changes no status and makes no request. The
// comparison reads each property through the one details cache (the same
// /api/listing read the drawer uses) and shows the server's own summaries as
// text (listings-summary.js); it ranks nothing and names no winner. A property
// outside the results now shown stays selected and says so. Notes are never
// part of what it shows.

const comparison = (() => {
  const store = AuctionComparisonStore.createStore({notify: () => { renderTray(); syncButtons(); if (dialog.open) syncModels(); }});
  const dialog = $("comparison-dialog");
  const tray = $("comparison-tray");
  const models = new Map();      // id → {state, values, title, stale}; only while the dialog is open
  const reads = new Map();       // id → {controller, number}: the latest read per property
  let readNumber = 0;
  let staleNote = "";

  function say(text) {
    $("comparison-message").textContent = text;
    if (text) AS.announce(text);
  }

  function titleOf(id, fallback) {
    return (shownItems.get(id) || {}).title || fallback || "Untitled listing";
  }

  // ── Selection buttons on every surface, by data-id ──
  function syncButtons() {
    document.querySelectorAll('[data-role="compare"]').forEach(button => {
      const id = button.dataset.id;
      if (!id) return;
      const chosen = store.has(id);
      button.setAttribute("aria-pressed", String(chosen));
      button.textContent = chosen ? "Selected to compare" : "Compare";
    });
  }

  function toggle(id, title) {
    try {
      const added = store.toggle(id, titleOf(id, title));
      say(added ? `Added “${titleOf(id, title)}” to the comparison (${store.snapshot().length} of ${store.maximum}).`
                : `Removed “${titleOf(id, title)}” from the comparison.`);
    } catch (error) {
      say(error.message);
    }
  }

  function renderTray() {
    const chosen = store.snapshot();
    tray.hidden = !chosen.length;
    $("comparison-selections").replaceChildren(...chosen.map(item => {
      const li = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = item.title;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-sm";
      remove.dataset.id = item.id;
      remove.dataset.role = "compare-remove";
      remove.textContent = "Remove";
      remove.setAttribute("aria-label", `Remove from comparison: ${item.title}`);
      li.append(name, " ", remove);
      if (!shownItems.has(item.id)) {
        const note = document.createElement("span");
        note.className = "small muted";
        note.textContent = " (out of current results)";
        li.append(note);
      }
      return li;
    }));
    // The sticky tray never covers what keyboard focus scrolls to.
    document.documentElement.style.scrollPaddingBottom = chosen.length ? `${tray.offsetHeight + 16}px` : "";
    $("comparison-open").disabled = chosen.length < 2;
    $("comparison-count").textContent = `${chosen.length} of ${store.maximum} selected`
      + (chosen.length < 2 ? "; select at least two to compare." : ".");
  }

  // ── One property's display strings, from its own details and summaries ──
  const KIND_TEXT = {home: "Home", urban_plot: "Urban plot", rural_plot: "Rural plot", other: "Other (shops, garages…)"};
  function goalLabel() {
    const tab = $(`goal-${view.mode}`);
    return tab ? tab.textContent.trim() : view.mode;
  }
  function modelFor(d) {
    const p = d.presentation || {};
    const place = [d.country, d.concelho, d.district].filter(Boolean).join(" · ");
    const loc = d.location || {};
    const area = Number(d.area_m2);
    const areaText = Number.isFinite(area) && area > 0
      ? (d.kind === "urban_plot" || d.kind === "rural_plot"
        ? `Plot area as stated: ${Math.round(area).toLocaleString("de-DE")} m²`
        : `Area as stated: ${Math.round(area).toLocaleString("de-DE")} m²; whether floor or plot is not established`)
      : "";
    const past = d.past_results;
    const climate = d.climate || {};
    const climateText = !climate.grade || climate.grade === "unknown"
      ? `Unknown; ${climate.missing || "hazard coverage not established"}`
      : `Grade ${climate.grade}: a planning indicator from climate layers, not a hazard survey`;
    return {
      location: [place, loc.label ? `${loc.label}${loc.source ? ` (from ${loc.source})` : ""}` : ""].filter(Boolean).join("; "),
      kind: KIND_TEXT[d.kind] || "",
      goal_score: d.score == null ? "" : `${goalLabel()}: ${d.score}; interest for this goal, not verification`,
      price: priceSummaryText(p.price),
      all_in: allInSummaryText(p.all_in),
      cost_basis: costBasisText(p.all_in),
      area: areaText,
      valuation: past ? `Auctions within ${past.km} km sold for a median of ${AS.money(past.median)} (${past.count} lots, last year): a benchmark, not a valuation of this property` : "",
      checks: checksSummaryText(p.checks),
      climate: climateText,
      deadline: deadlineSummaryText(p.deadline),
      reasons: (Array.isArray(d.reasons) ? d.reasons : []).join("; "),
    };
  }

  // ── Reads: one per selected property, the latest one wins ──
  async function read(id, {fresh = false} = {}) {
    const previous = reads.get(id);
    if (previous) previous.controller.abort();        // a details GET only; saves and status changes go on
    const controller = new AbortController();
    const number = ++readNumber;
    reads.set(id, {controller, number});
    const current = models.get(id);
    models.set(id, {...(current || {}), state: "loading", title: titleOf(id, current && current.title)});
    draw();
    const result = await detailsCache.load(view.mode, id, detailToken(id), {fresh, signal: controller.signal});
    const latest = reads.get(id);
    if (!latest || latest.number !== number || !dialog.open || !store.has(id)) return;   // removed, closed or superseded
    reads.delete(id);
    if (result.kind === "obsolete") { models.set(id, {state: "error", title: titleOf(id)}); draw(); return; }
    if (result.kind === "error") { models.set(id, {state: "error", title: titleOf(id)}); draw(); return; }
    const data = result.data;
    store.updateTitle(id, data.title || "");
    models.set(id, {state: "loaded", title: data.title || titleOf(id), values: modelFor(data), stale: false});
    draw();
  }

  function stopReads(ids = [...reads.keys()]) {
    ids.forEach(id => { const r = reads.get(id); if (r) r.controller.abort(); reads.delete(id); });
  }

  // The dialog follows the selection: a removed property goes, a new one is read.
  function syncModels() {
    const chosen = new Set(store.snapshot().map(s => s.id));
    [...models.keys()].forEach(id => { if (!chosen.has(id)) { models.delete(id); stopReads([id]); } });
    chosen.forEach(id => { if (!models.has(id)) read(id); });
    if (!chosen.size) close();
    else draw();
  }

  // Redraws keep focus on the same control of the same property.
  function draw() {
    if (!dialog.open) return;
    const active = document.activeElement;
    const keep = active && dialog.contains(active) && active.dataset.role ? {role: active.dataset.role, id: active.dataset.id} : null;
    const scroller = dialog.querySelector(".comparison-table-scroll");
    const left = scroller ? scroller.scrollLeft : 0;
    const list = store.snapshot().map(s => {
      const m = models.get(s.id) || {state: "loading"};
      const notes = [];
      if (!shownItems.has(s.id)) notes.push("Out of current results");
      if (m.stale) notes.push("May be out of date: use Refresh");
      return {id: s.id, title: m.title || s.title, state: m.state, values: m.values, scope_note: notes.join(" · ")};
    });
    AuctionComparisonRender.render($("comparison-content"), list, {open: openDetails, refresh: id => read(id, {fresh: true})});
    const notice = $("comparison-stale");
    notice.hidden = !staleNote;
    notice.textContent = staleNote;
    const again = dialog.querySelector(".comparison-table-scroll");
    if (again) again.scrollLeft = left;
    if (keep) {
      const target = [...dialog.querySelectorAll("[data-role]")].find(el => el.dataset.role === keep.role && el.dataset.id === keep.id);
      if (target && !target.disabled) target.focus({preventScroll: true});
      else if (target) dialog.querySelector(".comparison-table-scroll").focus({preventScroll: true});
    }
  }

  function open() {
    if (store.snapshot().length < 2) { say("Select at least two properties to compare."); return; }
    staleNote = "";
    models.clear();
    dialog.showModal();
    $("comparison-close").focus();
    store.snapshot().forEach(s => read(s.id));
  }

  function close() {
    stopReads();
    models.clear();
    if (dialog.open) dialog.close();
  }

  // Details open in the shared drawer: the comparison closes first, so there is
  // one modal at a time; closing the drawer gives focus back to Compare selected.
  function openDetails(id) {
    close();
    propertyDrawer.open(id, $("comparison-open"));
  }

  // After a redraw of the results: titles, buttons and "out of current results".
  function sync() {
    store.snapshot().forEach(s => { if (shownItems.has(s.id)) store.updateTitle(s.id, shownItems.get(s.id).title || ""); });
    renderTray();
    syncButtons();
    if (dialog.open) draw();
  }

  // A finished scan or a status change: what is shown may be out of date; it is read again when you ask.
  function markStale(id) {
    if (!dialog.open) return;
    models.forEach((m, key) => { if ((id == null || key === id) && m.state === "loaded") m.stale = true; });
    if (id == null) staleNote = "A scan finished: these details may be out of date. Refresh each property, or close and compare again.";
    draw();
  }

  document.addEventListener("click", e => {
    const button = e.target.closest && e.target.closest('[data-role="compare"], [data-role="compare-remove"]');
    if (!button || !button.dataset.id) return;
    if (button.dataset.role === "compare") toggle(button.dataset.id, button.dataset.title);
    else {
      const name = titleOf(button.dataset.id, (store.snapshot().find(s => s.id === button.dataset.id) || {}).title);
      store.remove(button.dataset.id);
      say(`Removed “${name}” from the comparison.`);
      if (dialog.open) $("comparison-close").focus();
      else if (!tray.hidden) $("comparison-open").focus();
      else $("result-count").focus({preventScroll: true});
    }
  });
  $("comparison-open").addEventListener("click", open);
  $("comparison-clear").addEventListener("click", () => {
    store.clear();
    say("Comparison selection cleared. Filters, notes and statuses did not change.");
    $("result-count").focus({preventScroll: true});
  });
  $("comparison-close").addEventListener("click", close);
  dialog.addEventListener("close", () => {
    stopReads();
    models.clear();
    if (!propertyDrawer.panel.open && !tray.hidden) $("comparison-open").focus({preventScroll: true});
  });

  return {sync, markStale, has: id => store.has(id), snapshot: () => store.snapshot(), syncButtons, dialog};
})();
