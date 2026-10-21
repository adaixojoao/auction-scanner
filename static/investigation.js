// Investigation views (investigation.py): what is still unknown about a property and what to find out next.
// Display only: nothing here verifies, scores, hides or bids. Shared by the drawer and the Investigations page.
const Investigation = (() => {
  const STATE = {verified: "Verified by you", unresolved: "Unresolved", concern: "Concern",
                 not_applicable: "Not applicable", established: "Established"};
  const BADGE = {verified: "b-drop", concern: "b-hidden", not_applicable: "b-muted", established: "b-muted"};

  function briefUrl(a) {
    return "/api/investigation/brief.md?" + new URLSearchParams({id: a.listing_id, mode: a.mode}).toString();
  }

  function badge(d) {
    const word = d.source_reported ? "Source-reported, unverified" : (STATE[d.state] || d.state);
    return `<span class="badge ${BADGE[d.state] || "b-short"}">${AS.esc(word)}</span>`;
  }

  // The checklist items behind a dimension, recorded in place (POST /api/checklist, the
  // one record of what you checked). Saving a status records what you found; nothing
  // else, and no other page's draft, changes.
  function recorderHtml(d, record) {
    if (!record) return "";
    const items = d.evidence_refs.filter(r => r.type === "checklist").map(r => record.items[r.key]).filter(Boolean);
    if (!items.length) return "";
    const item = x => `<div class="inv-record" data-key="${AS.esc(x.key)}">
        <label class="small"><span>${AS.esc(x.label)}</span>
          <select class="inv-status">${record.statuses.map(([k, v]) =>
            `<option value="${AS.esc(k)}"${k === x.status ? " selected" : ""}>${AS.esc(v)}</option>`).join("")}</select></label>
        ${x.needs_recheck ? `<span class="small warn">The listing changed since this was recorded: check it again.</span>` : ""}
        <input class="inv-notes" aria-label="What you found: ${AS.esc(x.label)}" placeholder="what you found" value="${AS.esc(x.notes)}">
        <input class="inv-ref" aria-label="Where the document is: ${AS.esc(x.label)}" placeholder="a link, or where the document is" value="${AS.esc(x.reference)}">
        <input class="inv-date" type="date" aria-label="Checked on: ${AS.esc(x.label)}" value="${AS.esc(x.checked_on)}">
        <input class="inv-by" type="hidden" value="${AS.esc(x.verified_by)}">
        <button type="button" class="btn btn-sm" data-role="inv-save">Save</button></div>`;
    return `<details class="small inv-recorder"><summary>Record what you found (${items.length})</summary>
      ${items.map(item).join("")}
      <p class="faint">"Verified" means you checked it yourself: your record, not legal advice.</p></details>`;
  }

  function dimensionRow(d, record) {
    return `<li class="inv-dim" data-dimension="${AS.esc(d.key)}" data-state="${AS.esc(d.state)}">
      <b>${AS.esc(d.label)}</b> ${badge(d)}
      <span class="small">${AS.esc(d.summary)}</span>
      ${d.limitations.length ? `<ul class="small faint">${d.limitations.map(l => `<li>${AS.esc(l)}</li>`).join("")}</ul>` : ""}
      ${recorderHtml(d, record)}
    </li>`;
  }

  // Information requests are drafted on Offers, which lists shortlisted properties.
  function lettersHtml(n, status) {
    if (!n.information_requests.length) return "";
    const where = status === "shortlisted" ? `<a href="/offers">Draft it on Offers</a>`
      : "Shortlist the property to draft it on Offers";
    return `<p class="small">Information request: ${n.information_requests.map(AS.esc).join("; ")}. ${where}
      (nothing is sent from here).</p>`;
  }

  function actionHtml(n, a = {}) {
    if (!n) return `<p class="small">No open question is left in the records the scanner holds. That does not
      mean the property is ready to buy.</p>`;
    return `<div class="inv-action" data-action="${AS.esc(n.key)}">
      <p><b>${AS.esc(n.title)}</b> <span class="small muted">${AS.esc(n.question)}</span></p>
      <p class="small">${AS.esc(n.reason)}</p>
      <p class="small">Evidence to ask for: ${n.requested_evidence.map(AS.esc).join("; ")}</p>
      ${lettersHtml(n, a.status)}
      ${n.links.length ? `<p class="small d-links">${n.links.map(l => AS.link(l.url, l.label + " ↗")).join(" · ")}</p>` : ""}
      ${a.record && n.dimension !== "contradiction" ? `<p class="small">Record the answer under ${
        AS.esc((a.dimensions.find(d => d.key === n.dimension) || {}).label || "the evidence above")}.</p>` : ""}
      <p class="small faint">${AS.esc(n.completion_note)}</p>
    </div>`;
  }

  // The drawer section: Opportunity → Unresolved evidence → Next action → Research progress.
  function sectionHtml(a) {
    if (!a) return "";
    const score = a.opportunity.score == null ? "—" : Math.round(a.opportunity.score);
    const open = a.dimensions.filter(d => d.state === "unresolved" || d.state === "concern");
    const settled = a.dimensions.filter(d => !(d.state === "unresolved" || d.state === "concern"));
    return `<div class="guide investigation" data-role="investigation" data-id="${AS.esc(a.listing_id)}">
      <p class="small muted">Assessed for ${AS.esc(a.purpose || "no goal")}. Nothing here establishes that the
        property is ready to buy.${a.eligibility ? ` Not in the queue: ${AS.esc(a.eligibility)}.` : ""}</p>
      <h4 class="inv-head">Opportunity</h4>
      <p class="small">Score ${AS.esc(String(score))}${a.opportunity.reasons.length
        ? `: ${AS.esc(a.opportunity.reasons.join(", "))}` : ""}. A high score is not evidence.</p>
      <h4 class="inv-head">Unresolved evidence (${a.unresolved} unresolved, ${a.concerns} concerns)</h4>
      ${a.contradictions.length ? `<ul class="inv-contradictions small">${a.contradictions.map(c =>
        `<li><b>Conflict:</b> ${AS.esc(c)}</li>`).join("")}</ul>` : ""}
      <ul class="inv-dims">${open.map(d => dimensionRow(d, a.record)).join("")}</ul>
      ${settled.length ? `<details class="small"><summary>Recorded or not applicable (${settled.length})</summary>
        <ul class="inv-dims">${settled.map(d => dimensionRow(d, a.record)).join("")}</ul></details>` : ""}
      <h4 class="inv-head">Next action</h4>
      ${actionHtml(a.next_action, a)}
      ${a.more_actions.length ? `<details class="small"><summary>Later actions (${a.more_actions.length})</summary>
        <ol>${a.more_actions.map(n => `<li>${AS.esc(n.title)}</li>`).join("")}</ol></details>` : ""}
      <h4 class="inv-head">Research progress</h4>
      <p class="small faint">${AS.esc(a.stage_history.note)}</p>
      <p class="small"><a href="${AS.esc(briefUrl(a))}" download>Download the investigation brief (Markdown)</a></p>
    </div>`;
  }

  function deadline(d) {
    if (!d || !d.date) return "No deadline published";
    return `Ends ${d.date}${d.time ? " " + d.time : ""}${d.precision === "date" ? " (date only)" : ""}`;
  }

  // One queue entry on the Investigations page; opens the property in the Listings drawer.
  function entryHtml(e, mode) {
    const href = "/?" + new URLSearchParams({mode, property: e.listing_id}).toString();
    const score = e.opportunity.score == null ? "—" : Math.round(e.opportunity.score);
    const n = e.next_action;
    return `<li class="card inv-entry" data-id="${AS.esc(e.listing_id)}">
      <h2 class="inv-title"><a href="${AS.esc(href)}">${AS.esc(e.title)}</a></h2>
      <p class="small muted">Score ${AS.esc(String(score))} · ${AS.esc(e.price && e.price.label || "")}
        ${e.price && e.price.amount != null ? AS.money(e.price.amount) : ""} · ${AS.esc(deadline(e.deadline))}</p>
      <p class="small">${e.unresolved} unresolved, ${e.concerns} concerns${e.contradictions.length
        ? `, ${e.contradictions.length} conflicting records` : ""}</p>
      ${n ? `<p><b>Next:</b> ${AS.esc(n.title)} <span class="small muted">${AS.esc(n.question)}</span></p>` : ""}
      <p class="small"><a href="${AS.esc(briefUrl({listing_id: e.listing_id, mode}))}" download>Brief (Markdown)</a></p>
    </li>`;
  }

  async function save(button) {
    const row = button.closest(".inv-record"), box = button.closest('[data-role="investigation"]');
    const value = selector => row.querySelector(selector).value;
    button.disabled = true;
    try {
      await AS.api("/api/checklist", {method: "POST", body: {id: box.dataset.id, key: row.dataset.key,
        status: value(".inv-status"), notes: value(".inv-notes"), reference: value(".inv-ref"),
        checked_on: value(".inv-date"), verified_by: value(".inv-by")}});
    } catch (e) {
      button.disabled = false;                     // AS.api said why; nothing was recorded
      return false;
    }
    AS.toast("Recorded in the checklist");
    // The assessment is recalculated from the new record: the drawer, its row and any comparison.
    if (typeof comparison !== "undefined") comparison.markStale(box.dataset.id);
    if (typeof refreshDetails === "function") refreshDetails(button);
    if (typeof load === "function") load({after: "saved"});
    return true;
  }
  document.addEventListener("click", e => {
    const button = e.target.closest && e.target.closest('[data-role="inv-save"]');
    if (button && !button.disabled) save(button);
  });

  return {sectionHtml, entryHtml, briefUrl};
})();
