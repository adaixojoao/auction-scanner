"""Batch 04: the note store and details cache, executed in Node (no DOM, no network).

They do not test browser focus or real persistence; tests/test_browser_listings.py
does. Without Node they skip, and say so.
"""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def run_js(code):
    if NODE is None:
        pytest.skip("Node is not installed; frontend state tests not executed")
    result = subprocess.run([NODE, "-e", code], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_note_acknowledgement_and_latest_draft():
    run_js(r'''
const assert = require("node:assert/strict");
require("./static/listing-notes.js");
(async () => {
  let complete;
  const calls = [];
  const notes = AuctionNotes.createStore({delay: 100000, persist: async (id, text) => {
    calls.push(text);
    if (calls.length === 1) await new Promise(resolve => { complete = resolve; });
    return {ok: true, id, length: text.length};
  }});
  notes.seed("a", "original");
  notes.edit("a", "first");
  const saving = notes.flush("a");
  await Promise.resolve();
  notes.edit("a", "second");
  complete();
  assert.equal(await saving, true);
  assert.deepEqual(calls, ["first", "second"]);
  assert.equal(notes.state("a").saved, "second");
  assert.equal(notes.hasUnsaved(), false);
  notes.dispose();
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_failed_acknowledgement_preserves_draft_and_retry():
    run_js(r'''
const assert = require("node:assert/strict");
require("./static/listing-notes.js");
(async () => {
  let good = false;
  const notes = AuctionNotes.createStore({delay: 100000, persist: async (id, text) =>
    good ? {ok: true, id, length: text.length} : {ok: true, id: "wrong", length: text.length}
  });
  notes.seed("a", "old");
  notes.edit("a", "draft");
  assert.equal(await notes.flush("a"), false);
  assert.equal(notes.state("a").draft, "draft");
  assert.equal(notes.state("a").saved, "old");
  assert.equal(notes.state("a").phase, "error");
  notes.seed("a", "server refresh must not erase draft");
  assert.equal(notes.state("a").draft, "draft");
  good = true;
  notes.edit("a", "latest");
  assert.equal(await notes.retry("a"), true);
  assert.equal(notes.state("a").saved, "latest");
  notes.dispose();
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_flush_all_is_one_answer_and_a_failure_never_retries_by_itself():
    run_js(r'''
const assert = require("node:assert/strict");
require("./static/listing-notes.js");
(async () => {
  const calls = [];
  const notes = AuctionNotes.createStore({delay: 0, persist: async (id, text) => {
    calls.push(id);
    if (id === "bad") throw new Error("offline");
    return {ok: true, id, length: text.length};
  }});
  notes.edit("good", "x");
  notes.edit("bad", "y");
  assert.equal(await notes.flushAll(), false);
  assert.equal(notes.state("good").phase, "saved");
  assert.equal(notes.state("bad").phase, "error");
  const before = calls.length;
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(calls.length, before);                  // no silent retry loop
  assert.equal(notes.hasUnsaved(), true);
  notes.dispose();
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_detail_token_reuse_and_inflight_invalidation():
    run_js(r'''
const assert = require("node:assert/strict");
require("./static/listing-details-cache.js");
(async () => {
  let calls = 0;
  let resolvePending;
  const cache = AuctionDetailsCache.createCache({read: async (mode, id) => {
    calls += 1;
    if (calls === 3) return new Promise(resolve => { resolvePending = resolve; });
    return {id, facts: [], revision: calls};
  }});
  assert.equal((await cache.load("home", "a", "one")).cached, false);
  assert.equal((await cache.load("home", "a", "one")).cached, true);
  assert.equal(calls, 1);
  await cache.load("home", "a", "two");
  assert.equal(calls, 2);
  const pending = cache.load("home", "a", "three");
  cache.invalidate("a");
  resolvePending({id: "a", facts: []});
  assert.equal((await pending).kind, "obsolete");
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_invalidated_details_stay_shown_as_earlier_but_never_as_current():
    run_js(r'''
const assert = require("node:assert/strict");
require("./static/listing-details-cache.js");
(async () => {
  let t = 0;
  const cache = AuctionDetailsCache.createCache({maxEntries: 2, ttl: 100, now: () => t,
    read: async (mode, id) => ({id, facts: []})});
  await cache.load("home", "a", "tok");
  assert.ok(cache.cached("home", "a", "tok"));
  assert.equal(cache.cached("home", "a", "other"), null);     // a changed token is not current
  cache.invalidate();                                         // a scan finished
  assert.equal(cache.cached("home", "a", "tok"), null);
  assert.ok(cache.earlier("home", "a"));
  await cache.load("home", "a", "tok");
  t = 100;
  assert.equal(cache.cached("home", "a", "tok"), null);       // too old
  await cache.load("home", "b", "tok");
  await cache.load("home", "c", "tok");
  assert.equal(cache.earlier("home", "a"), null);             // bounded: the oldest goes
  const untokened = await cache.load("home", "d", "");
  assert.equal(untokened.kind, "success");
  assert.equal(cache.earlier("home", "d"), null);             // no token, nothing kept
})().catch(error => { console.error(error); process.exitCode = 1; });
''')
