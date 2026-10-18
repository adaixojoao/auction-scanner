// listings-details.js — the Listings page: rendering rows and research cards, details panels and their freshness, and keeping your place across a refresh.
// Plain scripts sharing one global scope, loaded in order by templates/listings.html
// (listings-view.js, listings-summary.js, listings-details.js, listings-notes.js, listings.js).

// ── Keeping your place across a refresh (by listing ID, never by DOM ID) ──
// One registry for both surfaces: the results table and the research cards.
function rowFor(id) { return [...document.querySelectorAll("#rows tr[data-id]")].find(tr => tr.dataset.id === id); }
function cardFor(id) {
  return [...document.querySelectorAll("#research-candidates article.research-card")].find(a => a.dataset.id === id);
}
function anchorFor(surface, id) { return surface === "research" ? cardFor(id) : rowFor(id); }
function surfaceItems(surface) {
  return surface === "research" ? [...document.querySelectorAll("#research-candidates article.research-card")]
                                : [...document.querySelectorAll("#rows tr[data-id]")];
}
// The details panel of a row, only when it is that listing's own panel.
function rowPanel(row) {
  const next = row && row.nextElementSibling;
  return next && next.classList.contains("details-row") && next.dataset.for === row.dataset.id ? next : null;
}

function captureContext() {
  const open = [
    ...[...document.querySelectorAll("#rows tr.details-row")].map(tr => ({surface: "table", id: tr.dataset.for})),
    ...[...document.querySelectorAll("#research-candidates .candidate-details")]
      .map(panel => ({surface: "research", id: panel.dataset.for})),
  ];
  const titles = new Map(open.map(o => [o.id, (shownItems.get(o.id) || {}).title || "A listing"]));
  let focus = null;
  const active = document.activeElement;
  const row = active && active.closest ? active.closest("#rows tr") : null;
  const card = active && active.closest ? active.closest("#research-candidates article.research-card") : null;
  if (row || card) {
    const surface = row ? "table" : "research";
    const id = row ? (row.dataset.for || row.dataset.id) : card.dataset.id;
    focus = {surface, id, role: active.dataset.role || "",
             index: surfaceItems(surface).findIndex(el => el.dataset.id === id),
             selection: typeof active.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null};
  }
  let scroll = null;
  const top = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--topbar-height")) || 0;
  for (const el of document.querySelectorAll("#research-candidates article.research-card, #rows tr[data-id]")) {
    const box = el.getBoundingClientRect();
    if (box.bottom > top) { scroll = {surface: el.matches("article") ? "research" : "table", id: el.dataset.id, top: box.top}; break; }
  }
  return {open, titles, focus, scroll};
}

// Reopens details still in view, puts focus and scroll back; says what left.
function restoreContext(ctx) {
  const gone = [];
  ctx.open.forEach(({surface, id}) => {
    if (surface === "research") {
      const card = cardFor(id);
      if (card && !card.querySelector(".candidate-details")) openCandidate(id, card.querySelector('[data-role="details"]'));
      else if (!card) gone.push(id);
      return;
    }
    const row = rowFor(id);
    if (row) openRowDetails(row, id);
    else gone.push(id);
  });
  if (ctx.scroll) {
    const anchor = anchorFor(ctx.scroll.surface, ctx.scroll.id);
    if (anchor) window.scrollBy(0, anchor.getBoundingClientRect().top - ctx.scroll.top);
  }
  // Focus only comes back when it was in the results and nobody moved it since.
  const active = document.activeElement;
  const inResults = el => $("rows").contains(el) || $("research-candidates").contains(el);
  if (ctx.focus && (!active || active === document.body || inResults(active))) {
    const {surface, id, role, index} = ctx.focus;
    const anchor = anchorFor(surface, id);
    const scope = !anchor ? [] : surface === "research" ? [anchor] : [anchor, rowPanel(anchor)].filter(Boolean);
    let target = null;
    for (const el of scope) target = target || [...el.querySelectorAll("[data-role]")].find(x => x.dataset.role === role);
    if (!target) {
      // The listing left: its neighbour on the same surface, else the count line.
      const items = surfaceItems(surface);
      const near = anchor || items[Math.min(Math.max(index, 0), items.length - 1)];
      target = near ? near.querySelector('[data-role="details"]') : $("result-count");
    }
    if (target) {
      target.focus({preventScroll: true});
      if (ctx.focus.selection && typeof target.setSelectionRange === "function") target.setSelectionRange(...ctx.focus.selection);
    }
  }
  if (!gone.length) return "";
  const unique = [...new Set(gone)];
  const kept = unique.filter(id => notes.isPending(id)).length;
  return `${unique.length === 1 ? `“${ctx.titles.get(unique[0])}” is` : `${unique.length} listings you had open are`} no longer in this view.`
    + (kept ? " Unsaved notes are kept and still saved." : "");
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
    <div class="small muted">Checks</div>${checksSummaryHtml(p.checks)}
    <div class="small muted">Deadline</div>${deadlineSummaryHtml(p.deadline)}
    <details class="small result-more"><summary>Evidence gaps (${(it.gaps || []).length})</summary>
      <ul class="small">${(it.gaps || []).map(g => `<li>${AS.esc(g)}</li>`).join("")}</ul></details>
    <p>
      <button class="btn btn-sm" aria-label="Details: ${AS.esc(it.title || "this listing")}" aria-expanded="false" data-id="${id}" data-role="details" onclick="openCandidate(this.dataset.id, this)">Details</button>
      <button class="btn btn-sm" aria-label="${starred ? "Remove from shortlist" : "Shortlist"}: ${AS.esc(it.title || "this listing")}" aria-pressed="${starred}" data-id="${id}" data-role="star" data-status-action onclick="setStatus(this.dataset.id, ${starred ? "null" : "'shortlisted'"})">${starred ? "On Offers" : "Shortlist"}</button>
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

// Detail panels get DOM IDs from a counter, never from scraped listing IDs.
let detailsSeq = 0;
function nextDetailsId() { detailsSeq += 1; return `details-panel-${detailsSeq}`; }

// Details last loaded, per goal and listing: {data, token, at}. They are shown
// as current only while the listing's detail_token from the latest results is
// unchanged (a scan, status, score or evidence revision changes it) and they
// are younger than DETAIL_TTL_MS; otherwise they show as earlier details while
// fresh ones load. Note drafts live in the note queue, not in these objects.
const DETAIL_TTL_MS = 10 * 60 * 1000;
const detailCache = new Map();
const panelReads = new WeakMap();   // one latest-wins read per open panel
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
  if (typing) {
    const field = body.querySelector("textarea.d-note");
    if (field) { field.focus({preventScroll: true}); field.setSelectionRange(typing.start, typing.end); field.scrollTop = typing.scroll; }
  }
}

// Fills a details panel. A closed or replaced panel, or another goal, ignores
// a late answer; a failure says so with a Retry for this listing.
async function fillPanel(panel, id, {fresh = false} = {}) {
  const mode = view.mode, key = `${mode}\n${id}`, token = detailToken(id);
  const body = panel.querySelector(".details-body");
  const entry = detailCache.get(key);
  const current = entry && entry.token === token && Date.now() - entry.at < DETAIL_TTL_MS;
  if (entry && current && !fresh) { renderDetailsBody(body, id, entry.data, "current"); return; }
  if (entry) renderDetailsBody(body, id, entry.data, "refreshing");
  else body.innerHTML = `<p class="small muted details-loading">Loading details…</p>`;
  let read = panelReads.get(panel);
  if (!read) { read = AS.latestRead(); panelReads.set(panel, read); }
  const asked = Date.now();
  let result = await read.run("/api/listing?id=" + encodeURIComponent(id) + "&mode=" + encodeURIComponent(mode));
  if (result.kind === "obsolete" || !panel.isConnected || mode !== view.mode) return;
  if (result.kind === "success" && !(result.data && result.data.id === id && Array.isArray(result.data.facts))) {
    result = {kind: "error", message: "The server returned an unreadable response."};
  }
  if (result.kind === "error") {
    if (entry) { renderDetailsBody(body, id, entry.data, "failed", result.message); return; }
    const title = (shownItems.get(id) || {}).title || "this listing";
    body.innerHTML = `<div class="notice warn details-error"><p>Could not load details: ${AS.esc(result.message)}</p>
      <button type="button" class="btn btn-sm" data-role="retry-details" aria-label="Retry details: ${AS.esc(title)}">Retry details</button></div>`;
    body.querySelector("button").addEventListener("click", () => fillPanel(panel, id, {fresh: true}));
    return;
  }
  const newest = detailCache.get(key);
  if (!newest || newest.asked <= asked) detailCache.set(key, {data: result.data, token, at: Date.now(), asked});
  renderDetailsBody(body, id, result.data, "current");
}
document.addEventListener("click", e => {
  const button = e.target.closest && e.target.closest('[data-role="refresh-details"]');
  if (!button) return;
  const panel = button.closest(".details-row, .candidate-details");
  if (panel) fillPanel(panel, panel.dataset.for, {fresh: true});
});

function closePanel(panel) {
  const read = panelReads.get(panel);
  if (read) read.cancel();
  panel.remove();
}

function openCandidate(id, button) {
  const card = [...document.querySelectorAll("#research-candidates article")].find(el => el.dataset.id === id);
  if (!card) return;
  const existing = card.querySelector(".candidate-details");
  if (existing) {
    closePanel(existing);
    if (button) { button.setAttribute("aria-expanded", "false"); button.removeAttribute("aria-controls"); }
    return;
  }
  const panel = document.createElement("div");
  panel.className = "candidate-details";
  panel.id = nextDetailsId();
  panel.dataset.for = id;
  panel.innerHTML = `<div class="details-body"></div>`;
  card.appendChild(panel);
  if (button) { button.setAttribute("aria-expanded", "true"); button.setAttribute("aria-controls", panel.id); }
  fillPanel(panel, id);
}

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

function renderRows(items, hiddenView, neverScanned = false) {
  items.forEach(it => shownItems.set(it.id, it));
  if (!items.length) {
    const scan = hiddenView ? null : scanSuggestion();
    $("rows").innerHTML = `<tr><td colspan="${TABLE_COLUMNS}"><div class="empty">
      <h3>${neverScanned ? "No scan data yet" : "No listings here"}</h3>
      <p>${hiddenView ? "Nothing is hidden with these filters."
        : neverScanned ? "No scan has run yet, so there is nothing to show."
        : "No eligible listings match these filters. Change the filters, or scan for new listings."}</p>
      ${scan ? `<p class="small muted" id="scan-scope">${AS.esc(scan.scope)}</p>
        <p style="margin-top:10px"><button class="btn btn-primary" type="button" id="empty-scan" aria-describedby="scan-scope">${AS.esc(scan.label)}</button></p>` : ""}
    </div></td></tr>`;
    if (scan) $("empty-scan").addEventListener("click", () => AS.startScan(scan.body).catch(() => {}));
    return;
  }
  $("rows").innerHTML = items.map(it => {
    const loc = [it.concelho, it.district].filter(Boolean).join(", ");
    const p = it.presentation || {};
    const badges = (it.kind ? `<span class="badge b-muted">${AS.esc(KIND_LABEL[it.kind])}</span>` : "")
      + (it.is_recent ? '<span class="badge b-new">NEW</span>' : "")
      + (it.offer_outcome ? `<span class="badge b-offer">offer ${AS.esc(it.offer_outcome)}</span>` : "")
      + (it.hidden_reason ? `<span class="badge b-hidden" title="${AS.esc(it.hidden_reason)}">${AS.esc(it.hidden_category)}</span>` : "");
    const signals = AS.climateBadge(it.climate_grade)
      + (it.price_drop_pct ? `<span class="badge b-drop">↓${Math.round(it.price_drop_pct)}%</span>` : "");
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
      <td class="t-checks">${checksSummaryHtml(p.checks)}</td>
      <td class="t-deadline">${deadlineSummaryHtml(p.deadline)}</td>
      <td class="nowrap"><button class="icon-btn" aria-label="Details: ${propertyLabel}" aria-expanded="false" title="Details" data-id="${id}" data-role="details" onclick="toggleDetails(this)">ⓘ</button>${actions}</td>
    </tr>`;
  }).join("");
}

// ── Details panel: everything known about one listing (listing_info.py) ──
function setDetailsTriggers(row, panelId) {
  // Both the ⓘ button and a Citius title open the same panel.
  row.querySelectorAll("[aria-expanded][data-id]").forEach(t => {
    t.setAttribute("aria-expanded", String(!!panelId));
    if (panelId) t.setAttribute("aria-controls", panelId); else t.removeAttribute("aria-controls");
  });
}

function toggleDetails(el) {
  const row = el.closest("tr"), next = row.nextElementSibling;
  if (next && next.classList.contains("details-row")) { closePanel(next); setDetailsTriggers(row, null); return; }
  openRowDetails(row, row.dataset.id);
}

// Opens at once with "Loading details…"; one panel per row, never two.
function openRowDetails(row, id) {
  const next = row.nextElementSibling;
  if (next && next.classList.contains("details-row")) return;
  const tr = document.createElement("tr");
  tr.className = "details-row";
  tr.id = nextDetailsId();
  tr.dataset.for = id;
  tr.innerHTML = `<td colspan="${TABLE_COLUMNS}"><div class="details-body"></div></td>`;
  row.after(tr);
  setDetailsTriggers(row, tr.id);
  fillPanel(tr, id);
}

function factValue(f) {
  const v = AS.esc(f.value);
  if (/e-mail/i.test(f.label) && /^[^\s@]+@[^\s@]+$/.test(f.value)) return `<a href="mailto:${v}">${v}</a>`;
  if (/phone/i.test(f.label)) return `<a href="tel:${AS.esc(f.value.replace(/[^0-9+]/g, ""))}">${v}</a>`;
  return v;
}

function costsHtml(c) {
  if (!c) return "";
  const eur = v => v ? AS.money(v) : "\u20ac0";
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
    <b>What it really costs</b> <span class="small muted">on ${AS.esc(c.basis)}, ${eur(c.base)}</span>
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

// After a change made in the panel: reload that panel (row or research card).
function refreshDetails(el) {
  const panel = el.closest(".details-row, .candidate-details");
  if (panel) return fillPanel(panel, panel.dataset.for, {fresh: true});
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
  const gallery = imgs.length ? `<div class="d-gallery">${imgs.map(src =>
    `<img class="d-img" src="${AS.esc(src)}" alt="" loading="lazy" referrerpolicy="no-referrer">`).join("")}</div>` : "";
  return `<div class="details">
    ${gallery}
    <div class="d-main">
      ${AS.wishesHtml(d.wishes)}
      ${guide}
      ${locationHtml(d)}
      ${streetView}
      ${outlineHtml(d.outline)}
      ${official}
      ${research}
      ${links ? `<p class="d-links">${links}</p>` : ""}
      ${costsHtml(d.costs)}
      ${AS.bidCapHtml(d.bid_cap)}
      ${AS.climateHtml(d.climate)}
      ${projectHtml(d)}
      ${stewardshipHtml(d)}
      ${facts ? `<div class="facts">${facts}</div>` : ""}
      ${d.past_results ? `<div class="d-lots"><b>What auctions within ${d.past_results.km} km sold for</b>
        <span class="small muted">(last year, final bid: ${d.past_results.count} lots, median ${AS.money(d.past_results.median)},
        ${AS.money(d.past_results.low)}\u2013${AS.money(d.past_results.high)})</span><ul>${
        d.past_results.lots.map(l => `<li>${AS.money(l.price)} · ${AS.esc(l.date)} · ${l.km} km — ${
          l.url ? AS.link(l.url, l.name) : AS.esc(l.name)}${l.type ? ` <span class="small muted">(${AS.esc(l.type)})</span>` : ""}</li>`).join("")}</ul></div>` : ""}
      ${(d.same_case || []).length ? `<div class="d-lots"><b>Also sold in this case</b> <span class="small muted">(buying together can make sense: a house and the plot next to it)</span><ul>${
        d.same_case.map(l => `<li>${AS.money(l.price)}${l.area ? ` · ${Math.round(l.area).toLocaleString("de-DE")} m²` : ""} — ${AS.esc(l.title)}</li>`).join("")}</ul></div>` : ""}
      ${d.description ? `<p class="d-desc">${AS.esc(d.description)}</p>` : ""}
      <p class="small muted">Why this score: ${AS.esc(d.reasons.join(", "))}</p>
      ${contactLogHtml(d)}
      ${statusHistoryHtml(d)}
      ${noteHtml(d)}
    </div></div>`;
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
