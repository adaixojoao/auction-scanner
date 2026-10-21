// listings-details.js — the Listings page: rendering rows and research cards, the details shown in the property drawer and their freshness, and keeping your place across a refresh.
// Plain scripts sharing one global scope, loaded in order by templates/listings.html
// (listings-saved-searches.js, listings-view.js, listings-summary.js, listings-details.js,
//  listings-cards.js, listings-notes.js, listings-drawer.js, listings-comparison-store.js,
//  listings-comparison-render.js, listings-comparison.js, listings.js).

// ── Keeping your place across a refresh (by listing ID, never by DOM ID) ──
// One registry for every surface: the results table, the results as cards
// (listings-cards.js) and the research cards.
const SURFACE_SELECTOR = {
  table: "#rows tr[data-id]",
  cards: "#listing-cards article.listing-card",
  research: "#research-candidates article.research-card",
};
function surfaceItems(surface) { return [...document.querySelectorAll(SURFACE_SELECTOR[surface])]; }
function anchorFor(surface, id) { return surfaceItems(surface).find(el => el.dataset.id === id); }
function rowFor(id) { return anchorFor("table", id); }
function cardFor(id) { return anchorFor("research", id); }
function listingCardFor(id) { return anchorFor("cards", id); }
// The table and the cards are one surface in two layouts: a place in one is found in the other.
function shownSurface(surface) { return surface === "research" ? "research" : layout; }

function captureContext() {
  let focus = null;
  const active = document.activeElement;
  const surfaceOf = el => Object.keys(SURFACE_SELECTOR).find(key => el.matches(SURFACE_SELECTOR[key]));
  const holder = active && active.closest ? active.closest(Object.values(SURFACE_SELECTOR).join(", ")) : null;
  if (holder) {
    const surface = surfaceOf(holder);
    const id = holder.dataset.id;
    focus = {surface, id, role: active.dataset.role || "", index: surfaceItems(surface).findIndex(el => el.dataset.id === id)};
  }
  let scroll = null;
  const top = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--topbar-height")) || 0;
  for (const el of document.querySelectorAll(Object.values(SURFACE_SELECTOR).join(", "))) {
    const box = el.getBoundingClientRect();
    if (box.bottom > top) { scroll = {surface: surfaceOf(el), id: el.dataset.id, top: box.top}; break; }
  }
  return {focus, scroll};
}

// Puts scroll and focus back after a redraw. Details live in the property
// drawer (listings-drawer.js), which no redraw replaces: while it is open the
// background keeps its scroll and focus stays in the drawer.
function restoreContext(ctx) {
  if (ctx.scroll) {
    const anchor = anchorFor(shownSurface(ctx.scroll.surface), ctx.scroll.id);
    if (anchor) window.scrollBy(0, anchor.getBoundingClientRect().top - ctx.scroll.top);
  }
  if (propertyDrawer.panel.open) return "";
  // Focus only comes back when it was in the results and nobody moved it since.
  const active = document.activeElement;
  const inResults = el => $("rows").contains(el) || $("listing-cards").contains(el) || $("research-candidates").contains(el);
  if (ctx.focus && (!active || active === document.body || inResults(active))) {
    const {id, role, index} = ctx.focus;
    const surface = shownSurface(ctx.focus.surface);
    const anchor = anchorFor(surface, id);
    let target = anchor ? [...anchor.querySelectorAll("[data-role]")].find(x => x.dataset.role === role) : null;
    if (!target) {
      // The listing left: its neighbour on the same surface, else the count line.
      const items = surfaceItems(surface);
      const near = anchor || items[Math.min(Math.max(index, 0), items.length - 1)];
      target = near ? near.querySelector('[data-role="details"]') : $("result-count");
    }
    if (target) target.focus({preventScroll: true});
  }
  return "";
}

function renderCount(displayed, matched, cap, neverScanned = false) {
  const el = $("result-count");
  if (!displayed) {
    el.textContent = neverScanned ? "No scan data is available yet." : "No eligible listings match these filters.";
    return;
  }
  const first = (view.page - 1) * PER_PAGE + 1, last = Math.min(view.page * PER_PAGE, displayed);
  const n = v => v.toLocaleString("en-GB");
  el.textContent = matched > cap
    ? `Showing ${n(first)}–${n(last)} of ${n(displayed)} displayed matches. ${n(matched)} match your filters; display limit: ${n(cap)}.`
    : `Showing ${n(first)}–${n(last)} of ${n(displayed)} matches.`;
}

function renderStats(s) {
  // Three counts, each saying what it counts; source problems and the rest fold away.
  const n = v => Number(v || 0).toLocaleString("en-GB");
  const capped = lastFound > lastCap;
  const hidden = Object.entries(s.hidden || {}).map(([k, v]) => `${v} ${k}`).join(", ");
  const failing = Number(s.sources_failing || 0), quiet = Number(s.sources_quiet || 0);
  $("stats").innerHTML = `
    <div class="stat"><div class="label">Matching</div><div class="value">${n(lastFound)}</div>
      <div class="small muted">${capped ? `Display limit: the best ${n(lastCap)} are shown` : "All of them are shown"}</div></div>
    <div class="stat"><div class="label">New (24 h)</div><div class="value ok">${n(s.new_today)}</div>
      <div class="small muted">Among open matching listings</div></div>
    <div class="stat"><div class="label">On Offers</div><div class="value">${n(s.shortlisted)}</div>
      <div class="small muted">Among open matching listings, not your whole shortlist</div></div>`;
  $("stats-more").innerHTML = `
    <details class="source-health">
      <summary><span class="${failing || quiet ? "bad" : "ok"}">Source health: ${failing ? `${n(failing)} failing` : "none failing"}${quiet ? `, ${n(quiet)} with no good read` : ""}</span></summary>
      <p class="small">${n(failing)} source${failing === 1 ? "" : "s"} failing and ${n(quiet)} with no good read lately, across all sources (not only these filters). <a href="/sources">See Sources</a>.</p>
    </details>
    ${hidden ? `<p class="small muted">Hidden among these matches: ${AS.esc(hidden)}.</p>` : ""}
    ${s.unchecked ? `<p class="small muted">Not checked: ${n(s.unchecked)} listings over €${Math.round(s.check_max_price).toLocaleString("en-GB")} are not looked at in detail.</p>` : ""}`;
}

function candidateCard(it) {
  const plan = it.planning || {};
  const p = it.presentation || {};
  const work = plan.renovation;
  const point = plan.renovation_point === "middle" ? "middle of the repair range"
    : plan.renovation_point === "low" ? "low end of the repair range" : "repairs not priced";
  const link = it.url
    ? AS.link(it.url, it.title || "Listing")
    : `<span>${AS.esc(it.title || "Listing")}</span> <span class="small muted">lookup ${AS.esc(it.lookup || it.id)}</span>`;
  const starred = it.status === "shortlisted";
  const id = AS.esc(it.id);
  return `<article class="research-card" data-id="${id}">
    <h3>${link}</h3>
    <p class="small">Living ${it.live_score == null ? "—" : AS.esc(String(it.live_score))}
      · Investment ${it.invest_score == null ? "—" : AS.esc(String(it.invest_score))}</p>
    <div class="research-figures">
      <div><div class="small muted">Price basis</div>${priceSummaryHtml(p.price)}</div>
      <div><div class="small muted">Estimated all-in</div>${allInSummaryHtml(p.all_in)}</div>
    </div>
    <p class="small">Affordability uses the ${AS.esc(point)}${work ? ` (${work.low == null ? "low end not priced" : work.low ? AS.money(work.low) : "€0"}–${work.high == null ? "high end not priced" : AS.money(work.high)})` : ""}.</p>
    ${plan.total_required != null ? `<p class="small">Required total with the reserve ${AS.money(plan.total_required)}: planning ${AS.money(plan.planning_amount)} + reserve ${AS.money(plan.reserve || 0)}. A policy total, not the estimated range.</p>` : ""}
    <p class="small">${AS.esc(plan.note || "")}</p>
    <p class="small">${AS.esc(it.valuation || "")}</p>
    <div class="small muted">Checks</div>${checksSummaryHtml(p.checks)}${investigationSummaryHtml(it.investigation)}
    <div class="small muted">Deadline</div>${deadlineSummaryHtml(p.deadline)}
    <details class="small result-more"><summary>Evidence gaps (${(it.gaps || []).length})</summary>
      <ul class="small">${(it.gaps || []).map(g => `<li>${AS.esc(g)}</li>`).join("")}</ul></details>
    <p>
      <button class="btn btn-sm" aria-label="Details: ${AS.esc(it.title || "this listing")}" aria-expanded="false" data-id="${id}" data-role="details" onclick="openCandidate(this.dataset.id, this)">Details</button>
      <button class="btn btn-sm" aria-label="${starred ? "Remove from shortlist" : "Shortlist"}: ${AS.esc(it.title || "this listing")}" aria-pressed="${starred}" data-id="${id}" data-role="star" data-status-action onclick="setStatus(this.dataset.id, ${starred ? "null" : "'shortlisted'"})">${starred ? "On Offers" : "Shortlist"}</button>
      ${compareButtonHtml(it.id, it.title)}
    </p>
  </article>`;
}

// Open unless you closed it; remembered like the view (a browsing preference only).
const RESEARCH_OPEN_KEY = "auction-scanner:listings-research-open:v1";
let researchOpen = true;
try { researchOpen = localStorage.getItem(RESEARCH_OPEN_KEY) !== "0"; } catch (e) { /* private window */ }

function renderResearch(research) {
  const box = $("research-candidates");
  if (view.mode !== "both" || !research) { box.hidden = true; box.innerHTML = ""; return; }
  const all = [...(research.valued || []), ...(research.unvalued || [])];
  all.forEach(it => shownItems.set(it.id, it));
  const distinct = new Set(all.map(it => it.id)).size;
  const valued = (research.valued || []).map(candidateCard).join("");
  const unvalued = (research.unvalued || []).map(candidateCard).join("");
  box.hidden = false;
  // Collapsing only hides the cards: open panels and unsaved notes stay in the page.
  box.innerHTML = `<details id="research-details" ${researchOpen ? "open" : ""}>
    <summary><h2>${AS.esc(research.heading)} <span class="small muted">(${distinct} candidate${distinct === 1 ? "" : "s"})</span></h2>
      <span class="small warn research-warning">Not verified as ready to buy.</span></summary>
    <p class="small muted">These can also appear in the table below; they are not added to its count. ${AS.esc(research.duplicates || "")}</p>
    <h3 class="small">Valued — a local median is used as a planning figure</h3>
    ${valued ? `<div class="research-grid">${valued}</div>` : `<p class="small muted">No priced research candidate in this view.</p>`}
    ${unvalued ? `<h3 class="small">Unvalued — investment value not established</h3><div class="research-grid">${unvalued}</div>` : ""}
  </details>`;
  $("research-details").addEventListener("toggle", e => {
    researchOpen = e.target.open;
    try { localStorage.setItem(RESEARCH_OPEN_KEY, researchOpen ? "1" : "0"); } catch (err) { /* not kept */ }
  });
}

// Details last loaded, per goal and listing (listing-details-cache.js). They
// are shown as current only while the listing's detail_token from the latest
// results is unchanged (a scan, status, score or evidence revision changes it)
// and they are younger than DETAIL_TTL_MS; otherwise they show as earlier
// details while fresh ones load. A finished scan or a change made here
// invalidates them at once. Note drafts live in the note store, not here.
const DETAIL_TTL_MS = 10 * 60 * 1000;
const detailsCache = AuctionDetailsCache.createCache({read: AuctionDetailsCache.read, ttl: DETAIL_TTL_MS});
const panelReads = new WeakMap();   // the drawer's read in flight: {controller, generation}
const panelShown = new WeakMap();   // what the drawer shows now: {mode, id, data}
function detailToken(id) { return (shownItems.get(id) || {}).detail_token || ""; }

function freshnessHtml(id, state, message = "") {
  const title = AS.esc((shownItems.get(id) || {}).title || "this listing");
  const text = state === "refreshing" ? "Showing details loaded earlier while they update…"
    : state === "failed" ? `These details may be out of date: they could not be refreshed (${AS.esc(message)}).` : "";
  return `<p class="small muted details-freshness" data-state="${state}">${text}
    <button type="button" class="btn btn-sm" data-role="refresh-details" aria-label="Refresh details: ${title}">Refresh details</button></p>`;
}

// Redraws a panel without taking the caret out of a note you are typing in.
function renderDetailsBody(body, id, data, state, message = "") {
  const active = document.activeElement;
  const typing = active && body.contains(active) && active.matches("textarea.d-note")
    ? {start: active.selectionStart, end: active.selectionEnd, scroll: active.scrollTop} : null;
  body.innerHTML = freshnessHtml(id, state, message) + detailsHtml(data);
  const panel = body.closest("#property-drawer");
  if (panel) panelShown.set(panel, {mode: view.mode, id, data});
  if (typing) {
    const field = body.querySelector("textarea.d-note");
    if (field) { field.focus({preventScroll: true}); field.setSelectionRange(typing.start, typing.end); field.scrollTop = typing.scroll; }
  }
}

// Fills the drawer's details for one listing. A closed drawer, another listing
// or another goal ignores a late answer; a failure says so with a Retry.
// Returns the details shown, or null.
async function fillPanel(panel, id, {fresh = false} = {}) {
  const mode = view.mode, token = detailToken(id);
  const body = panel.querySelector(".details-body");
  const current = fresh ? null : detailsCache.cached(mode, id, token);
  if (current) { renderDetailsBody(body, id, current, "current"); return current; }
  // Details already on screen for this listing count as earlier ones, even
  // once the bounded cache has dropped them: the note you are typing in stays.
  const shown = panelShown.get(panel);
  const earlier = detailsCache.earlier(mode, id) || (shown && shown.mode === mode && shown.id === id ? shown.data : null);
  if (earlier) renderDetailsBody(body, id, earlier, "refreshing");
  else body.innerHTML = `<p class="small muted details-loading">Loading details…</p>`;
  // One read at a time: a newer one, or closing the drawer, supersedes it.
  const previous = panelReads.get(panel);
  if (previous) previous.controller.abort();
  const read = {controller: new AbortController(), generation: (previous ? previous.generation : 0) + 1};
  panelReads.set(panel, read);
  const result = await detailsCache.load(mode, id, token, {fresh: true, signal: read.controller.signal});
  // The drawer stays in the page when closed, so being connected is not enough.
  if (result.kind === "obsolete" || !panel.isConnected || (panel.matches("dialog") && !panel.open)
      || mode !== view.mode || panelReads.get(panel) !== read || panel.dataset.for !== id) return null;
  if (result.kind === "error") {
    if (earlier) { renderDetailsBody(body, id, earlier, "failed", result.message); return earlier; }
    const title = (shownItems.get(id) || {}).title || "this listing";
    body.innerHTML = `<div class="notice warn details-error"><p>Could not load details: ${AS.esc(result.message)}</p>
      <button type="button" class="btn btn-sm" data-role="retry-details" aria-label="Retry details: ${AS.esc(title)}">Retry details</button></div>`;
    body.querySelector("button").addEventListener("click", () => fillPanel(panel, id, {fresh: true}));
    return null;
  }
  renderDetailsBody(body, id, result.data, "current");
  return result.data;
}

document.addEventListener("click", e => {
  const button = e.target.closest && e.target.closest('[data-role="refresh-details"]');
  if (!button) return;
  const panel = button.closest("#property-drawer");
  if (panel) fillPanel(panel, panel.dataset.for, {fresh: true});
});

// A research card's Details opens the same drawer as the table's.
function openCandidate(id, button) { return propertyDrawer.open(id, button); }

function scanSuggestion() {
  // What a scan would cover, from what is selected; nothing runs until you click.
  if (view.source) {
    return {label: `Scan ${view.source}`, scope: `Scans only the source ${view.source}.`, body: {source: view.source}};
  }
  if (view.country) {
    const name = optionText("f-country", view.country);
    return {label: `Scan ${name}`, scope: `Scans every ${name} source.`, body: {countries: [view.country]}};
  }
  return {label: "Scan all countries", scope: "Scans every source in every country; this takes a while.", body: {countries: null}};
}

// No results, said the same way in the table and the cards; the scan waits for its button.
function emptyResultsHtml(hiddenView, neverScanned) {
  const scan = hiddenView ? null : scanSuggestion();
  return {scan, html: `<div class="empty">
      <h3>${neverScanned ? "No scan data yet" : "No listings here"}</h3>
      <p>${hiddenView ? "Nothing is hidden with these filters."
        : neverScanned ? "No scan has run yet, so there is nothing to show."
        : "No eligible listings match these filters. Change the filters, or scan for new listings."}</p>
      ${scan ? `<p class="small muted" id="scan-scope">${AS.esc(scan.scope)}</p>
        <p style="margin-top:10px"><button class="btn btn-primary" type="button" id="empty-scan" aria-describedby="scan-scope">${AS.esc(scan.label)}</button></p>` : ""}
    </div>`};
}
function wireEmptyScan(scan) {
  if (scan) $("empty-scan").addEventListener("click", () => AS.startScan(scan.body).catch(() => {}));
}

function renderRows(items, hiddenView, neverScanned = false) {
  if (!items.length) {
    const empty = emptyResultsHtml(hiddenView, neverScanned);
    $("rows").innerHTML = `<tr><td colspan="${TABLE_COLUMNS}">${empty.html}</td></tr>`;
    wireEmptyScan(empty.scan);
    return;
  }
  $("rows").innerHTML = items.map(it => {
    const loc = [it.concelho, it.district].filter(Boolean).join(", ");
    const p = it.presentation || {};
    const badges = listingBadgesHtml(it);
    const signals = listingSignalsHtml(it);
    const starred = it.status === "shortlisted";
    // IDs go through data-id, never into the onclick source.
    const id = AS.esc(it.id);
    const propertyLabel = AS.esc(it.title || "this listing");
    const actions = it.status === "dismissed"
      ? `<button class="btn btn-sm" aria-label="Restore ${propertyLabel}" data-id="${id}" data-role="restore" data-status-action onclick="setStatus(this.dataset.id, null)">Restore</button>`
      : `<button class="icon-btn ${starred ? "on" : ""}" aria-label="${starred ? "Remove from shortlist" : "Shortlist"}: ${propertyLabel}" aria-pressed="${starred}" title="${starred ? "Remove from Offers" : "Add to Offers"}" data-id="${id}" data-role="star" data-status-action onclick="setStatus(this.dataset.id, ${starred ? "null" : "'shortlisted'"})">${starred ? "★" : "☆"}</button>
         <button class="icon-btn" aria-label="Dismiss ${propertyLabel}" title="Dismiss" data-id="${id}" data-role="dismiss" data-status-action onclick="setStatus(this.dataset.id, 'dismissed')">✕</button>`;
    // Citius has no page per sale: its title opens the details, with the way to find it.
    const titleLink = it.source === "citius"
      ? `<a href="#" aria-expanded="false" data-id="${id}" data-role="title" onclick="toggleDetails(this); return false">${AS.esc(it.title || "?")}</a>`
      : AS.link(it.url, it.title || "?");
    return `<tr class="${it.hidden_reason ? "dim" : ""}" data-id="${id}">
      <td class="t-property"><div class="result-property">${thumbnailHtml(p.thumbnail, it.title)}
        <div><div class="result-title">${titleLink}</div>
          <div class="small muted">${AS.flag(it.country)} ${AS.esc(loc)}</div>
          <div class="small"><span class="src">${AS.esc(it.source)}</span>${badges}</div>
          ${view.mode === "home" ? AS.wishesHtml(it.wishes, true) : ""}</div></div></td>
      <td class="t-price">${priceSummaryHtml(p.price)}</td>
      <td class="t-allin">${allInSummaryHtml(p.all_in)}</td>
      <td class="t-why">${AS.scoreBadge(it.score, it.rank, it.excellent)}${signals}${reasonsSummaryHtml(it.reasons)}</td>
      <td class="t-checks">${checksSummaryHtml(p.checks)}${investigationSummaryHtml(it.investigation)}</td>
      <td class="t-deadline">${deadlineSummaryHtml(p.deadline)}</td>
      <td class="nowrap"><button class="icon-btn" aria-label="Details: ${propertyLabel}" aria-expanded="false" title="Details" data-id="${id}" data-role="details" onclick="toggleDetails(this)">ⓘ</button>${actions}
        ${compareButtonHtml(it.id, it.title)}</td>
    </tr>`;
  }).join("");
}

// ── Details: everything known about one listing (listing_info.py), in the property drawer ──
// The ⓘ button and a Citius title open it; no details are drawn inside the table.
function toggleDetails(el) { return propertyDrawer.open(el.dataset.id, el); }

function factValue(f) {
  const v = AS.esc(f.value);
  if (/e-mail/i.test(f.label) && /^[^\s@]+@[^\s@]+$/.test(f.value)) return `<a href="mailto:${v}">${v}</a>`;
  if (/phone/i.test(f.label)) return `<a href="tel:${AS.esc(f.value.replace(/[^0-9+]/g, ""))}">${v}</a>`;
  return v;
}

function costsHtml(c) {
  if (!c) return "";
  // Missing is not zero: an amount the estimate lacks says so.
  const eur = v => v == null || v === "" ? "Not established" : v === 0 ? "\u20ac0" : AS.money(v);
  const row = (k, v, note) => `<div class="fact"><span class="k">${AS.esc(k)}</span><span class="v">${v}
      <span class="small muted">${AS.esc(note)}</span></span></div>`;
  const lines = c.lines.map(l => row(l.label, eur(l.amount), l.note)).join("");
  const work = c.renovation ? row("Work", eur(c.renovation.low) + "\u2013" + eur(c.renovation.high), c.renovation.note) : "";
  const total = `${eur(c.total)} to own it`
    + (c.all_in ? ` \u00b7 ${eur(c.all_in.low)}\u2013${eur(c.all_in.high)} all-in with the work` : "");
  const rent = c.rent ? `<div class="cost-total">Rents for about ${eur(c.rent.monthly)}/month \u00b7
      ${c.rent.net_yield_pct}% a year after running costs and empty months \u00b7 paid back in ${c.rent.payback_years} years</div>
      <p class="small muted">${AS.esc(c.rent.note)}</p>` : "";
  return `<div class="guide">
    <b>Estimated costs: what it really costs</b> <span class="small muted">on ${AS.esc(c.basis)}, ${eur(c.base)}</span>
    <div class="facts">${lines}${work}</div>
    <div class="cost-total">${total}</div>
    <p class="small muted">${AS.esc(c.note)}</p>${rent}</div>`;
}

function projectHtml(d) {
  const p = d.project;
  if (!p) return "";
  const lines = (p.lines || []).map(l =>
    `<div class="fact"><span class="k">${AS.esc(l.label)}</span><span class="v">${AS.esc(l.value)}</span>
     <span class="small muted">${AS.esc(l.note || "")}</span></div>`).join("");
  const steps = (p.steps || []).map(t => `<li>${AS.esc(t)}</li>`).join("");
  const unknowns = (p.unknowns || []).length
    ? `<p class="small muted">Not known yet: ${AS.esc(p.unknowns.join("; "))}.</p>` : "";
  return `<div class="guide">
    <b>Forestry project on the land budget</b>
    <p class="small">${AS.esc(p.headline || "")}</p>
    <div class="facts">${lines}</div>
    <ol class="small">${steps}</ol>
    ${unknowns}
    <p class="small muted">${AS.esc(p.note || "")}</p>
  </div>`;
}

function stewardshipHtml(d) {
  const s = d.stewardship;
  if (!s || !s.available) return "";
  const id = encodeURIComponent(d.id);
  const list = (title, items, map) => items && items.length
    ? `<div class="k" style="margin-top:8px">${AS.esc(title)}</div><ul class="small">${items.map(map).join("")}</ul>` : "";
  return `<div class="guide stewardship">
    <b>Climate &amp; Land Stewardship Plan</b>
    <span class="small muted">${s.area_ha != null ? s.area_ha + " ha · " : ""}climate ${AS.esc(s.climate_grade || "?")}</span>
    <p class="small">${AS.esc(s.suitability || "")}</p>
    ${s.location_warning ? `<div class="notice warn">${AS.esc(s.location_warning)}</div>` : ""}
    ${list("Risks", s.risks, r => `<li><b>${AS.esc(r.title)}:</b> ${AS.esc(r.text)}</li>`)}
    ${list("Opportunities", s.opportunities, r => `<li><b>${AS.esc(r.title)}:</b> ${AS.esc(r.text)}</li>`)}
    ${list("First 90 days", s.days_90, t => `<li>${AS.esc(t)}</li>`)}
    ${list("Do not", s.do_not, t => `<li>${AS.esc(t)}</li>`)}
    <p class="small muted">${AS.esc(s.note || "")}</p>
    <p class="d-links">
      <a href="/api/stewardship.md?id=${id}">Markdown</a> ·
      <a href="/api/stewardship.pdf?id=${id}">PDF</a> ·
      <a href="/api/stewardship.docx?id=${id}">Word</a>
    </p>
  </div>`;
}

// How exact the position is (geo.location_confidence), and a form to verify it.
function locationHtml(d) {
  const l = d.location;
  if (!l) return "";
  const id = AS.esc(d.id);
  const methods = [["coordinates", "Coordinates"], ["address", "Address"]]
    .concat(l.country === "ES" ? [["cadastre", "Cadastral reference"]] : []);
  const ll = (lat, lon) => lat != null ? `${Number(lat).toFixed(5)}, ${Number(lon).toFixed(5)}` : "";
  const history = (l.history || []).map(h => `<li>${AS.esc((h.created_at || "").slice(0, 16).replace("T", " "))} ·
      ${h.action === "verified" ? "set from" : "cleared:"} ${AS.esc(h.method || "")} “${AS.esc(h.input || "")}” ${AS.esc(ll(h.lat, h.lon))}</li>`).join("");
  return `<div class="guide location">
    <b>Location</b> <span class="badge l-${AS.esc(l.level)}">${AS.esc(l.label)}</span>
    <span class="small muted">from ${AS.esc(l.source)}</span>
    <p class="small">${AS.esc(l.reliable)}</p>
    ${l.scanner ? `<p class="small muted">The scanner's own position is kept: ${AS.esc(ll(l.scanner.lat, l.scanner.lon))} (${AS.esc(l.scanner.source)}).</p>` : ""}
    <div class="loc-form">
      <select class="loc-method" aria-label="How to give the location">${methods.map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
      <input class="loc-value" type="text" aria-label="Location" placeholder="latitude, longitude · a Google Maps link · street, village, town">
      <button class="btn btn-sm btn-primary" data-id="${id}" onclick="verifyLocation(this)">Verify location</button>
      ${l.verified ? `<button class="btn btn-sm" data-id="${id}" onclick="clearLocation(this)">Use the scanner's position</button>` : ""}
    </div>
    ${history ? `<details class="small" style="margin-top:6px"><summary>History</summary><ul>${history}</ul></details>` : ""}
  </div>`;
}

// After a change made in the drawer: reload its details.
function refreshDetails(el) {
  const panel = el.closest("#property-drawer");
  if (!panel) return;
  detailsCache.invalidate(panel.dataset.for);     // every panel of this listing, not just this one
  return fillPanel(panel, panel.dataset.for, {fresh: true});
}

async function verifyLocation(btn) {
  const box = btn.closest(".location");
  const body = {id: btn.dataset.id, method: box.querySelector(".loc-method").value,
                value: box.querySelector(".loc-value").value.trim()};
  if (!body.value) { AS.toast("Paste coordinates, an address or a reference first", "bad"); return; }
  btn.disabled = true;
  try {
    let r = await AS.api("/api/listing/location", {method: "POST", body});
    if (r.confirm) {
      if (!confirm(r.confirm)) return;
      r = await AS.api("/api/listing/location", {method: "POST", body: {...body, confirm: true}});
    }
    AS.toast(`Location saved: ${r.location.label}`);
    await refreshDetails(btn);
  } catch (e) { /* the toast said why */ } finally { btn.disabled = false; }
}

async function clearLocation(btn) {
  if (!confirm("Go back to the position the scanner found? Yours stays in the history.")) return;
  await AS.api("/api/listing/location/clear", {method: "POST", body: {id: btn.dataset.id}});
  AS.toast("Back to the scanner's position");
  await refreshDetails(btn);
}

// The house and its plot as the map draws them (outline.py): a sketch from
// stored coordinates (numbers only), the plot pale, the house solid, annexes lighter.
function outlineSvg(o) {
  const shapes = [o.plot || [], ...(o.buildings || [])].filter(s => s.length);
  const pts = shapes.flat().concat(o.pin ? [o.pin] : []);
  if (!shapes.length) return "";
  const k = Math.cos(pts[0][0] * Math.PI / 180);
  const xy = p => [p[1] * k, -p[0]];
  const xs = pts.map(p => xy(p)[0]), ys = pts.map(p => xy(p)[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const W = 260, H = 190, pad = 10;
  const s = Math.min((W - 2 * pad) / ((x1 - x0) || 1e-6), (H - 2 * pad) / ((y1 - y0) || 1e-6));
  const dx = (W - (x1 - x0) * s) / 2, dy = (H - (y1 - y0) * s) / 2;     // centred
  const at = p => { const [x, y] = xy(p); return [dx + (x - x0) * s, dy + (y - y0) * s]; };
  const poly = (ring, cls) => `<polygon class="${cls}" points="${ring.map(p => at(p).map(n => n.toFixed(1)).join(",")).join(" ")}"/>`;
  const pin = o.pin ? at(o.pin) : null;
  return `<svg class="outline-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Plot and buildings from the map">
    ${(o.plot || []).length ? poly(o.plot, "ol-plot") : ""}
    ${(o.buildings || []).map((b, i) => poly(b, i ? "ol-annex" : "ol-house")).join("")}
    ${pin ? `<circle class="ol-pin" cx="${pin[0].toFixed(1)}" cy="${pin[1].toFixed(1)}" r="3"/>` : ""}
  </svg>`;
}

function outlineHtml(o) {
  if (!o || !(o.rows || []).length) return "";
  return `<div class="guide outline">
    <b>House and plot on the map</b> <span class="small muted">(estimates from OpenStreetMap and the cadastre, not the deed)</span>
    <div class="outline-body">${outlineSvg(o)}
      <div class="facts">${o.rows.map(r => `<div class="fact"><span class="k">${AS.esc(r.label)}</span><span class="v">${AS.esc(r.value)}</span></div>`).join("")}</div>
    </div>
    ${(o.notes || []).map(n => `<p class="small"><b>${AS.esc(n)}</b></p>`).join("")}
  </div>`;
}

// research_indicators: what the stored evidence shows, and what it cannot show.
// Research notes only: they change no score, bid cap or alert.
const RESEARCH_LABELS = {
  property_specific_comparables: "Comparable sales", document_contradictions: "Documents disagree",
  sale_event_history: "Sale history", acquisition_feasibility: "Can it be bought",
  local_liquidity: "Local market", travel_access: "Travel to services", parcel_geometry: "The plot",
  historical_imagery: "Past aerial images", ecological_connectivity: "Habitat and protected areas",
  restoration_feasibility: "Restoration effort",
};
function researchHtml(r) {
  if (!r || !r.indicators) return "";
  const known = r.indicators.filter(i => i.status !== "unknown");
  const unknown = r.indicators.filter(i => i.status === "unknown");
  const show = v => typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
  const rows = known.map(i => `<div class="research-item">
      <b>${AS.esc(RESEARCH_LABELS[i.category] || i.category)}</b>
      <span class="small muted">(${AS.esc(i.status)}; evidence ${AS.esc(i.evidence_ids.join(", "))})</span>
      <ul class="small">${Object.entries(i.value).map(([k, v]) =>
        `<li>${AS.esc(k.replace(/_/g, " "))}: <code>${AS.esc(show(v))}</code></li>`).join("")}</ul>
      <ul class="small faint">${i.caveats.map(c => `<li>${AS.esc(c)}</li>`).join("")}</ul>
    </div>`).join("");
  return `<div class="guide research">
      <b>Research notes</b> <span class="small muted">from what the scanner has stored; not a score or a bid</span>
      ${rows || `<p class="small">No stored evidence yet.</p>`}
      ${unknown.length ? `<p class="small faint">No evidence yet: ${unknown.map(i =>
        AS.esc(RESEARCH_LABELS[i.category] || i.category)).join(", ")}.</p>` : ""}
    </div>`;
}

function detailsHtml(d) {
  const facts = d.facts.map(f => `<div class="fact"><span class="k">${AS.esc(f.label)}</span><span class="v">${factValue(f)}</span></div>`).join("");
  const links = [d.url && d.source !== "citius" ? AS.link(d.url, "Open the listing ↗") : "",
                 ...d.related.map(r => AS.link(r.url, r.label + " ↗"))].filter(Boolean).join(" · ");
  const guide = d.how_to_find ? `<div class="guide">
      <b>How to find this sale on ${AS.esc(d.how_to_find.site)}</b> <span class="small muted">${AS.esc(d.how_to_find.why)}</span>
      <ol>${d.how_to_find.steps.map(s => `<li>${AS.esc(s.text)}
        ${s.copy ? ` <code>${AS.esc(s.copy)}</code> <button class="btn btn-sm" data-copy="${AS.esc(s.copy)}" onclick="copyText(this.dataset.copy)">Copy</button>` : ""}
        ${s.url ? ` ${AS.link(s.url, "Open Citius ↗")}` : ""}</li>`).join("")}</ol></div>` : "";
  const sv = d.street_view;
  const streetView = sv ? `<div class="guide street">
      <b>Where it is</b> <span class="small muted">(${AS.esc(sv.precision)})</span>
      <code class="small">${Number(sv.lat).toFixed(5)}, ${Number(sv.lon).toFixed(5)}</code>
      ${sv.embed ? `<iframe class="sv-frame" src="${AS.esc(sv.embed)}" loading="lazy" allowfullscreen referrerpolicy="no-referrer-when-downgrade"></iframe>` : ""}
      <p class="d-links">${AS.link(sv.url, "Street View ↗")} · ${AS.link(sv.satellite, "Satellite ↗")}
      ${sv.embed ? "" : `<span class="small faint"> · Add a Google Maps key in Settings to see Street View here.</span>`}</p>
    </div>` : "";
  const off = d.official;
  const official = off ? `<div class="guide official">
      <b>Official records: ${AS.esc(off.registry)}</b>
      ${off.check ? `<p class="small"><b>${AS.esc(off.check)}</b></p>` : ""}
      ${off.facts.length ? `<div class="facts">${off.facts.map(f => `<div class="fact"><span class="k">${AS.esc(f.label)}</span><span class="v">${AS.esc(f.value)}</span></div>`).join("")}</div>` : ""}
      ${off.copy.length ? `<ul class="copy-list">${off.copy.map(c => `<li>${AS.esc(c.label)}: <code>${AS.esc(c.value)}</code> <button class="btn btn-sm" data-copy="${AS.esc(c.value)}" onclick="copyText(this.dataset.copy)">Copy</button></li>`).join("")}</ul>` : ""}
      ${off.steps.length ? `<ol>${off.steps.map(s => `<li>${AS.esc(s.text)}</li>`).join("")}</ol>` : ""}
      ${off.links.map(l => AS.link(l.url, l.label + " ↗")).join(" · ")}
    </div>` : "";
  const imgs = (d.images && d.images.length) ? d.images : (d.image ? [d.image] : []);
  const research = researchHtml(d.research);
  // Each photo is a button that enlarges it in the drawer (listings-drawer.js).
  const gallery = imgs.length ? `<div class="d-gallery">${imgs.map((src, i) => {
    const label = `listing photograph ${i + 1} of ${imgs.length}`;
    return `<button type="button" class="d-photo" data-role="enlarge-photo" aria-label="Enlarge ${label}">
      <img class="d-img" src="${AS.esc(src)}" alt="Listing photograph ${i + 1} of ${imgs.length}" loading="lazy" referrerpolicy="no-referrer"></button>`;
  }).join("")}</div>` : "";
  const pastResults = `${d.past_results ? `<div class="d-lots"><b>What auctions within ${d.past_results.km} km sold for</b>
        <span class="small muted">(last year, final bid: ${d.past_results.count} lots, median ${AS.money(d.past_results.median)},
        ${AS.money(d.past_results.low)}\u2013${AS.money(d.past_results.high)})</span><ul>${
        d.past_results.lots.map(l => `<li>${AS.money(l.price)} · ${AS.esc(l.date)} · ${l.km} km — ${
          l.url ? AS.link(l.url, l.name) : AS.esc(l.name)}${l.type ? ` <span class="small muted">(${AS.esc(l.type)})</span>` : ""}</li>`).join("")}</ul></div>` : ""}`;
  const sameCase = `${(d.same_case || []).length ? `<div class="d-lots"><b>Also sold in this case</b> <span class="small muted">(buying together can make sense: a house and the plot next to it)</span><ul>${
        d.same_case.map(l => `<li>${AS.money(l.price)}${l.area ? ` · ${Math.round(l.area).toLocaleString("de-DE")} m²` : ""} — ${AS.esc(l.title)}</li>`).join("")}</ul></div>` : ""}`;
  // Labelled sections, each from the same helpers as before; an empty one is left out.
  return `<div class="details">
    ${detailsSection("overview", "Overview", `
      ${gallery}
      ${AS.wishesHtml(d.wishes)}
      ${links ? `<p class="d-links">${links}</p>` : ""}
      ${facts ? `<div class="facts">${facts}</div>` : ""}
      ${d.description ? `<p class="d-desc">${AS.esc(d.description)}</p>` : ""}
      <p class="small muted">Why this score: ${AS.esc(d.reasons.join(", "))}</p>`)}
    ${detailsSection("investigation", "Investigation",
      typeof Investigation === "undefined" ? "" : Investigation.sectionHtml(d.investigation))}
    ${detailsSection("costs", "Costs and planning", `${costsHtml(d.costs)}${AS.bidCapHtml(d.bid_cap)}`)}
    ${detailsSection("location", "Location and climate",
      `${locationHtml(d)}${streetView}${outlineHtml(d.outline)}${AS.climateHtml(d.climate)}`)}
    ${detailsSection("records", "Official records and sale procedure", `${guide}${official}`)}
    ${detailsSection("research", "Research and stewardship",
      `${research}${projectHtml(d)}${stewardshipHtml(d)}${pastResults}${sameCase}`)}
    ${detailsSection("notes", "Notes and history", `${contactLogHtml(d)}${statusHistoryHtml(d)}${noteHtml(d)}`)}
  </div>`;
}

// One drawer, so fixed section IDs: never built from a scraped listing ID.
function detailsSection(key, title, body) {
  if (!body.trim()) return "";
  return `<section class="d-section" id="property-section-${key}" aria-labelledby="property-section-${key}-title">
    <h3 class="d-section-title" id="property-section-${key}-title" tabindex="-1">${AS.esc(title)}</h3>${body}</section>`;
}

function contactLogHtml(d) {
  const log = d.contact_log || [];
  if (!log.length) return "";
  const rows = log.map(c => `<tr>
    <td>${AS.esc(c.sent_date || "")}</td>
    <td>${AS.esc(c.method || "")}</td>
    <td>${AS.esc(c.outcome || "")}</td>
    <td class="small">${AS.esc(c.notes || "")}</td>
  </tr>`).join("");
  return `<div class="d-contact">
    <b class="small">Contact history</b>
    <table class="contact-table small"><thead><tr><th>Date</th><th>How</th><th>Outcome</th><th>Notes</th></tr></thead>
    <tbody>${rows}</tbody></table>
  </div>`;
}

// Every shortlist/dismiss change, oldest first; a dismissal stays here after Undo.
function statusHistoryHtml(d) {
  const log = d.status_history || [];
  if (!log.length) return "";
  const label = s => s === "shortlisted" ? "On Offers" : s === "dismissed" ? "Dismissed" : "Open again";
  const rows = log.map(h => `<tr>
    <td>${AS.esc((h.changed_at || "").slice(0, 16).replace("T", " "))}</td>
    <td>${label(h.status)}</td>
    <td class="small">${AS.esc(h.reason || "")}</td>
  </tr>`).join("");
  return `<div class="d-contact">
    <b class="small">Status history</b>
    <table class="contact-table small"><thead><tr><th>When (UTC)</th><th>Status</th><th>Reason</th></tr></thead>
    <tbody>${rows}</tbody></table>
  </div>`;
}
