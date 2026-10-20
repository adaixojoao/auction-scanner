"""Batch 08: the saved-search store (static/listings-saved-searches.js) run in
Node when it is installed. Storage logic only: the page wiring is tested in a
real browser by tests/test_browser_saved_views.py."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

PRELUDE = r'''
const assert = require("node:assert/strict");
require("./static/listings-saved-searches.js");
function memory(initial = null) {
  const box = {document: initial, failWrite: false, failRead: false};
  box.storage = {
    getItem: () => { if (box.failRead) throw new Error("blocked"); return box.document; },
    setItem: (key, value) => { if (box.failWrite) throw new Error("quota"); box.document = value; },
  };
  return box;
}
let sequence = 0;
function store(box, notify) {
  return AuctionSavedSearches.createStore({
    storage: box.storage, notify, makeId: () => "test-" + (++sequence),
    validateView: view => { if (view.mode !== "home") throw new Error("Goal is not one of this page's goals."); return view; },
  });
}
'''


def _run(body):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable; saved-search state test not executed")
    result = subprocess.run([node, "-e", PRELUDE + body], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_round_trip_keeps_only_browsing_fields():
    _run(r'''
const box = memory();
const s = store(box);
s.reload();
const id = s.create("River search", {
  mode: "home", search: "river", country: "PT", page: 5,
  property: "private-selection", user_note: "must not persist", token: "secret", density: "compact"
});
assert.equal(s.apply(id).page, 1);
assert.equal(s.apply(id).search, "river");
for (const text of ["must not persist", "private-selection", "secret", "compact", "page"]) {
  assert.equal(box.document.includes(text), false, text);
}
s.rename(id, "  New name  ");
assert.equal(s.snapshot().records[0].name, "New name");
s.update(id, {mode: "home", search: "forest"});
assert.equal(s.apply(id).search, "forest");
assert.throws(() => s.create("", {mode: "home"}), /1 to 80/);
assert.throws(() => s.create("x".repeat(81), {mode: "home"}), /1 to 80/);
assert.throws(() => s.create("Invalid", {mode: "invented"}), /not one of this page's goals/);
assert.throws(() => s.create("Long", {mode: "home", search: "y".repeat(501)}), /500/);
const copy = s.snapshot();
copy.records[0].view.search = "changed outside";
assert.equal(s.apply(id).search, "forest");
s.remove(id);
assert.equal(s.snapshot().records.length, 0);
assert.throws(() => s.apply(id), /no longer available/);
''')


def test_unreadable_storage_is_never_overwritten():
    _run(r'''
for (const text of ["corrupt json", JSON.stringify({version: 2, records: []}),
                    JSON.stringify({version: 1, records: [{id: "a", name: "A", view: {mode: "invented"}}]}),
                    JSON.stringify({version: 1, records: [{id: "a", name: "A", view: {mode: "home"}},
                                                          {id: "a", name: "B", view: {mode: "home"}}]})]) {
  const box = memory(text);
  const s = store(box);
  const state = s.reload();
  assert.equal(state.loaded, false);
  assert.equal(state.conflict, true);
  assert.equal(state.records.length, 0);
  assert.match(state.warning, /could not be loaded/);
  assert.throws(() => s.create("Must not overwrite", {mode: "home"}), /cannot be changed/);
  assert.equal(box.document, text);
}
''')


def test_blocked_and_full_storage_report_and_keep_the_previous_searches():
    _run(r'''
const blocked = memory();
blocked.failRead = true;
const b = store(blocked);
assert.equal(b.reload().loaded, false);
const box = memory();
const s = store(box);
s.reload();
const id = s.create("Kept", {mode: "home", search: "a"});
const before = box.document;
box.failWrite = true;
assert.throws(() => s.create("Too much", {mode: "home"}), /could not save this change/);
assert.throws(() => s.rename(id, "Other"), /could not save this change/);
assert.equal(box.document, before);
assert.deepEqual(s.snapshot().records.map(r => r.name), ["Kept"]);
''')


def test_another_tabs_change_is_reported_not_overwritten():
    _run(r'''
const box = memory();
const seen = [];
const s = store(box, state => seen.push(state));
s.reload();
const id = s.create("Mine", {mode: "home"});
const other = store(box);
other.reload();
other.rename(id, "Theirs");
const theirs = box.document;
assert.throws(() => s.rename(id, "Mine again"), /another tab/);
assert.equal(box.document, theirs);
assert.equal(s.snapshot().conflict, true);
assert.match(seen[seen.length - 1].warning, /another tab/);
assert.equal(s.apply(id).mode, "home");          // reading still works
s.reload();
assert.equal(s.snapshot().records[0].name, "Theirs");
s.externalChange();
assert.throws(() => s.remove(id), /another tab/);
''')


def test_at_most_fifty_searches():
    _run(r'''
const box = memory();
const s = store(box);
s.reload();
for (let i = 0; i < 50; i++) s.create("S" + i, {mode: "home"});
assert.throws(() => s.create("One more", {mode: "home"}), /At most 50/);
''')
