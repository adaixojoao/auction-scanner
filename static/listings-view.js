// listings-view.js — the Listings page: the view (goal, filters, sort, page) in the URL and remembered, and the controls.
// Plain scripts sharing one global scope, loaded in order by templates/listings.html
// (listings-view.js, listings-summary.js, listings-details.js, listings-notes.js, listings-drawer.js, listings.js).

const KIND_LABEL = {home: "home", urban_plot: "urban plot", rural_plot: "rural plot", other: "other"};
const PER_PAGE = 50;
const $ = id => document.getElementById(id);

// Each goal ranked on its own (scoring.MODES).
const MODE_NOTE = {
  both: "Homes that can be lived in and priced as an investment. Sea distance uses the stored pin and is only as exact as that pin; 10 km is the gate. A climate grade is not a full hazard survey. The number is the worse of the living and investment scores, planned on the middle of the repair range. A home with no local sale price stays in the research group. These candidates are not verified as ready to buy.",
  home: "A home to live in — not a plot: the sea within 1.5 km, not a poor climate, your five wishes, mild winters if the Atlantic current fails.",
  invest: "Homes to make money on: far below the local price all-in (taxes, fees and the work), a net rental yield, by the beach, forced or bank sales.",
  land: "Plots as an investment: cheap per hectare against what land goes for there, big enough, reachable, water on it — with the forestry upside on top.",
  forest: "Land for a forestry project: the cheaper the hectare, the better, and the sooner the sale can close. 10 ha is the project, 5 ha can be a first plot. Portugal first — you can visit it. No deadline of this year.",
};

// One short line per goal: the opening words of its note above, which stays in full under "How this ranking works".
const MODE_INTRO = {
  both: "Homes that can be lived in and priced as an investment.",
  home: "A home to live in — not a plot.",
  invest: "Homes to make money on.",
  land: "Plots as an investment.",
  forest: "Land for a forestry project.",
};

// ── The view: goal, filters, sort and page, in the URL and remembered ──
// Browsing state only: never notes, buyer details or anything secret.
const VIEW_STORAGE_KEY = "auction-scanner:listings-view:v1";
const VIEW_KEYS = ["mode", "country", "source", "kind", "status", "max_price", "min_score", "search",
                   "properties", "climate", "climate_exact", "sale_method", "sort", "dir", "page"];
const VALID_MODES = new Set(Object.keys(MODE_NOTE));
const VALID_KINDS = new Set(["", "home", "urban_plot", "rural_plot", "other"]);
const VALID_STATUSES = new Set(["", "shortlisted", "hidden"]);
const VALID_CLIMATES = new Set(["", "excellent", "good", "caution", "poor", "unknown"]);
const VALID_SORTS = new Set(["score", "country", "title", "price", "current_bid", "date_end"]);
// Filled from the page and /api/meta before the view is restored.
const allowed = {countries: new Set([""]), sources: new Set([""]), saleMethods: new Set([""])};
const SALE_METHOD_LABEL = {};

function goalDefaults(goal) {
  const budget = GOAL_BUDGETS[goal];
  return {mode: goal, country: "", source: "", kind: "", status: "", max_price: budget ? budget : "",
          min_score: 0, search: "", properties: true, climate: "", climate_exact: false,
          sale_method: "", sort: "score", dir: "desc", page: 1};
}

function boundedInteger(value, fallback, minimum, maximum) {
  if (value === "" || value == null) return fallback;
  const number = Number(value);
  if (!Number.isInteger(number)) return fallback;
  return Math.min(maximum, Math.max(minimum, number));
}

function booleanValue(value, fallback) {
  if (value === true || value === "1" || value === 1) return true;
  if (value === false || value === "0" || value === 0) return false;
  return fallback;
}

// Incoming URL or stored state against the goal's defaults. `dropped` collects
// what was asked for but cannot be shown, so it is said, not silently widened.
function normalizeView(raw, fallbackMode, dropped = []) {
  const mode = VALID_MODES.has(raw.mode) ? raw.mode : fallbackMode;
  const defaults = goalDefaults(mode);
  const pick = (key, ok, label) => {
    if (raw[key] == null) return defaults[key];
    if (ok(raw[key])) return raw[key];
    if (raw[key] !== "" && label) dropped.push(`${label} “${raw[key]}”`);
    return defaults[key];
  };
  const budget = GOAL_BUDGETS[mode] || 0;
  const priceInput = raw.max_price == null ? defaults.max_price : raw.max_price;
  let price = priceInput === "" ? "" : Number(priceInput);
  if (price !== "" && !(Number.isFinite(price) && price >= 0)) price = defaults.max_price;
  if (price !== "" && budget && price > budget) price = budget;      // the budget holds anyway
  return {
    mode,
    country: pick("country", v => allowed.countries.has(v), "Country"),
    source: pick("source", v => allowed.sources.has(v), "Source"),
    kind: pick("kind", v => VALID_KINDS.has(v)),
    status: pick("status", v => VALID_STATUSES.has(v)),
    max_price: price,
    min_score: boundedInteger(raw.min_score, defaults.min_score, 0, 100),
    search: typeof raw.search === "string" ? raw.search.trim() : defaults.search,
    properties: booleanValue(raw.properties, defaults.properties),
    climate: pick("climate", v => VALID_CLIMATES.has(v)),
    climate_exact: booleanValue(raw.climate_exact, defaults.climate_exact),
    sale_method: pick("sale_method", v => allowed.saleMethods.has(v), "Sale method"),
    sort: pick("sort", v => VALID_SORTS.has(v)),
    dir: raw.dir === "asc" || raw.dir === "desc" ? raw.dir : defaults.dir,
    page: boundedInteger(raw.page, defaults.page, 1, 1000000),
  };
}

function rawViewFromURL() {
  const params = new URLSearchParams(location.search);
  const raw = {};
  VIEW_KEYS.forEach(key => { if (params.has(key)) raw[key] = params.get(key); });
  return raw;
}

function readStoredView() {
  try {
    const raw = localStorage.getItem(VIEW_STORAGE_KEY);
    const value = raw ? JSON.parse(raw) : null;
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch (error) {
    return null;
  }
}

function rememberView(state) {
  try { localStorage.setItem(VIEW_STORAGE_KEY, JSON.stringify(state)); }
  catch (error) { /* URL state still works. */ }
}

function viewParameters(state) {
  const params = new URLSearchParams();
  VIEW_KEYS.forEach(key => {
    const value = state[key];
    if (value == null) return;
    // Keep empty values explicit: an empty maximum price must not
    // disappear and become the default after a reload.
    params.set(key, typeof value === "boolean" ? (value ? "1" : "0") : String(value));
  });
  return params;
}

function writeViewURL(state, historyMode = "push") {
  const url = new URL(location.href);
  VIEW_KEYS.forEach(key => url.searchParams.delete(key));
  viewParameters(state).forEach((value, key) => url.searchParams.set(key, value));
  const method = historyMode === "replace" ? "replaceState" : "pushState";
  if (url.href !== location.href) history[method]({listings: true}, "", url);
  rememberView(state);
}

function debounce(callback, delay = 350) {
  let timer;
  function scheduled(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => { timer = null; callback(...args); }, delay);
  }
  scheduled.cancel = () => { clearTimeout(timer); timer = null; };
  return scheduled;
}

let storedMode = "both";
try { storedMode = localStorage.getItem("listings-mode") || "both"; } catch (e) { /* private window */ }
if (!MODE_NOTE[storedMode]) storedMode = "home";
let view = goalDefaults(storedMode);

function showMode() {
  document.querySelectorAll("#modes button").forEach(button => {
    const selected = button.dataset.mode === view.mode;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    if (selected) $("goal-results").setAttribute("aria-labelledby", button.id);
  });
  $("mode-intro").textContent = MODE_INTRO[view.mode];
  $("mode-note").textContent = MODE_NOTE[view.mode];
}

// ── Controls ↔ view ──
// Native validity, said next to the field; nothing is rewritten while typing.
function checkNumber(input, errorId, message) {
  const error = $(errorId);
  const ok = input.disabled || input.checkValidity();
  input.setAttribute("aria-invalid", String(!ok));
  error.hidden = ok;
  error.textContent = ok ? "" : message();
  return ok;
}

function numbersValid() {
  const budget = GOAL_BUDGETS[view.mode] || 0;
  const price = checkNumber($("f-maxprice"), "f-maxprice-error", () => budget
    ? `Max listed price: an amount from €0 to this goal's budget, ${AS.money(budget)}.`
    : "Max listed price: an amount of €0 or more.");
  const score = checkNumber($("f-minscore"), "f-minscore-error", () => "Min score: a whole number from 0 to 100.");
  return price && score;
}

// The view the controls show, or null while a number is not valid.
function readControls() {
  if (!numbersValid()) return null;
  const price = $("f-maxprice").value;
  const score = $("f-minscore").value;
  return {
    ...view,
    country: $("f-country").value, source: $("f-source").value, kind: $("f-kind").value,
    status: $("f-status").value, sale_method: $("f-sale-method").value,
    max_price: price === "" ? "" : Number(price),
    min_score: score === "" ? 0 : Number(score),
    search: $("f-search").value.trim(), properties: $("f-properties").checked,
    climate: $("f-climate").value, climate_exact: $("f-climate-exact").checked,
  };
}

function restoreControls() {
  $("f-country").value = view.country;
  $("f-source").value = view.source;
  $("f-sale-method").value = view.sale_method;
  $("f-kind").value = view.kind;
  $("f-status").value = view.status;
  $("f-search").value = view.search;
  $("f-properties").checked = view.properties;
  $("f-climate").value = view.climate;
  $("f-climate-exact").checked = view.climate_exact;
  $("f-minscore").value = String(view.min_score);
  const price = $("f-maxprice"), budget = GOAL_BUDGETS[view.mode] || 0;
  price.value = view.max_price === "" ? "" : String(view.max_price);
  price.disabled = view.mode === "both";
  if (budget) price.max = String(budget); else price.removeAttribute("max");
  $("f-maxprice-hint").textContent = view.mode === "both"
    ? "Max listed price is not used on Live and invest: the all-in estimate against your cash is the ceiling."
    : "Max listed price is the listed or base figure as stored, not the current bid or the estimated all-in total."
      + (budget ? ` This goal's budget, ${AS.money(budget)}, is the most it shows; an empty field means the budget.` : "");
  if (view.climate || view.climate_exact) $("f-climate").closest("details").open = true;
  numbersValid();
  showMode();
  updateSortHeaders();
  renderChips();
}

// ── Active filters ──
function optionText(id, value) {
  const option = [...$(id).options].find(o => o.value === value);
  return option ? option.textContent.trim() : value;
}

function activeFilters() {
  const d = goalDefaults(view.mode), chips = [];
  const add = (key, label, value) => chips.push({key, label, value});
  if (view.country !== d.country) add("country", "Country", optionText("f-country", view.country));
  if (view.source !== d.source) add("source", "Source", view.source);
  if (view.kind !== d.kind) add("kind", "Type", optionText("f-kind", view.kind));
  if (view.status !== d.status) add("status", "Status", optionText("f-status", view.status));
  if (view.mode !== "both" && view.max_price !== d.max_price)
    add("max_price", "Max listed price", view.max_price === "" ? "any (the goal's budget holds)" : AS.money(view.max_price));
  if (view.min_score !== d.min_score) add("min_score", "Minimum score", String(view.min_score));
  if (view.climate !== d.climate) add("climate", "Climate", optionText("f-climate", view.climate));
  if (view.climate_exact !== d.climate_exact) add("climate_exact", "Exact climate data", "");
  if (view.sale_method !== d.sale_method) add("sale_method", "Sale method", SALE_METHOD_LABEL[view.sale_method] || view.sale_method);
  if (view.search !== d.search) add("search", "Search", view.search);
  if (view.properties !== d.properties) add("properties", "Property only", "off");
  return chips;
}

function renderChips() {
  const chips = activeFilters();
  $("active-filters").innerHTML = chips.length
    ? `<span class="small muted">Active filters:</span> ` + chips.map(c => {
        const text = c.value ? `${c.label}: ${c.value}` : c.label;
        return `<span class="chip">${AS.esc(text)}<button type="button" class="chip-remove" data-key="${AS.esc(c.key)}"
          aria-label="Remove ${AS.esc(c.label.toLowerCase())} filter${c.value ? `: ${AS.esc(c.value)}` : ""}">×</button></span>`;
      }).join("")
    : `<span class="small muted">Default filters</span>`;
}

// ── Changing the view ──
const searchDebounce = debounce(() => commitControls("replace"));
const numberDebounce = debounce(() => commitControls("replace"));
function cancelPending() { searchDebounce.cancel(); numberDebounce.cancel(); }

function sameView(a, b) { return viewParameters(a).toString() === viewParameters(b).toString(); }

function setView(next, historyMode = "push", loadOptions = {}) {
  const changed = !sameView(next, view);
  view = next;
  writeViewURL(view, historyMode);
  renderChips();
  if (changed) load(loadOptions);
}

// A filter changed: back to page 1. Invalid numbers wait for a valid value.
function commitControls(historyMode = "push") {
  cancelPending();
  const next = readControls();
  if (!next) return;
  const filtersSame = sameView({...next, page: 1}, {...view, page: 1});
  setView({...next, page: filtersSame ? view.page : 1}, historyMode);
}

function activateGoal(button) {
  const nextMode = button.dataset.mode;
  if (nextMode === view.mode) { showMode(); return; }
  cancelPending();
  const current = readControls() || view;
  const next = {...current, mode: nextMode, page: 1};
  // A maximum left at the old goal's budget follows the goal; one you typed
  // stays, within the new goal's budget.
  const budget = GOAL_BUDGETS[nextMode] || 0;
  if (current.max_price === goalDefaults(view.mode).max_price) next.max_price = goalDefaults(nextMode).max_price;
  else if (next.max_price !== "" && budget && next.max_price > budget) next.max_price = budget;
  try { localStorage.setItem("listings-mode", nextMode); } catch (e) { /* private window */ }
  setView(next);
  restoreControls();
}

function resetFilters() {
  cancelPending();
  setView(goalDefaults(view.mode));
  restoreControls();
  AS.announce("Filters reset for this goal");
}

function removeFilter(key) {
  cancelPending();
  setView({...view, [key]: goalDefaults(view.mode)[key], page: 1});
  restoreControls();
}
