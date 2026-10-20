// listings-saved-searches.js — the Listings page: named saved searches, kept in this browser only.
// Loaded before listings-view.js; it has no page wiring of its own (listings.js does that),
// so tests/test_batch08_saved_searches.py can run it in Node too.
//
// One versioned document in browser storage. Only the browsing fields below
// are ever copied in: never notes, buyer details, the open property, the page
// or display preferences. The document is read once and written only when you
// change a search; if it cannot be read, it is left exactly as it is. Another
// tab's change is noticed and has to be reloaded before editing: this narrows
// overwrites between tabs but is not an atomic merge (the last write wins in
// the moment between reading and writing).
(function (root) {
  "use strict";
  const STORAGE_KEY = "auction-scanner:saved-searches:v1";
  const FIELDS = [
    "mode", "country", "source", "kind", "status", "max_price", "min_score",
    "search", "properties", "climate", "climate_exact", "sale_method", "sort", "dir"
  ];
  const MAX_RECORDS = 50;
  const MAX_NAME = 80;
  const MAX_SEARCH = 500;
  const CHANGED_ELSEWHERE = "Saved searches changed in another tab. Reload them before editing.";

  function copyView(input) {
    const result = {};
    FIELDS.forEach(function (key) {
      const value = input[key];
      if (typeof value === "string" || typeof value === "boolean"
          || (typeof value === "number" && Number.isFinite(value))) {
        result[key] = value;
      }
    });
    return result;
  }

  function nameOf(value) {
    if (typeof value !== "string") throw new Error("Enter a saved-search name.");
    const name = value.trim();
    if (!name || name.length > MAX_NAME) throw new Error("Use a name from 1 to 80 characters.");
    return name;
  }

  // validateView(view) returns the checked view, or throws (or returns null) with the reason.
  function createStore(options) {
    const storage = options.storage;
    const validateView = options.validateView;
    const notify = options.notify || function () {};
    // randomUUID needs a secure context (the app opened by a LAN address is not one); getRandomValues does not.
    const makeId = options.makeId || function () {
      if (root.crypto && root.crypto.randomUUID) return root.crypto.randomUUID();
      if (!root.crypto || !root.crypto.getRandomValues) throw new Error("Secure ID creation is unavailable.");
      return Array.from(root.crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");
    };
    let records = [];
    let baseline = null;
    let loaded = false;
    let warning = "";
    let conflict = false;

    function snapshot() {
      return {
        records: records.map(function (record) { return {...record, view: {...record.view}}; }),
        loaded, warning, conflict
      };
    }
    function publish() { notify(snapshot()); }
    function raw() { return storage.getItem(STORAGE_KEY); }

    function checked(view) {
      const result = validateView(copyView(view));
      if (!result) throw new Error("Correct the filters before saving this search.");
      return copyView(result);
    }

    function parse(text) {
      if (text == null) return [];
      const document = JSON.parse(text);
      if (!document || document.version !== 1 || !Array.isArray(document.records)
          || document.records.length > MAX_RECORDS) throw new Error("Saved-search storage is invalid.");
      const ids = new Set();
      return document.records.map(function (record) {
        if (!record || typeof record.id !== "string" || !record.id
            || record.id.length > 100 || ids.has(record.id)
            || !record.view || typeof record.view !== "object" || Array.isArray(record.view)) {
          throw new Error("Saved-search storage is invalid.");
        }
        ids.add(record.id);
        return {id: record.id, name: nameOf(record.name), view: checked(record.view)};
      });
    }

    function reload() {
      try {
        const text = raw();
        records = parse(text);
        baseline = text;
        loaded = true;
        warning = "";
        conflict = false;
      } catch (error) {
        // Unreadable or blocked: nothing is shown as saved and nothing is written over it.
        records = [];
        loaded = false;
        warning = "Saved searches could not be loaded from this browser. What is stored was not changed or replaced.";
        conflict = true;
      }
      publish();
      return snapshot();
    }

    function persist(next) {
      if (conflict) throw new Error(loaded ? CHANGED_ELSEWHERE : "Saved searches could not be loaded, so they cannot be changed.");
      let current;
      try { current = raw(); }
      catch (error) { throw new Error("Browser storage is unavailable. Saved searches were not changed."); }
      if (current !== baseline) {
        conflict = true;
        warning = CHANGED_ELSEWHERE;
        publish();
        throw new Error(warning);
      }
      const text = JSON.stringify({version: 1, records: next});
      try { storage.setItem(STORAGE_KEY, text); }
      catch (error) { throw new Error("Browser storage could not save this change. The previous saved searches remain."); }
      records = next;
      baseline = text;
      warning = "";
      publish();
    }

    function checkedView(input) {
      if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("View state is invalid.");
      if (typeof input.search === "string" && input.search.length > MAX_SEARCH) {
        throw new Error("Saved searches support up to 500 search characters.");
      }
      return checked(input);
    }

    function find(id) {
      return records.find(function (record) { return record.id === id; });
    }
    function create(name, view) {
      if (records.length >= MAX_RECORDS) throw new Error("At most 50 searches can be saved in this browser.");
      const next = {id: makeId(), name: nameOf(name), view: checkedView(view)};
      if (find(next.id)) throw new Error("Saved-search ID already exists.");
      persist([...records, next]);
      return next.id;
    }
    function update(id, view) {
      if (!find(id)) throw new Error("That saved search is no longer available.");
      const validated = checkedView(view);
      persist(records.map(function (record) { return record.id === id ? {...record, view: validated} : record; }));
    }
    function rename(id, name) {
      if (!find(id)) throw new Error("That saved search is no longer available.");
      const validated = nameOf(name);
      persist(records.map(function (record) { return record.id === id ? {...record, name: validated} : record; }));
    }
    function remove(id) {
      if (!find(id)) throw new Error("That saved search is no longer available.");
      persist(records.filter(function (record) { return record.id !== id; }));
    }
    function apply(id) {
      const record = find(id);
      if (!record) throw new Error("That saved search is no longer available.");
      return {...checkedView(record.view), page: 1};
    }
    function externalChange() {
      if (!loaded) return;
      conflict = true;
      warning = CHANGED_ELSEWHERE;
      publish();
    }
    return {reload, create, update, rename, remove, apply, snapshot, externalChange};
  }
  root.AuctionSavedSearches = {STORAGE_KEY, FIELDS, copyView, createStore};
})(typeof window !== "undefined" ? window : globalThis);
