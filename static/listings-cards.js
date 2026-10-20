// listings-cards.js — the Listings page: the results as a table or as cards, one population, one set of actions.
// Plain scripts sharing one global scope, loaded in order by templates/listings.html
// (listings-saved-searches.js, listings-view.js, listings-summary.js, listings-details.js,
//  listings-cards.js, listings-notes.js, listings-drawer.js, listings.js).
//
// Cards are another way to draw the page of results the server sent: the same
// items, count, sort and page as the table, with the same summaries
// (listings-summary.js). Only the active layout is drawn; switching redraws the
// last results without asking the server again. Details open the one property
// drawer; status changes go through setStatus, as from the table.

const LAYOUT_KEY = "auction-scanner:listings-layout:v1";
let layout = "table";
try { layout = localStorage.getItem(LAYOUT_KEY) === "cards" ? "cards" : "table"; } catch (e) { /* private window */ }
let lastResults = null;          // {items, hiddenView, neverScanned}: what the active layout draws

function listingCardHtml(item) {
  const id = AS.esc(item.id);
  const title = AS.esc(item.title || "Untitled listing");
  const p = item.presentation || {};
  const starred = item.status === "shortlisted";
  const dismissed = item.status === "dismissed";
  const place = [item.concelho, item.district].filter(Boolean).join(", ");
  // Citius has no page per sale; every other sale keeps its link out.
  const sale = item.url && item.source !== "citius" ? AS.link(item.url, "Sale page ↗") : "";
  return `<article class="listing-card${item.hidden_reason ? " dim" : ""}" data-id="${id}">
    <div class="listing-card-property">${thumbnailHtml(p.thumbnail, item.title)}
      <div><h3><button type="button" class="listing-card-title" data-role="details" data-id="${id}"
          aria-label="Details: ${title}" aria-expanded="false">${title}</button></h3>
        <p class="small muted">${AS.flag(item.country)} ${AS.esc(place)}</p>
        <p class="small"><span class="src">${AS.esc(item.source || "")}</span>${listingBadgesHtml(item)}</p>
        ${view.mode === "home" ? AS.wishesHtml(item.wishes, true) : ""}
        ${sale ? `<p class="small">${sale}</p>` : ""}</div></div>
    <div class="listing-card-figures">
      <section aria-label="Price basis"><div class="small muted">Price basis</div>${priceSummaryHtml(p.price)}</section>
      <section aria-label="Estimated all-in"><div class="small muted">Estimated all-in</div>${allInSummaryHtml(p.all_in)}</section>
    </div>
    <section aria-label="Opportunity"><div class="small muted">Opportunity</div>
      ${AS.scoreBadge(item.score, item.rank, item.excellent)}${listingSignalsHtml(item)}${reasonsSummaryHtml(item.reasons)}</section>
    <section aria-label="Unresolved checks"><div class="small muted">Unresolved checks</div>${checksSummaryHtml(p.checks)}</section>
    <section aria-label="Deadline"><div class="small muted">Deadline</div>${deadlineSummaryHtml(p.deadline)}</section>
    <p class="small muted">An opportunity score is not independent verification or acquisition readiness.</p>
    <div class="listing-card-actions">
      <button type="button" class="btn btn-sm" data-role="${dismissed ? "restore" : "star"}" data-status-action data-id="${id}"
        ${dismissed ? "" : `aria-pressed="${starred}"`} aria-label="${dismissed ? "Restore" : starred ? "Remove from shortlist" : "Shortlist"}: ${title}">${dismissed ? "Restore" : starred ? "On Offers" : "Shortlist"}</button>
      ${dismissed ? "" : `<button type="button" class="btn btn-sm" data-role="dismiss" data-status-action data-id="${id}" aria-label="Dismiss: ${title}">Dismiss</button>`}
    </div>
  </article>`;
}

function renderListingCards(container, items, hiddenView, neverScanned) {
  if (!items.length) {
    const empty = emptyResultsHtml(hiddenView, neverScanned);
    container.innerHTML = empty.html;
    wireEmptyScan(empty.scan);
    return;
  }
  container.innerHTML = items.map(listingCardHtml).join("");
}

// Draws the results in the active layout; the other one is hidden and emptied,
// so there is one set of buttons per listing.
function renderResults(items, hiddenView, neverScanned = false) {
  lastResults = {items, hiddenView, neverScanned};
  items.forEach(it => shownItems.set(it.id, it));
  drawLayout();
}

function drawLayout() {
  const cards = layout === "cards";
  $("listings-table").hidden = cards;
  $("listing-cards").hidden = !cards;
  document.querySelectorAll("[data-layout]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.layout === layout)));
  if (!lastResults) return;
  const {items, hiddenView, neverScanned} = lastResults;
  if (cards) {
    $("rows").innerHTML = "";
    renderListingCards($("listing-cards"), items, hiddenView, neverScanned);
  } else {
    $("listing-cards").innerHTML = "";
    renderRows(items, hiddenView, neverScanned);
  }
  // A status change still on its way keeps its redrawn buttons waiting.
  statusPending.forEach(id => statusButtons(id).forEach(b => { b.disabled = true; }));
}

// Where results that could not be loaded the first time say so: the active layout.
function showResultsMessage(html) {
  if (layout === "cards") $("listing-cards").innerHTML = html;
  else $("rows").innerHTML = `<tr><td colspan="${TABLE_COLUMNS}">${html}</td></tr>`;
}

function setLayout(next) {
  if (next !== "table" && next !== "cards") return;
  try { localStorage.setItem(LAYOUT_KEY, next); } catch (e) { /* not kept */ }
  if (next === layout) return;
  const context = captureContext();
  layout = next;
  drawLayout();
  restoreContext(context);
  propertyDrawer.reconcile();
  AS.announce(next === "cards" ? "Showing results as cards" : "Showing results as a table");
}
document.querySelectorAll("[data-layout]").forEach(button => button.addEventListener("click", () => setLayout(button.dataset.layout)));

// One listener for every card's buttons; IDs come from data-id, never from markup or script source.
$("listing-cards").addEventListener("click", e => {
  const button = e.target.closest && e.target.closest("button[data-role]");
  if (!button || !button.dataset.id || button.disabled) return;
  const id = button.dataset.id;
  const role = button.dataset.role;
  if (role === "details") propertyDrawer.open(id, button);
  else if (role === "star") setStatus(id, (shownItems.get(id) || {}).status === "shortlisted" ? null : "shortlisted");
  else if (role === "restore") setStatus(id, null);
  else if (role === "dismiss") setStatus(id, "dismissed");
});
drawLayout();                                     // the remembered layout, before the first results
