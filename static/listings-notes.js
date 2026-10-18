// listings-notes.js — the Listings page: note drafts in memory, saved one at a time, and the warnings before leaving with one unsaved.
// Plain scripts sharing one global scope, loaded in order by templates/listings.html
// (listings-view.js, listings-details.js, listings-notes.js, listings.js).

// ── Notes: a draft per listing in memory, saved one at a time ──
// The server keeps exactly what it is sent (up to 2,000 characters, the
// field's maxlength; an empty note is removed; a longer one or a missing
// listing is refused) and answers {ok, id, length} with no copy of the text.
// "Saved" shows only when that answer matches the text sent. Drafts never go
// into the URL or browser storage. Two windows on one note: the last save wins.
function createNoteQueue({persist, notify, delay = 700}) {
  const records = new Map();
  function get(id, initialText = "") {
    if (!records.has(id)) {
      records.set(id, {draft: initialText, saved: initialText, version: 0, running: false, timer: null, phase: "saved"});
    }
    return records.get(id);
  }
  function publish(id, record, message = "") {
    notify(id, {draft: record.draft, phase: record.phase, dirty: record.draft !== record.saved, message});
  }
  function edit(id, text) {
    const record = get(id);
    record.draft = text;
    record.version += 1;
    clearTimeout(record.timer);
    record.phase = record.running ? "saving" : (record.draft === record.saved ? "saved" : "unsaved");
    publish(id, record);
    record.timer = setTimeout(() => flush(id), delay);
  }
  async function flush(id) {
    const record = get(id);
    clearTimeout(record.timer);
    record.timer = null;
    if (record.running) return record.pending;
    if (record.draft === record.saved) {
      record.phase = "saved";
      publish(id, record);
      return true;
    }
    record.running = true;
    record.pending = (async () => {
      try {
        while (record.draft !== record.saved) {
          const snapshot = record.draft;
          const snapshotVersion = record.version;
          record.phase = "saving";
          publish(id, record);
          await persist(id, snapshot);
          record.saved = snapshot;      // acknowledged exactly as sent
          if (record.version === snapshotVersion) break;
        }
        record.phase = record.draft === record.saved ? "saved" : "unsaved";
        publish(id, record);
        if (record.phase === "unsaved") record.timer = setTimeout(() => flush(id), delay);
        return record.phase === "saved";
      } catch (error) {
        record.phase = "error";
        publish(id, record, "Could not save this note. Retry saves the latest text.");
        return false;
      } finally {
        record.running = false;
      }
    })();
    return record.pending;
  }
  function seed(id, text) { return get(id, text); }
  function isPending(id) {
    const record = records.get(id);
    return !!record && (record.running || record.draft !== record.saved);
  }
  function hasUnsaved() { return [...records.keys()].some(isPending); }
  function flushAll() { return Promise.all([...records.keys()].filter(isPending).map(flush)); }
  return {seed, edit, flush, retry: flush, isPending, hasUnsaved, flushAll};
}

async function persistNote(id, note) {
  const response = await fetch("/api/listing/note", {method: "POST", headers: {"Content-Type": "application/json"},
                                                      body: JSON.stringify({id, note})});
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  const ack = await response.json().catch(() => null);
  // String length is in UTF-16 units, as the server counts it.
  if (!ack || ack.ok !== true || ack.id !== id || ack.length !== note.length) {
    throw new Error("The save was not acknowledged");
  }
}

const NOTE_PHASE = {saved: "Saved", unsaved: "Unsaved changes", saving: "Saving…", error: "Could not save"};
function noteFields(id) { return [...document.querySelectorAll("textarea.d-note")].filter(t => t.dataset.id === id); }
function noteStatuses(id) { return [...document.querySelectorAll(".note-status")].filter(s => s.dataset.id === id); }

// Every place the listing's note shows follows the same draft; the field you
// are typing in is never rewritten, so the caret stays put.
const notes = createNoteQueue({
  persist: persistNote,
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
  const record = notes.seed(d.id, d.user_note || "");      // the server's text only the first time
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
      const results = await notes.flushAll();
      if (results.some(ok => !ok) || notes.hasUnsaved()) {
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
