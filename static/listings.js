// listings.js — the Listings page: loading the results, status changes and Undo, sorting and goal tabs, refreshes after a scan, and start-up.
// Plain scripts sharing one global scope, loaded in order by templates/listings.html
// (listings-saved-searches.js, listings-view.js, listings-summary.js, listings-details.js,
//  listings-cards.js, listings-notes.js, listings-drawer.js, listings.js).

// ── Loading: one latest-wins read for the results (AS.latestRead) ──
const resultsRead = AS.latestRead();
let renderedOnce = false;      // a successful answer is on screen
let lastCountText = "";
const shownItems = new Map();  // id → item on screen, rows and research cards

function listingParams(state) {
  return new URLSearchParams({
    country: state.country, source: state.source, kind: state.kind,
    max_price: state.max_price, min_score: state.min_score,
    search: state.search, properties: state.properties ? 1 : 0,
    // The view's "hidden" is the API's show_hidden, here only.
    show_hidden: state.status === "hidden" ? 1 : 0, status: state.status === "shortlisted" ? "shortlisted" : "",
    climate: state.climate, climate_exact: state.climate_exact ? 1 : 0, sale_method: state.sale_method,
    sort: state.sort, dir: state.dir, page: state.page, per_page: PER_PAGE, mode: state.mode,
  });
}

function validListings(data) {
  return !!data && Array.isArray(data.items) && !!data.stats && typeof data.stats === "object"
    && Number.isFinite(Number(data.display_total ?? data.total));
}

// loading → done | error; only the request that is still current gets here.
function setLoadState(state, message = "") {
  const results = $("listings-results");
  results.setAttribute("aria-busy", String(state === "loading"));
  results.classList.toggle("is-stale", state === "error" && renderedOnce);
  $("listings-load-error").hidden = state !== "error";
  const status = $("listings-load-status");
  if (state === "loading") status.textContent = renderedOnce ? "Updating results…" : "Loading listings…";
  else if (state === "error") {
    status.textContent = renderedOnce ? "Could not update listings. Previous results are still shown."
                                      : "Could not load listings.";
    $("listings-load-error-text").textContent = renderedOnce
      ? `${message} The rows below belong to your previous view, not to the filters now selected.`
      : message;
  } else status.textContent = message;
}

async function load({after = "", navigation = false} = {}) {
  setLoadState("loading");
  let result = await resultsRead.run("/api/listings?" + listingParams(view).toString());
  if (result.kind === "obsolete") return result;      // a newer request owns the page now
  if (result.kind === "success" && !validListings(result.data)) {
    result = {kind: "error", message: "The server returned an unreadable response."};
  }
  if (result.kind === "error") {
    setLoadState("error", after === "saved"
      ? `Saved, but results could not be refreshed: ${result.message}` : result.message);
    if (!renderedOnce) {
      showResultsMessage(`<div class="empty"><h3>Listings could not be loaded</h3>
        <p>This is not an empty result. Use Retry listings above.</p></div>`);
      $("result-count").textContent = "";
    } else {
      $("result-count").textContent = `Previous results: ${lastCountText}`;
    }
    return result;
  }
  const data = result.data;
  const cash = data.cash || {};
  const note = $("cash-note");
  if (cash.set) {
    note.className = "small muted";
    note.textContent = `Cash you can lose is ${AS.money(cash.amount)}. A listing whose all-in cost is higher is hidden, unless you ☆ it.`;
  } else {
    note.className = "notice warn";
    note.textContent = "Cash you can lose is not set. Set it under Settings → Budget. Offers stay blocked until you do.";
  }
  lastFound = data.matched_total ?? data.found ?? data.total; lastCap = data.cap ?? 100;
  const shown = data.effective_page ?? data.page ?? view.page;
  if (shown !== view.page) {          // past the last page: the server showed the last one
    view = {...view, page: shown};
    writeViewURL(view, "replace");
  }
  // Where you are now, not when the request left: scrolling or moving focus
  // while it was on its way is not undone.
  const context = captureContext();
  shownItems.clear();
  renderStats(data.stats);
  renderResearch(data.research);
  renderResults(data.items, view.status === "hidden", !data.last_scrape);   // the table or the cards
  renderCount(data.display_total ?? data.total, lastFound, lastCap, !data.last_scrape);
  renderPages(data.display_total ?? data.total);
  renderedOnce = true;
  lastCountText = $("result-count").textContent;
  // A status change still on its way keeps its redrawn buttons waiting.
  statusPending.forEach(id => statusButtons(id).forEach(b => { b.disabled = true; }));
  let left = "";
  // A new page starts at its top, unless the property drawer holds focus.
  if (navigation && !propertyDrawer.panel.open) $("result-count").focus({preventScroll: true});
  else left = restoreContext(context);
  propertyDrawer.reconcile();
  setLoadState("done", left);
  return result;
}


async function copyText(text) {
  try { await navigator.clipboard.writeText(text); AS.toast("Copied: " + text); }
  catch { AS.toast("Select the text and copy it", "warn"); }
}

function renderPages(total) {
  const page = view.page, pages = Math.ceil(total / PER_PAGE), el = $("pagination");
  if (pages <= 1) { el.innerHTML = ""; return; }
  const first = Math.max(1, Math.min(page - 4, pages - 9)), last = Math.min(pages, first + 9);
  let html = `<button class="btn btn-sm" aria-label="Previous page" ${page === 1 ? "disabled" : ""} onclick="go(${page - 1})">‹</button>`;
  for (let i = first; i <= last; i++) html += `<button class="btn btn-sm ${i === page ? "active" : ""}" ${i === page ? 'aria-current="page"' : ""} onclick="go(${i})">${i}</button>`;
  el.innerHTML = html + `<button class="btn btn-sm" aria-label="Next page" ${page === pages ? "disabled" : ""} onclick="go(${page + 1})">›</button><span class="small muted" style="align-self:center">${countText()}</span>`;
}
let lastFound = 0, lastCap = 100;
function countText() {
  return lastFound > lastCap ? `${lastCap} shown of ${lastFound.toLocaleString("de-DE")} matching listings`
                             : `${lastFound} listings`;
}
function go(p) { cancelPending(); setView({...view, page: p}, "push", {navigation: true}); window.scrollTo(0, 0); }

// One request per listing at a time; only that listing's buttons wait.
const statusPending = new Set();
function statusButtons(id) { return [...document.querySelectorAll("[data-status-action]")].filter(b => b.dataset.id === id); }

// Resolves true once the server has the new status.
async function setStatus(id, status) {
  if (statusPending.has(id)) return false;
  const item = shownItems.get(id) || {};
  const previous = item.status || null;
  statusPending.add(id);
  statusButtons(id).forEach(b => { b.disabled = true; });
  let ok = false;
  try {
    if (status === "dismissed") {
      ok = await AS.dismiss(id);                  // the reason dialog; Cancel changes nothing
      if (ok) {
        AS.toast("Dismissed");
        offerUndo(id, item.title || "this listing", previous);
      }
    } else {
      await AS.api("/api/listings/status", {method: "POST", body: {id, status}});
      ok = true;
      AS.toast(status === "shortlisted" ? "Added to Offers" : "Restored");
    }
  } catch (e) {
    ok = false;                                   // AS.api said why; nothing on screen changed
  } finally {
    statusPending.delete(id);
    statusButtons(id).forEach(b => { b.disabled = false; });
  }
  if (ok) {
    detailsCache.invalidate(id);
    load({after: "saved"});                       // the server decides where it now belongs
  }
  return ok;
}

// Undo puts back the status it had (On Offers, or none) through the same API.
// The dismissal and its reason stay in the listing's status history.
let undoTimer = null;
function offerUndo(id, title, previous) {
  const box = $("status-undo"), button = $("status-undo-button");
  $("status-undo-text").textContent = `Dismissed “${title}”. Undo puts it back ${previous === "shortlisted" ? "on Offers" : "as an open listing"}; the dismissal and its reason stay in its history.`;
  box.hidden = false;
  clearTimeout(undoTimer);
  undoTimer = setTimeout(() => { box.hidden = true; }, 15000);
  button.onclick = async () => {
    button.disabled = true;
    try {
      await AS.api("/api/listings/status", {method: "POST", body: {id, status: previous}});
      detailsCache.invalidate(id);                // its status history now has the Undo too
      box.hidden = true;
      AS.toast(previous === "shortlisted" ? "Back on Offers" : "Restored");
      load({after: "saved"});
    } catch (e) { /* AS.api said why */ } finally { button.disabled = false; }
  };
}

// Native buttons in the headers: Enter and Space already work, one handler sorts once.
function nextSortDirection(column) {
  if (view.sort === column) return view.dir === "desc" ? "asc" : "desc";
  return column === "date_end" || column === "price" ? "asc" : "desc";
}
function updateSortHeaders() {
  $("sort-by").value = view.sort;
  $("sort-dir").value = view.dir;
  document.querySelectorAll("table.list th[data-sort]").forEach(header => {
    const column = header.dataset.sort;
    const active = column === view.sort;
    header.setAttribute("aria-sort", active ? (view.dir === "asc" ? "ascending" : "descending") : "none");
    const button = header.querySelector(".sort-button");
    if (!button) return;
    button.querySelector(".sort-indicator").textContent = active ? (view.dir === "asc" ? "↑" : "↓") : "↕";
    const next = nextSortDirection(column) === "asc" ? "ascending" : "descending";
    button.setAttribute("aria-label", `Sort by ${button.dataset.label}, ${next}`);
  });
}
document.querySelectorAll("table.list .sort-button").forEach(button => {
  button.addEventListener("click", () => {
    const column = button.dataset.sort;
    cancelPending();
    // Sorting starts again at page 1.
    setView({...(readControls() || view), dir: nextSortDirection(column), sort: column, page: 1});
    updateSortHeaders();
  });
});

// The labelled Sort by control reaches every sort, including the ones without a column header.
["sort-by", "sort-dir"].forEach(id => $(id).addEventListener("change", () => {
  cancelPending();
  setView({...(readControls() || view), sort: $("sort-by").value, dir: $("sort-dir").value, page: 1});
  updateSortHeaders();
}));

// Comfortable or Compact: presentation only, remembered in this browser.
const DENSITY_KEY = "auction-scanner:listings-density:v1";
function applyDensity(density) {
  $("listings-results").classList.toggle("results-density-compact", density === "compact");
  document.querySelectorAll("[data-density]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.density === density)));
}
document.querySelectorAll("[data-density]").forEach(button => button.addEventListener("click", () => {
  applyDensity(button.dataset.density);
  try { localStorage.setItem(DENSITY_KEY, button.dataset.density); } catch (e) { /* not kept */ }
}));
try { applyDensity(localStorage.getItem(DENSITY_KEY) === "compact" ? "compact" : "comfortable"); }
catch (e) { applyDensity("comfortable"); }

document.querySelectorAll("#modes button").forEach(button => {
  button.addEventListener("click", () => activateGoal(button));
});
AS.initGoalTabs($("modes"), activateGoal);

// Lists and boxes apply at once; search and numbers after a pause, or on Enter.
["f-country", "f-kind", "f-source", "f-sale-method", "f-status", "f-properties", "f-climate", "f-climate-exact"]
  .forEach(id => $(id).addEventListener("change", () => commitControls("push")));
$("f-search").addEventListener("input", () => searchDebounce());
["f-maxprice", "f-minscore"].forEach(id => $(id).addEventListener("input", () => {
  if (numbersValid()) numberDebounce(); else numberDebounce.cancel();
}));
["f-search", "f-maxprice", "f-minscore"].forEach(id => $(id).addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); commitControls("replace"); }
}));
$("reset-filters").addEventListener("click", resetFilters);
$("active-filters").addEventListener("click", e => {
  const button = e.target.closest(".chip-remove");
  if (button) removeFilter(button.dataset.key);
});
window.addEventListener("popstate", () => {
  cancelPending();
  view = normalizeView(rawViewFromURL(), view.mode);
  rememberView(view);
  restoreControls();
  load();
  propertyDrawer.restoreURL();                    // ?property= is not a filter: the drawer follows it
});
// A finished scan refreshes a quiet page in place. While you type, edit a
// note or a save is running it waits, and says so once.
let updatePending = false;
function busyEditing() {
  const active = document.activeElement;
  const typing = active && active.matches && active.matches("input, textarea, select") && !active.matches("[type=checkbox]");
  return typing || notes.hasUnsaved() || statusPending.size > 0;
}
document.addEventListener("scan-finished", () => {
  detailsCache.invalidate();                      // at once, even when the redraw waits for you
  if (updatePending) return;                      // one notice for any number of scans
  if (!busyEditing()) { load(); return; }
  updatePending = true;
  $("listings-update-notice").hidden = false;
  propertyDrawer.markStale();
  AS.announce("New results are available.");
});
$("listings-refresh").addEventListener("click", async () => {
  updatePending = false;
  $("listings-update-notice").hidden = true;
  notes.flushAll();                               // drafts stay in memory whatever happens
  load();
});
$("listings-retry").addEventListener("click", () => load());

// ── Saved searches (listings-saved-searches.js): named views kept in this browser only ──
// Nothing applies by itself: Apply changes the filters and sort once, Save and
// Update store what the controls show. A change made in another tab is only
// reported; it has to be reloaded before editing here.
const savedSearches = AuctionSavedSearches.createStore({
  // Through functions, so a browser that blocks storage fails inside the store, which says so.
  storage: {getItem: key => localStorage.getItem(key), setItem: (key, value) => localStorage.setItem(key, value)},
  validateView: validateSavedView,
  notify: showSavedSearches,
});
let appliedSearch = "";          // the saved search the view came from, until you change it
let nameAction = null;           // what the name dialog does with the name: returns what to say, or throws why not
let nameOpener = null;

function savedRecord(id) { return savedSearches.snapshot().records.find(r => r.id === id); }
function savedMessage(text, bad = false) {
  const message = $("saved-search-message");
  message.textContent = text;
  message.classList.toggle("warn", bad);
}

function syncSavedButtons(state = savedSearches.snapshot()) {
  const chosen = $("saved-search-select").value;
  const editable = state.loaded && !state.conflict;
  $("saved-search-select").disabled = !state.loaded;
  $("saved-search-apply").disabled = !state.loaded || !chosen;
  $("saved-search-create").disabled = !editable;
  ["saved-search-update", "saved-search-rename", "saved-search-delete"].forEach(id => { $(id).disabled = !editable || !chosen; });
}

function showSavedSearches(state) {
  const select = $("saved-search-select");
  const chosen = select.value || appliedSearch;
  // Names are text: options are built as nodes, never from markup.
  select.replaceChildren(new Option("Choose…", ""), ...state.records.map(r => new Option(r.name, r.id)));
  select.value = state.records.some(r => r.id === chosen) ? chosen : "";
  if (!state.records.some(r => r.id === appliedSearch)) appliedSearch = "";
  $("saved-search-warning").hidden = !state.warning;
  $("saved-search-warning").textContent = state.warning;
  syncSavedButtons(state);
  savedSearchViewChanged();
}

// Filters or sort no longer what the applied search holds: said beside it (the page and the open property do not count).
function savedSearchViewChanged() {
  const record = appliedSearch && savedRecord(appliedSearch);
  const saved = record && budgetedView({...goalDefaults(record.view.mode), ...record.view}).view;
  const same = saved && AuctionSavedSearches.FIELDS.every(key => saved[key] === view[key]);
  $("saved-search-modified").hidden = !record || same;
}

// The controls as a view to save, committed first so the results match what is saved.
function viewToSave() {
  cancelPending();
  const current = readControls();
  if (!current) {
    savedMessage("Correct the highlighted filters before saving this search. Nothing was saved.", true);
    return null;
  }
  commitControls("replace");
  return view;
}

function askName(title, initial, action, opener) {
  $("saved-search-name-title").textContent = title;
  $("saved-search-name").value = initial;
  $("saved-search-name-error").textContent = "";
  nameAction = action;
  nameOpener = opener;
  $("saved-search-name-dialog").showModal();
  $("saved-search-name").select();
}
function submitName() {
  if (!nameAction) return;
  try {
    const said = nameAction($("saved-search-name").value);
    nameAction = null;
    $("saved-search-name-dialog").close();
    savedMessage(said);
  } catch (error) {
    $("saved-search-name-error").textContent = error.message;   // the dialog stays open with what you typed
  }
}
$("saved-search-name-ok").addEventListener("click", submitName);
$("saved-search-name").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); submitName(); }
});
$("saved-search-name-dialog").addEventListener("close", () => {
  nameAction = null;
  if (nameOpener && nameOpener.isConnected) nameOpener.focus();
  nameOpener = null;
});

$("saved-search-select").addEventListener("change", () => syncSavedButtons());
$("saved-search-create").addEventListener("click", e => {
  const current = viewToSave();
  if (!current) return;
  askName("Save current search", "", name => {
    const id = savedSearches.create(name, current);
    appliedSearch = id;
    $("saved-search-select").value = id;
    syncSavedButtons();
    savedSearchViewChanged();
    return `Saved “${savedRecord(id).name}” in this browser.`;
  }, e.currentTarget);
});
$("saved-search-update").addEventListener("click", () => {
  const record = savedRecord($("saved-search-select").value);
  if (!record) return;
  if (!confirm(`Replace the filters and sort saved in “${record.name}” with the ones shown now?`)) {
    savedMessage("Not changed.");
    return;
  }
  const current = viewToSave();
  if (!current) return;
  try {
    savedSearches.update(record.id, current);
    appliedSearch = record.id;
    savedSearchViewChanged();
    savedMessage(`Updated “${record.name}” in this browser.`);
  } catch (error) { savedMessage(error.message, true); }
});
$("saved-search-rename").addEventListener("click", e => {
  const record = savedRecord($("saved-search-select").value);
  if (!record) return;
  askName("Rename saved search", record.name, name => {
    savedSearches.rename(record.id, name);
    return `Renamed to “${savedRecord(record.id).name}”.`;
  }, e.currentTarget);
});
$("saved-search-delete").addEventListener("click", () => {
  const record = savedRecord($("saved-search-select").value);
  if (!record) return;
  if (!confirm(`Delete the saved search “${record.name}” from this browser? This cannot be undone.`)) {
    savedMessage("Not deleted.");
    return;
  }
  try {
    savedSearches.remove(record.id);
    savedMessage(`Deleted “${record.name}”. The listings shown did not change.`);
  } catch (error) { savedMessage(error.message, true); }
});
$("saved-search-apply").addEventListener("click", () => {
  const record = savedRecord($("saved-search-select").value);
  if (!record) return;
  cancelPending();
  let saved;
  try { saved = savedSearches.apply(record.id); }
  catch (error) { savedMessage(`Not applied: ${error.message}`, true); return; }
  // Never quietly widened: a source or method this page does not offer now stops it.
  const missing = savedViewUnavailable(saved);
  if (missing.length) {
    savedMessage(`Not applied: ${missing.join(", ")} ${missing.length === 1 ? "is" : "are"} not available on this page now. Your filters were not changed.`, true);
    return;
  }
  const {view: next, note} = budgetedView({...goalDefaults(saved.mode), ...saved, page: 1});
  if (next.mode !== view.mode) {
    try { localStorage.setItem("listings-mode", next.mode); } catch (e) { /* private window */ }
  }
  appliedSearch = record.id;
  setView(next);                                  // one history entry, one read
  restoreControls();
  savedMessage(`Applied “${record.name}”.${note ? ` ${note}` : ""}`);
});
$("saved-search-reload").addEventListener("click", () => {
  const state = savedSearches.reload();
  savedMessage(state.loaded ? `Saved searches reloaded: ${state.records.length}.` : "", !state.loaded);
});
window.addEventListener("storage", e => {
  if (e.key !== AuctionSavedSearches.STORAGE_KEY && e.key !== null) return;
  savedSearches.externalChange();                 // said, never applied here by itself
});

function showViewNote(lines) {
  const note = $("view-note");
  note.hidden = !lines.length;
  note.textContent = lines.join(" ");
}

// Start: the sources and sale methods first, then the view (URL, else the
// last one remembered, else the defaults), then one request.
async function start() {
  [...$("f-country").options].forEach(o => allowed.countries.add(o.value));
  const notes = [];
  let metaFailed = false;
  try {
    const response = await fetch("/api/meta");
    if (!response.ok) throw new Error(`metadata ${response.status}`);
    const meta = await response.json();
    (meta.sources || []).forEach(s => {
      const o = document.createElement("option"); o.value = s; o.textContent = s; $("f-source").appendChild(o);
      allowed.sources.add(s);
    });
    (meta.sale_methods || []).forEach(m => {
      const o = document.createElement("option"); o.value = m.value; o.textContent = m.label;
      $("f-sale-method").appendChild(o);
      allowed.saleMethods.add(m.value);
      SALE_METHOD_LABEL[m.value] = m.label;
    });
  } catch (e) {
    metaFailed = true;
  }
  const fromURL = rawViewFromURL();
  const raw = Object.keys(fromURL).length ? fromURL : (readStoredView() || {});
  if (metaFailed) {
    // The lists of sources and sale methods are missing, not the request: a
    // source or method you asked for is kept (the server checks it) instead
    // of quietly widening to all.
    const kept = [];
    [["source", "f-source", allowed.sources, "Source"], ["sale_method", "f-sale-method", allowed.saleMethods, "Sale method"]]
      .forEach(([key, control, set, label]) => {
        const value = typeof raw[key] === "string" ? raw[key].trim() : "";
        if (!value) return;
        set.add(value);
        const o = document.createElement("option"); o.value = value; o.textContent = `${value} (not confirmed)`;
        $(control).appendChild(o);
        kept.push(`${label} “${value}”`);
      });
    notes.push("Sources and sale methods could not be loaded. "
      + (kept.length ? `Kept as asked: ${kept.join(", ")}. Reload the page to check them.` : "Those two lists show all for now."));
  }
  const dropped = [];
  view = normalizeView(raw, storedMode, dropped);
  if (dropped.length) notes.push(`Not available now, so not applied: ${dropped.join(", ")}.`);
  showViewNote(notes);
  savedSearches.reload();                         // after the lists above, which a saved search is checked against
  if (metaFailed) savedMessage("Sources and sale methods could not be loaded: a saved search that names one is not applied until you reload the page.", true);
  restoreControls();
  writeViewURL(view, "replace");
  await load();
  propertyDrawer.restoreURL();                    // a listing named in the address opens once the results are in
}
start();
