// listings-notes.js — the Listings page: note drafts in memory, saved one at a time, and the warnings before leaving with one unsaved.
// Plain scripts sharing one global scope, loaded in order by templates/listings.html
// (listings-saved-searches.js, listings-view.js, listings-summary.js, listings-details.js,
//  listings-cards.js, listings-notes.js, listings-drawer.js, listings.js).

// ── Notes: a draft per listing in memory, saved one at a time ──
// The store is listing-notes.js (AuctionNotes). The server keeps exactly what
// it is sent (up to 2,000 characters, the field's maxlength; an empty note is
// removed; a longer one or a missing listing is refused) and answers
// {ok, id, length} with no copy of the text. "Saved" shows only when that
// answer matches the text sent. Drafts never go into the URL or browser
// storage. Two windows on one note: the last save wins.

const NOTE_PHASE = {saved: "Saved", unsaved: "Unsaved changes", saving: "Saving…", error: "Could not save"};
function noteFields(id) { return [...document.querySelectorAll("textarea.d-note")].filter(t => t.dataset.id === id); }
function noteStatuses(id) { return [...document.querySelectorAll(".note-status")].filter(s => s.dataset.id === id); }

// Every place the listing's note shows follows the same draft; the field you
// are typing in is never rewritten, so the caret stays put.
const notes = AuctionNotes.createStore({
  persist: AuctionNotes.persist,
  notify(id, state) {
    noteFields(id).forEach(field => {
      if (field !== document.activeElement && field.value !== state.draft) field.value = state.draft;
    });
    noteStatuses(id).forEach(el => {
      el.dataset.phase = state.phase;
      el.querySelector(".note-phase").textContent = NOTE_PHASE[state.phase] || "";
      el.querySelector(".note-retry").hidden = state.phase !== "error";
    });
  },
});

let noteSeq = 0;
function noteHtml(d) {
  const record = notes.seed(d.id, d.user_note || "");      // the server's text only the first time; a draft wins
  const fieldId = `note-field-${++noteSeq}`, id = AS.esc(d.id);
  return `<div class="d-note-wrap">
    <label class="small muted" for="${fieldId}">Notes</label>
    <textarea id="${fieldId}" class="d-note" rows="3" maxlength="2000" placeholder="Personal notes about this listing…"
      data-id="${id}" data-role="note">${AS.esc(record.draft)}</textarea>
    <p class="small note-status" data-id="${id}" data-phase="${AS.esc(record.phase)}" role="status">
      <span class="note-phase">${NOTE_PHASE[record.phase] || ""}</span>
      <button type="button" class="btn btn-sm note-retry" data-id="${id}" ${record.phase === "error" ? "" : "hidden"}>Retry saving note</button>
    </p>
  </div>`;
}

document.addEventListener("input", e => {
  if (e.target.matches && e.target.matches("textarea.d-note")) notes.edit(e.target.dataset.id, e.target.value);
});
document.addEventListener("focusout", e => {
  if (e.target.matches && e.target.matches("textarea.d-note")) notes.flush(e.target.dataset.id);
});
document.addEventListener("click", e => {
  const retry = e.target.closest && e.target.closest(".note-retry");
  if (retry) notes.retry(retry.dataset.id);
});

// The browser's own warning while a note is unsaved or being saved. Not every
// window shows it (the desktop app's close button may not), so it is a
// warning, not a guarantee.
window.addEventListener("beforeunload", e => {
  if (!notes.hasUnsaved()) return;
  e.preventDefault();
  e.returnValue = "";
});

// Links to the app's other pages ask first while a note is unsaved.
document.addEventListener("click", e => {
  const link = e.target.closest && e.target.closest("a[href]");
  if (!link || !notes.hasUnsaved() || e.defaultPrevented || link.target === "_blank" || e.button !== 0
      || e.ctrlKey || e.metaKey || e.shiftKey) return;
  let url;
  try { url = new URL(link.href, location.href); } catch (err) { return; }
  if (url.host !== location.host || url.pathname === location.pathname || /^\/(api|export)\//.test(url.pathname)) return;
  e.preventDefault();
  const dlg = $("leave-dialog");
  const choose = async choice => {
    dlg.querySelectorAll("[data-leave]").forEach(b => { b.onclick = null; });
    if (choice === "stay") { dlg.close(); return; }
    if (choice === "save") {
      $("leave-text").textContent = "Saving…";
      if (!(await notes.flushAll()) || notes.hasUnsaved()) {
        $("leave-text").textContent = "The note could not be saved. Stay to retry, or leave without saving it.";
        dlg.querySelectorAll("[data-leave]").forEach(b => { b.onclick = () => choose(b.dataset.leave); });
        return;
      }
    }
    dlg.close();
    location.href = url.href;
  };
  $("leave-text").textContent = "Save it before leaving, stay on this page, or leave without saving it.";
  dlg.querySelectorAll("[data-leave]").forEach(b => { b.onclick = () => choose(b.dataset.leave); });
  dlg.showModal();
});
