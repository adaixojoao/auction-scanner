"""Batch 05: two reads of one listing's details that finish in reverse order
(two panels of the same listing, e.g. its table row and its research card).
The older answer must not replace the newer one in the cache. Executed in
Node; skipped, and says so, without it."""
from tests.test_batch04_frontend_state import run_js


def test_an_older_details_read_finishing_last_never_replaces_the_newer():
    run_js(r'''
const assert = require("node:assert/strict");
require("./static/listing-details-cache.js");
(async () => {
  const pending = [];
  const cache = AuctionDetailsCache.createCache({read: () => new Promise(resolve => pending.push(resolve)),
                                                 valid: data => !!data && data.id === "a"});
  const older = cache.load("home", "a", "t", {fresh: true});
  const newer = cache.load("home", "a", "t", {fresh: true});
  pending[1]({id: "a", version: "new"});
  assert.equal((await newer).data.version, "new");
  pending[0]({id: "a", version: "old"});
  const late = await older;
  // The late caller is told about the newest details, and the cache keeps them.
  assert.equal(late.kind, "success");
  assert.equal(late.data.version, "new");
  assert.equal(cache.cached("home", "a", "t").version, "new");
  assert.equal(cache.earlier("home", "a").version, "new");
  // In order, the later read still wins as usual.
  const third = cache.load("home", "a", "t", {fresh: true});
  pending[2]({id: "a", version: "newest"});
  assert.equal((await third).data.version, "newest");
  assert.equal(cache.cached("home", "a", "t").version, "newest");
})().catch(error => { console.error(error); process.exitCode = 1; });
''')
