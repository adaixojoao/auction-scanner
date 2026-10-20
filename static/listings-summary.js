// listings-summary.js — the Listings page: what one row or research card says about price, all-in cost, checks and deadline.
// Plain scripts sharing one global scope, loaded in order by templates/listings.html
// (listings-saved-searches.js, listings-view.js, listings-summary.js, listings-details.js,
//  listings-cards.js, listings-notes.js, listings-drawer.js, listings.js).
// Display only: every figure and state comes from the server's presentation summary
// (presentation.py). Nothing here prices, verifies or ranks a listing.

// The results table's columns: every full-width row (empty, error, details) spans them all.
const TABLE_COLUMNS = 7;

function amountOrNull(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function displayMoney(value) {
  const number = amountOrNull(value);
  if (number === null) return "Not established";
  return number === 0 ? "€0" : AS.money(number);
}

function priceSummaryHtml(price) {
  if (!price) return '<span class="muted">Price basis not established</span>';
  const amount = amountOrNull(price.amount);
  const secondary = Array.isArray(price.secondary) ? price.secondary : [];
  return `<div class="money"><strong>${AS.esc(amount === null ? "Not published" : displayMoney(amount))}</strong></div>
    <div class="small">${AS.esc(price.label || "Basis not established")}</div>
    ${price.note ? `<div class="small muted">${AS.esc(price.note)}</div>` : ""}
    ${secondary.map(s => `<div class="small faint">${AS.esc(s.label)}: <span class="money">${AS.esc(displayMoney(s.amount))}</span>${s.note ? ` <span class="muted">(${AS.esc(s.note)})</span>` : ""}</div>`).join("")}`;
}

function allInSummaryHtml(estimate) {
  if (!estimate) return '<span class="muted">All-in estimate not established</span>';
  if (estimate.headline) return `<span class="muted">${AS.esc(estimate.headline)}</span>`;
  const low = amountOrNull(estimate.low), high = amountOrNull(estimate.high);
  const subtotal = amountOrNull(estimate.known_subtotal);
  const ranged = estimate.repairs_priced === true && low !== null && high !== null && high >= low;
  const missing = Array.isArray(estimate.missing) ? estimate.missing : [];
  const excludes = Array.isArray(estimate.excludes) ? estimate.excludes : [];
  const headline = ranged ? `${displayMoney(low)}–${displayMoney(high)}`
    : subtotal !== null ? `${displayMoney(subtotal)} known subtotal` : "Range not established";
  return `<div class="money"><strong>${AS.esc(headline)}</strong></div>
    <div class="small muted">Estimated · ${AS.esc(estimate.basis_label || "Basis not established")}</div>
    ${ranged ? "" : '<div class="small warn">Incomplete cost estimate: price, taxes and fees only</div>'}
    ${missing.length ? `<ul class="result-gaps">${missing.map(m => `<li>${AS.esc(m)}</li>`).join("")}</ul>` : ""}
    ${excludes.length ? `<div class="small faint">Not included: ${AS.esc(excludes.join("; "))}</div>` : ""}`;
}

function reasonsSummaryHtml(reasons) {
  const list = Array.isArray(reasons) ? reasons : [];
  const rows = values => values.map(value => `<li>${AS.esc(value)}</li>`).join("");
  const remaining = list.slice(2);
  return `${list.length ? `<ul class="result-reasons">${rows(list.slice(0, 2))}</ul>` : '<span class="small muted">No reasons available</span>'}
    ${remaining.length ? `<details class="small result-more"><summary>${remaining.length} more reason${remaining.length === 1 ? "" : "s"}</summary><ul class="result-reasons">${rows(remaining)}</ul></details>` : ""}`;
}

const CHECK_MARK = {verified: "✓", not_applicable: "–", concern: "!", needs_recheck: "↻", source_reported: "i", unverified: "?"};
function checksSummaryHtml(checks) {
  const items = checks && Array.isArray(checks.items) ? checks.items : [];
  if (!items.length) return '<span class="small muted">No checks for this listing</span>';
  // Concerns first: they are never cut.
  const ordered = [...items.filter(x => x.state === "concern"), ...items.filter(x => x.state !== "concern")];
  return `<ul class="result-checks">${ordered.map(x => `<li class="check-${AS.esc(x.state)}"><span class="check-mark" aria-hidden="true">${CHECK_MARK[x.state] || "?"}</span> <b>${AS.esc(x.label)}:</b> ${AS.esc(x.text)}</li>`).join("")}</ul>`;
}

// Only a timezone-resolved instant gets a countdown; it is worked out when the
// rows are drawn and never announced as it changes.
function relativeInstantLabel(iso, now = Date.now()) {
  const timestamp = Date.parse(iso);
  if (!Number.isFinite(timestamp)) return "Deadline unavailable";
  const minutes = Math.ceil((timestamp - now) / 60000);
  if (minutes <= 0) return "Deadline passed";
  if (minutes < 60) return `${minutes} min left`;
  const hours = Math.ceil(minutes / 60);
  if (hours < 24) return `${hours} h left`;
  const days = Math.ceil(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} left`;
}

function relativeDayLabel(days) {
  if (!Number.isInteger(days)) return "";
  if (days < 0) return "Date passed";
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  return `In ${days} days`;
}

function calendarDate(isoDate) {
  // A calendar date, not an instant: read and shown in UTC so no zone moves it a day.
  const date = new Date(`${isoDate}T00:00:00Z`);
  return Number.isFinite(date.getTime())
    ? date.toLocaleDateString("en-GB", {timeZone: "UTC", day: "numeric", month: "short", year: "numeric"})
    : isoDate;
}

function deadlineSummaryHtml(d) {
  if (!d || d.precision === "unknown") return '<span class="small muted">Not published</span>';
  if (d.precision === "invalid" || !d.date) return '<span class="small muted">Deadline unavailable</span>';
  const zone = d.timezone ? AS.esc(d.timezone) : "time zone not known";
  if (d.precision === "date") {
    return `<div><strong>${AS.esc(relativeDayLabel(d.days))}</strong> <span class="small muted">— closing time not published</span></div>
      <div class="small"><time datetime="${AS.esc(d.date)}">${AS.esc(calendarDate(d.date))}</time></div>
      <div class="small faint">Date only (${zone})</div>`;
  }
  const relative = d.instant ? relativeInstantLabel(d.instant) : relativeDayLabel(d.days);
  return `<div><strong>${AS.esc(relative)}</strong></div>
    <div class="small"><time datetime="${AS.esc(d.instant || d.date)}">${AS.esc(calendarDate(d.date))}, ${AS.esc(d.time || "")}</time></div>
    <div class="small faint">${d.precision === "local" ? `Sale-local time (${zone}); the notice gives no zone` : `Sale-local time (${zone})`}</div>`;
}

// The badges and signals a row shows, shared with the cards (listings-cards.js).
function listingBadgesHtml(it) {
  return (it.kind ? `<span class="badge b-muted">${AS.esc(KIND_LABEL[it.kind])}</span>` : "")
    + (it.is_recent ? '<span class="badge b-new">NEW</span>' : "")
    + (it.offer_outcome ? `<span class="badge b-offer">offer ${AS.esc(it.offer_outcome)}</span>` : "")
    + (it.hidden_reason ? `<span class="badge b-hidden" title="${AS.esc(it.hidden_reason)}">${AS.esc(it.hidden_category)}</span>` : "");
}
function listingSignalsHtml(it) {
  return AS.climateBadge(it.climate_grade)
    + (it.price_drop_pct ? `<span class="badge b-drop">↓${Math.round(it.price_drop_pct)}%</span>` : "");
}

function thumbnailHtml(src, title) {
  if (!src) return '<div class="result-thumbnail-fallback">No image</div>';
  return `<img class="result-thumbnail" src="${AS.esc(src)}" alt="${AS.esc(`Listing photograph: ${title || "listing"}`)}" width="72" height="54" loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
}

// A broken picture becomes the neutral fallback; no URL or ID goes into inline script.
document.addEventListener("error", e => {
  const img = e.target;
  if (!(img instanceof HTMLImageElement) || !img.classList.contains("result-thumbnail")) return;
  const box = document.createElement("div");
  box.className = "result-thumbnail-fallback";
  box.textContent = "No image";
  img.replaceWith(box);
}, true);
