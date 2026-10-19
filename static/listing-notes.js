// listing-notes.js — note drafts held in memory and saved one at a time per listing.
// No DOM and no network of its own: listings-notes.js gives it the save call
// and draws what it reports. Loaded before the other Listings scripts; Node
// tests load it directly (tests/test_batch04_frontend_state.py).
// Drafts never go into the URL, browser storage or logs. A failed save keeps
// the draft; Retry saves the latest text. Not a crash backup, and two windows
// on one note: the last save wins.
(function (root) {
  "use strict";

  function createStore({persist, notify = () => {}, delay = 700}) {
    const records = new Map();

    function record(id, initialText) {
      if (!records.has(id)) {
        const text = initialText == null ? "" : String(initialText);
        records.set(id, {draft: text, saved: text, version: 0, phase: "saved", timer: null, pending: null, error: ""});
      }
      return records.get(id);
    }

    function state(id) {
      const item = records.get(id);
      if (!item) return null;
      return {draft: item.draft, saved: item.saved, version: item.version, phase: item.phase,
              dirty: item.draft !== item.saved, running: item.pending !== null, error: item.error};
    }

    function publish(id) { notify(id, state(id)); }

    function cancelTimer(item) {
      clearTimeout(item.timer);
      item.timer = null;
    }

    // The server's text counts only the first time: a refresh never erases a draft.
    function seed(id, serverText) {
      record(id, serverText);
      return state(id);
    }

    function edit(id, text) {
      if (typeof text !== "string") throw new TypeError("Note must be text");
      const item = record(id, "");
      if (item.draft === text) return state(id);
      item.draft = text;
      item.version += 1;
      item.error = "";
      cancelTimer(item);
      item.phase = item.pending ? "saving" : (item.draft === item.saved ? "saved" : "unsaved");
      publish(id);
      if (item.draft !== item.saved) item.timer = setTimeout(() => flush(id), delay);
      return state(id);
    }

    function flush(id) {
      const item = record(id, "");
      cancelTimer(item);
      if (item.pending) return item.pending;
      if (item.draft === item.saved) {
        item.phase = "saved";
        item.error = "";
        publish(id);
        return Promise.resolve(true);
      }
      // Started in a microtask, so `pending` is set before anyone is notified.
      const pending = Promise.resolve().then(async () => {
        try {
          while (item.draft !== item.saved) {
            const snapshot = item.draft;
            const snapshotVersion = item.version;
            item.phase = "saving";
            item.error = "";
            publish(id);
            const answer = await persist(id, snapshot);
            // "Saved" only when the answer is about this listing and this text
            // (String.length is UTF-16 units, as the server counts).
            if (!answer || answer.ok !== true || answer.id !== id || answer.length !== snapshot.length) {
              throw new Error("Note acknowledgement did not match the submitted note");
            }
            item.saved = snapshot;
            if (item.version === snapshotVersion) break;
          }
          item.phase = item.draft === item.saved ? "saved" : "unsaved";
          item.error = "";
          return item.draft === item.saved;
        } catch (error) {
          item.phase = "error";
          item.error = "Could not save this note. Retry saves the latest text.";
          return false;
        } finally {
          item.pending = null;
          cancelTimer(item);          // a failed save never retries by itself
          if (item.phase !== "error" && item.draft !== item.saved) {
            item.phase = "unsaved";
            item.timer = setTimeout(() => flush(id), delay);
          }
          publish(id);
        }
      });
      item.pending = pending;
      item.phase = "saving";
      publish(id);
      return pending;
    }

    function isPending(id) {
      const item = records.get(id);
      return !!item && (item.pending !== null || item.draft !== item.saved);
    }

    function hasUnsaved() { return [...records.keys()].some(isPending); }

    // One answer for all of them: true only when every note is saved.
    async function flushAll() {
      const results = await Promise.all([...records.keys()].filter(isPending).map(flush));
      return results.every(Boolean) && !hasUnsaved();
    }

    // Stops waiting saves; a save already sent is not taken back.
    function dispose() { records.forEach(cancelTimer); }

    return {seed, edit, flush, retry: flush, state, isPending, hasUnsaved, flushAll, dispose};
  }

  // POST /api/listing/note answers {ok, id, length} with no copy of the text.
  async function persist(id, text) {
    const response = await fetch("/api/listing/note", {method: "POST", headers: {"Content-Type": "application/json"},
                                                        body: JSON.stringify({id, note: text})});
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    return body;
  }

  root.AuctionNotes = {createStore, persist};
})(typeof window !== "undefined" ? window : globalThis);
