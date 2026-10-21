// Investigation views (investigation.py, investigation_stages.py): what is still unknown about a
// property, what to find out next, and where your research stands. Nothing here verifies, scores,
// hides or bids, and no stage means a property is ready to buy. Shared by the drawer and the
// Investigations page.
const Investigation = (() => {
  const STATE = {verified: "Verified by you", unresolved: "Unresolved", concern: "Concern",
                 not_applicable: "Not applicable", established: "Stated by the sale"};
  const BADGE = {verified: "b-drop", concern: "b-hidden", not_applicable: "b-muted", established: "b-muted"};
  const STAGE_ORDER = ["discovered", "screening", "documents_requested", "evidence_reviewed", "visit_warranted",
                       "professional_review", "decision_recorded"];
  // A stage form you are typing in survives a refresh of the drawer (per listing, this page only).
  const drafts = new Map();

  function briefUrl(a) {
    return "/api/investigation/brief.md?" + new URLSearchParams({id: a.listing_id, mode: a.mode}).toString();
  }

  function badge(d) {
    const word = d.source_reported ? "Source-reported, unverified" : (STATE[d.state] || d.state);
    return `<span class="badge ${BADGE[d.state] || "b-short"}">${AS.esc(word)}</span>`;
  }

  function when(iso) { return AS.esc(String(iso || "").slice(0, 16).replace("T", " ")); }

  // ── Unresolved evidence ──

  // The checklist items behind a dimension, recorded in place (POST /api/checklist, the
  // one record of what you checked). Saving a status records what you found; nothing
  // else, and no other page's draft, changes.
  function recorderHtml(d, record) {
    if (!record) return "";
    const items = d.evidence_refs.filter(r => r.type === "checklist").map(r => record.items[r.key]).filter(Boolean);
    if (!items.length) return "";
    const item = x => `<div class="inv-record" data-key="${AS.esc(x.key)}">
        <label class="inv-field"><span>${AS.esc(x.label)}</span>
          <select class="inv-status">${record.statuses.map(([k, v]) =>
            `<option value="${AS.esc(k)}"${k === x.status ? " selected" : ""}>${AS.esc(v)}</option>`).join("")}</select></label>
        ${x.needs_recheck ? `<p class="small warn">The listing changed since this was recorded: check it again.</p>` : ""}
        <label class="inv-field"><span>What you found</span>
          <input class="inv-notes" value="${AS.esc(x.notes)}" maxlength="2000"></label>
        <label class="inv-field"><span>Where the document is</span>
          <input class="inv-ref" placeholder="a link, or where it is filed" value="${AS.esc(x.reference)}" maxlength="500"></label>
        <label class="inv-field"><span>Checked on</span>
          <input class="inv-date" type="date" value="${AS.esc(x.checked_on)}"></label>
        <input class="inv-by" type="hidden" value="${AS.esc(x.verified_by)}">
        <button type="button" class="btn btn-sm" data-role="inv-save">Save</button></div>`;
    return `<details class="small inv-recorder"><summary>Record what you found (${items.length})</summary>
      ${items.map(item).join("")}
      <p class="faint">"Verified" means you checked it yourself: your record, not legal advice.</p></details>`;
  }

  function dimensionRow(d, record) {
    return `<li class="inv-dim" data-dimension="${AS.esc(d.key)}" data-state="${AS.esc(d.state)}">
      <div class="inv-dim-head"><b>${AS.esc(d.label)}</b> ${badge(d)}</div>
      <p class="small">${AS.esc(d.summary)}</p>
      ${d.limitations.length ? `<details class="small inv-limits"><summary>Limits of this record (${d.limitations.length})</summary>
        <ul class="faint">${d.limitations.map(l => `<li>${AS.esc(l)}</li>`).join("")}</ul></details>` : ""}
      ${recorderHtml(d, record)}
    </li>`;
  }

  // ── Next action ──

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
    const where = a.record && n.dimension !== "contradiction"
      ? (a.dimensions.find(d => d.key === n.dimension) || {}).label : "";
    return `<div class="inv-action" data-action="${AS.esc(n.key)}">
      <p class="inv-action-title"><b>${AS.esc(n.title)}</b></p>
      <p class="inv-question">${AS.esc(n.question)}</p>
      <p class="small muted">${AS.esc(n.reason)}</p>
      <p class="small"><b>Ask for:</b> ${n.requested_evidence.map(AS.esc).join("; ")}</p>
      ${lettersHtml(n, a.status)}
      ${n.links.length ? `<p class="small d-links">${n.links.map(l => AS.link(l.url, l.label + " ↗")).join(" · ")}</p>` : ""}
      ${where ? `<p class="small">Record the answer under <b>${AS.esc(where)}</b> above.</p>` : ""}
      <p class="small faint">${AS.esc(n.completion_note)}</p>
    </div>`;
  }

  // ── Research progress (stages) ──

  // The seven stages in order; a pause or rejection is shown beside the last stage reached.
  function stepperHtml(h) {
    const reached = [...(h.history || [])].reverse().find(r => STAGE_ORDER.includes(r.stage));
    const at = reached ? STAGE_ORDER.indexOf(reached.stage) : -1;
    const labels = Object.fromEntries(h.stages || []);
    const current = h.current;
    const disposition = current && !STAGE_ORDER.includes(current.stage) ? current : null;
    return `<ol class="inv-stepper" aria-label="Research stages">${STAGE_ORDER.map((key, i) => {
      const state = i < at ? "done" : i === at ? "current" : "todo";
      return `<li class="inv-step" data-step="${key}" data-state="${state}"${i === at && !disposition ? ' aria-current="step"' : ""}>
        <span class="inv-step-dot" aria-hidden="true">${i < at ? "✓" : i + 1}</span>
        <span class="inv-step-label">${AS.esc(labels[key] || key)}</span></li>`;
    }).join("")}</ol>
    ${disposition ? `<p class="small"><span class="badge ${disposition.stage === "rejected" ? "b-hidden" : "b-short"}">${
      AS.esc(disposition.label)}</span> since ${when(disposition.created_at)} UTC. The evidence and history stay; nothing was dismissed or deleted.</p>` : ""}`;
  }

  function historyHtml(h) {
    const rows = [...(h.history || [])].reverse();
    if (!rows.length) return `<p class="small muted">No stage recorded yet.</p>`;
    const labels = Object.fromEntries([...(h.stages || []), ...(h.dispositions || [])]);
    return `<details class="small inv-history"${rows.length <= 3 ? " open" : ""}><summary>History (${rows.length})</summary>
      <ol class="inv-timeline">${rows.map(r => `<li data-stage="${AS.esc(r.stage)}">
        <div><b>${AS.esc(r.label)}</b>${r.previous_stage ? ` <span class="muted">from ${AS.esc(labels[r.previous_stage] || r.previous_stage)}</span>` : ""}
          <span class="badge b-muted">${AS.esc(r.reason_kind_label)}</span></div>
        <div class="muted">${when(r.created_at)} UTC</div>
        <p>${AS.esc(r.reason)}</p>
        ${r.spend && r.spend.amount != null ? `<p>Spent ${AS.esc(r.spend.amount.toFixed(2))} ${AS.esc(r.spend.currency)} on ${
          AS.esc(r.spend.date)} (${AS.esc(r.spend.category_label || r.spend.category)})</p>` : ""}
        ${r.facts_changed && r.facts_changed.length ? `<p class="warn">Recorded when the ${AS.esc(r.facts_changed.join(", "))}
          differed from now: it may concern an earlier sale event or notice.</p>` : ""}
      </li>`).join("")}</ol></details>`;
  }

  function stageFormHtml(a) {
    const h = a.stage_history, d = drafts.get(a.listing_id) || {};
    const current = h.current ? h.current.stage : "";
    const options = list => list.map(([k, v]) =>
      `<option value="${AS.esc(k)}"${k === (d.stage || "") ? " selected" : ""}>${AS.esc(v)}</option>`).join("");
    const keys = a.record ? Object.values(a.record.items) : [];
    const today = new Date().toISOString().slice(0, 10);
    const spendOpen = d.amount ? " open" : "";
    return `<form class="inv-stage-form" data-role="stage-form" data-current="${AS.esc(current)}" novalidate>
      <div class="inv-form-grid">
        <label class="inv-field"><span>New stage</span>
          <select class="inv-stage" required><option value="">Choose…</option>
            <optgroup label="Stages">${options(h.stages)}</optgroup>
            <optgroup label="Or set aside">${options(h.dispositions)}</optgroup></select></label>
        <label class="inv-field"><span>Why</span>
          <select class="inv-kind" required>${h.reason_kinds.map(([k, v]) =>
            `<option value="${AS.esc(k)}"${k === (d.kind || "progress") ? " selected" : ""}>${AS.esc(v)}</option>`).join("")}</select></label>
      </div>
      <label class="inv-field"><span>In your words <span class="muted">(kept on this PC; not in the brief)</span></span>
        <textarea class="inv-reason" rows="2" maxlength="1000" required>${AS.esc(d.reason || "")}</textarea></label>
      ${keys.length ? `<details class="small"><summary>Evidence this rests on (optional)</summary>
        <div class="inv-checks">${keys.map(x => `<label><input type="checkbox" class="inv-ref-key" value="${AS.esc(x.key)}"${
          (d.keys || []).includes(x.key) ? " checked" : ""}> ${AS.esc(x.label)}</label>`).join("")}</div></details>` : ""}
      <details class="small"${spendOpen}><summary>Money spent on this step (optional)</summary>
        <p class="faint">Only what you actually paid. Leave it empty when nothing was spent or you do not know: unknown is not zero.</p>
        <div class="inv-form-grid">
          <label class="inv-field"><span>Amount</span><input class="inv-spend-amount" type="number" min="0" step="0.01" inputmode="decimal" value="${AS.esc(d.amount || "")}"></label>
          <label class="inv-field"><span>Currency</span><input class="inv-spend-currency" maxlength="3" value="${AS.esc(d.currency || "EUR")}"></label>
          <label class="inv-field"><span>Date</span><input class="inv-spend-date" type="date" max="${today}" value="${AS.esc(d.date || today)}"></label>
          <label class="inv-field"><span>For</span><select class="inv-spend-category">${h.spend_categories.map(([k, v]) =>
            `<option value="${AS.esc(k)}"${k === (d.category || "registry") ? " selected" : ""}>${AS.esc(v)}</option>`).join("")}</select></label>
        </div></details>
      <p class="inv-form-error small bad" role="alert"${d.error ? "" : " hidden"}>${AS.esc(d.error || "")}</p>
      <button type="submit" class="btn btn-sm btn-primary" data-role="stage-save">Record stage</button>
      <span class="small faint">Recording a stage changes nothing else: no shortlist, dismissal, letter or verification.</span>
    </form>`;
  }

  function progressHtml(a) {
    const h = a.stage_history;
    if (!h || !h.available) return `<p class="small faint">${AS.esc((h && h.note) || "")}</p>`;
    const totals = Object.entries(h.spend_totals || {});
    return `${stepperHtml(h)}
      ${totals.length ? `<p class="small">Research spending you recorded: ${totals.map(([c, v]) =>
        `${AS.esc(v.toFixed(2))} ${AS.esc(c)}`).join(", ")}. Anything not recorded is unknown, not zero.</p>` : ""}
      ${historyHtml(h)}
      <details class="inv-record-stage"${drafts.has(a.listing_id) ? " open" : ""}><summary>Record a stage or decision</summary>
        ${stageFormHtml(a)}</details>
      <p class="small faint">${AS.esc(h.note)}</p>`;
  }

  // ── The drawer section: Opportunity → Unresolved evidence → Next action → Research progress ──
  function sectionHtml(a) {
    if (!a) return "";
    const score = a.opportunity.score == null ? "—" : Math.round(a.opportunity.score);
    const open = a.dimensions.filter(d => d.state === "unresolved" || d.state === "concern")
      .sort((x, y) => (y.state === "concern") - (x.state === "concern"));
    const settled = a.dimensions.filter(d => !(d.state === "unresolved" || d.state === "concern"));
    const stage = a.stage_history && a.stage_history.current;
    const chip = (n, word, cls) => `<span class="inv-chip ${cls}"><b>${n}</b> ${word}</span>`;
    return `<div class="guide investigation" data-role="investigation" data-id="${AS.esc(a.listing_id)}">
      <div class="inv-glance" aria-label="At a glance">
        ${chip(a.unresolved, "unresolved", "")}
        ${chip(a.concerns, a.concerns === 1 ? "concern" : "concerns", a.concerns ? "is-bad" : "")}
        ${a.contradictions.length ? chip(a.contradictions.length, "conflicting", "is-bad") : ""}
        <span class="inv-chip">Stage: <b>${AS.esc(stage ? stage.label : "not started")}</b></span>
      </div>
      <p class="small muted">Assessed for ${AS.esc(a.purpose || "no goal")}. Nothing here establishes that the
        property is ready to buy.${a.eligibility ? ` Not in the queue: ${AS.esc(a.eligibility)}.` : ""}</p>
      <h4 class="inv-head">Opportunity</h4>
      <p class="small">Score <b>${AS.esc(String(score))}</b>${a.opportunity.reasons.length
        ? `: ${AS.esc(a.opportunity.reasons.join(", "))}` : ""}. A high score is not evidence.</p>
      <h4 class="inv-head">Unresolved evidence</h4>
      ${a.contradictions.length ? `<ul class="inv-contradictions small">${a.contradictions.map(c =>
        `<li><b>Conflict:</b> ${AS.esc(c)}</li>`).join("")}</ul>` : ""}
      ${open.length ? `<ul class="inv-dims">${open.map(d => dimensionRow(d, a.record)).join("")}</ul>`
        : `<p class="small">Nothing unresolved in the stored records.</p>`}
      ${settled.length ? `<details class="small"><summary>Recorded or not applicable (${settled.length})</summary>
        <ul class="inv-dims">${settled.map(d => dimensionRow(d, a.record)).join("")}</ul></details>` : ""}
      <h4 class="inv-head">Next action</h4>
      ${actionHtml(a.next_action, a)}
      ${a.more_actions.length ? `<details class="small"><summary>Later actions (${a.more_actions.length})</summary>
        <ol>${a.more_actions.map(n => `<li>${AS.esc(n.title)}</li>`).join("")}</ol></details>` : ""}
      <h4 class="inv-head">Research progress</h4>
      ${progressHtml(a)}
      <p class="small"><a href="${AS.esc(briefUrl(a))}" download>Download the investigation brief (Markdown)</a>
        <span class="faint">: no notes, contacts, buyer details or stage reasons</span></p>
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
    const stage = e.stage;
    return `<li class="card inv-entry" data-id="${AS.esc(e.listing_id)}" data-stage="${AS.esc(stage ? stage.stage : "")}">
      <div class="inv-entry-head">
        <h2 class="inv-title"><a href="${AS.esc(href)}">${AS.esc(e.title)}</a></h2>
        <span class="badge ${stage && stage.stage === "rejected" ? "b-hidden" : "b-muted"}">${AS.esc(stage ? stage.label : "Not started")}</span>
      </div>
      <p class="small muted">Score ${AS.esc(String(score))} · ${AS.esc(e.price && e.price.label || "")}
        ${e.price && e.price.amount != null ? AS.money(e.price.amount) : ""} · ${AS.esc(deadline(e.deadline))}</p>
      <p class="inv-glance small">
        <span class="inv-chip"><b>${e.unresolved}</b> unresolved</span>
        <span class="inv-chip ${e.concerns ? "is-bad" : ""}"><b>${e.concerns}</b> ${e.concerns === 1 ? "concern" : "concerns"}</span>
        ${e.contradictions.length ? `<span class="inv-chip is-bad"><b>${e.contradictions.length}</b> conflicting records</span>` : ""}</p>
      ${n ? `<p class="inv-next"><b>Next:</b> ${AS.esc(n.title)} <span class="small muted">${AS.esc(n.question)}</span></p>` : ""}
      <p class="small"><a href="${AS.esc(href)}">Open the details</a> ·
        <a href="${AS.esc(briefUrl({listing_id: e.listing_id, mode}))}" download>Brief (Markdown)</a></p>
    </li>`;
  }

  // ── Saving ──

  function after(box) {
    // The assessment is recalculated from the new record: the drawer, its row and any comparison.
    if (typeof comparison !== "undefined") comparison.markStale(box.dataset.id);
    if (typeof refreshDetails === "function") refreshDetails(box);
    if (typeof load === "function") load({after: "saved"});
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
    after(box);
    return true;
  }

  function readForm(form) {
    const q = s => form.querySelector(s);
    return {stage: q(".inv-stage").value, kind: q(".inv-kind").value, reason: q(".inv-reason").value,
            keys: [...form.querySelectorAll(".inv-ref-key:checked")].map(x => x.value),
            amount: q(".inv-spend-amount").value.trim(), currency: q(".inv-spend-currency").value.trim(),
            date: q(".inv-spend-date").value, category: q(".inv-spend-category").value};
  }

  async function saveStage(form) {
    const box = form.closest('[data-role="investigation"]'), id = box.dataset.id;
    const f = readForm(form), error = form.querySelector(".inv-form-error"), button = form.querySelector('[data-role="stage-save"]');
    const fail = text => { error.textContent = text; error.hidden = false; return false; };
    if (!f.stage) return fail("Choose a stage or decision.");
    if (!f.reason.trim()) return fail("Say why, in a few words.");
    error.hidden = true;
    button.disabled = true;
    const body = {id, stage: f.stage, reason_kind: f.kind, reason: f.reason, checklist_keys: f.keys,
                  expected_previous: form.dataset.current,
                  spend: f.amount ? {amount: f.amount, currency: f.currency, date: f.date, category: f.category} : null};
    try {
      const response = await fetch("/api/investigation/stage", {method: "POST", headers: {"Content-Type": "application/json"},
                                                               body: JSON.stringify(body)});
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        button.disabled = false;
        const message = (data && data.error) || `Could not record the stage (${response.status}).`;
        fail(message);
        drafts.set(id, {...readForm(form), error: message});
        if (response.status === 409) after(box);   // show the newer stage; your draft and the reason stay
        return false;
      }
    } catch (e) {
      button.disabled = false;
      return fail("Could not record the stage: the app did not answer. Nothing was recorded.");
    }
    drafts.delete(id);
    AS.toast("Stage recorded");
    after(box);
    return true;
  }

  document.addEventListener("click", e => {
    const button = e.target.closest && e.target.closest('[data-role="inv-save"]');
    if (button && !button.disabled) save(button);
  });
  document.addEventListener("submit", e => {
    const form = e.target.closest && e.target.closest('[data-role="stage-form"]');
    if (!form) return;
    e.preventDefault();
    saveStage(form);
  });
  const keepDraft = e => {
    const form = e.target.closest && e.target.closest('[data-role="stage-form"]');
    if (form) drafts.set(form.closest('[data-role="investigation"]').dataset.id, readForm(form));
  };
  document.addEventListener("input", keepDraft);
  document.addEventListener("change", keepDraft);

  return {sectionHtml, entryHtml, briefUrl};
})();
