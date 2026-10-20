"""Batch 09: the comparison selection store (static/listings-comparison-store.js)
in Node when it is installed. Selection logic only: the page wiring is tested
in a real browser by tests/test_browser_comparison.py."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_comparison_store():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable; comparison state test not executed")
    code = r'''
const assert = require("node:assert/strict");
require("./static/listings-comparison-store.js");
const changes = [];
const store = AuctionComparisonStore.createStore({notify: state => changes.push(state)});
assert.equal(store.add("a", "First"), true);
assert.equal(store.add("a", "Duplicate"), false);
store.add("b", "Second");
store.add("c", "Third");
store.add("d", "Fourth");
assert.throws(() => store.add("e", "Fifth"), /at most four/);
assert.deepEqual(store.snapshot().map(item => item.id), ["a", "b", "c", "d"]);
const copy = store.snapshot();
copy[0].title = "Changed outside";
assert.equal(store.snapshot()[0].title, "First");
store.updateTitle("a", "Fresh title");
assert.equal(store.snapshot()[0].title, "Fresh title");
const published = changes.length;
store.updateTitle("a", "Fresh title");                 // no change, no notification
assert.equal(changes.length, published);
store.toggle("a", "Fresh title");
assert.equal(store.has("a"), false);
store.add("e", "Fifth");
assert.deepEqual(store.snapshot().map(item => item.id), ["b", "c", "d", "e"]);
assert.equal(store.remove("zzz"), false);
store.clear();
assert.equal(store.snapshot().length, 0);
assert.throws(() => store.add("", "Invalid"));
assert.throws(() => store.add(5, "Invalid"));
assert.equal(store.add("f", "").valueOf(), true);
assert.equal(store.snapshot()[0].title, "Untitled listing");
'''
    result = subprocess.run([node, "-e", code], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
