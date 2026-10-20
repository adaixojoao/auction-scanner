// listings-comparison-store.js — the Listings page: which properties are selected for comparison.
// No DOM, nothing stored: page-session memory only (tests/test_batch09_comparison_state.py runs it in Node).
// At most four stable listing IDs, in the order chosen; a title is kept only to name them in the tray.
(function (root) {
  "use strict";

  function createStore(options = {}) {
    const maximum = 4;
    const notify = options.notify || function () {};
    const selections = new Map();

    function snapshot() {
      return [...selections.values()].map(item => ({...item}));
    }
    function publish() { notify(snapshot()); }
    function has(id) { return selections.has(id); }
    function add(id, title) {
      if (typeof id !== "string" || !id) throw new Error("Choose a property with a stable ID.");
      if (selections.has(id)) return false;
      if (selections.size >= maximum) throw new Error("Compare at most four properties. Remove one first.");
      selections.set(id, {id, title: typeof title === "string" && title ? title : "Untitled listing"});
      publish();
      return true;
    }
    function remove(id) {
      const removed = selections.delete(id);
      if (removed) publish();
      return removed;
    }
    function toggle(id, title) { return has(id) ? remove(id) : add(id, title); }
    function updateTitle(id, title) {
      if (!selections.has(id) || typeof title !== "string" || !title) return;
      const item = selections.get(id);
      if (item.title === title) return;
      selections.set(id, {...item, title});
      publish();
    }
    function clear() {
      if (!selections.size) return;
      selections.clear();
      publish();
    }
    return {snapshot, has, add, remove, toggle, updateTitle, clear, maximum};
  }

  root.AuctionComparisonStore = {createStore};
})(typeof window !== "undefined" ? window : globalThis);
