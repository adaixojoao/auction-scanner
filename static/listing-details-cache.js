// listing-details-cache.js — details panels' server data, per goal and listing.
// No DOM: listings-details.js draws what it returns. Loaded before the other
// Listings scripts; Node tests load it directly (tests/test_batch04_frontend_state.py).
// Details count as current only for the detail_token they were read under and
// for `ttl` ms; a token is an "is this still the same listing?" hint, not proof
// that legal or financial evidence is current. Note drafts are never cached here.
(function (root) {
  "use strict";

  function createCache({read, valid = (data, id) => !!data && data.id === id && Array.isArray(data.facts),
                        maxEntries = 40, ttl = 60000, now = Date.now}) {
    const entries = new Map();        // key → {data, token, at, stale}, oldest first
    const generations = new Map();    // key → revision, so invalidation also beats reads in flight
    let epoch = 0;

    const key = (mode, id) => JSON.stringify([mode, id]);
    const revision = k => generations.get(k) || 0;
    function touch(k, entry) {
      entries.delete(k);
      entries.set(k, entry);
      while (entries.size > maxEntries) entries.delete(entries.keys().next().value);
    }

    // Details still current for this token, or null. Synchronous, so a panel
    // can be drawn in the same tick as the rows around it.
    function cached(mode, id, token) {
      const k = key(mode, id), entry = entries.get(k);
      if (!entry || entry.stale || !token || entry.token !== token || now() - entry.at >= ttl) return null;
      touch(k, entry);
      return entry.data;
    }

    // The last details read, current or not, to show as "loaded earlier".
    function earlier(mode, id) {
      const entry = entries.get(key(mode, id));
      return entry ? entry.data : null;
    }

    // One listing (id) or everything (no id): never reused as current again,
    // and a read already in flight is answered as obsolete.
    function invalidate(id) {
      if (id == null) epoch += 1;
      for (const k of new Set([...entries.keys(), ...generations.keys()])) {
        if (id != null && JSON.parse(k)[1] !== id) continue;
        if (id != null) generations.set(k, revision(k) + 1);
        const entry = entries.get(k);
        if (entry) entry.stale = true;
      }
    }

    async function load(mode, id, token, {fresh = false, signal} = {}) {
      const k = key(mode, id);
      if (!generations.has(k)) generations.set(k, 0);
      const expected = typeof token === "string" && token ? token : null;
      const hit = fresh ? null : cached(mode, id, expected);
      if (hit) return {kind: "success", data: hit, cached: true};
      const startEpoch = epoch, startRevision = revision(k);
      const superseded = () => epoch !== startEpoch || revision(k) !== startRevision;
      try {
        const data = await read(mode, id, signal);
        if (superseded()) return {kind: "obsolete"};
        if (!valid(data, id)) return {kind: "error", message: "The server returned an unreadable response."};
        // Without a token nothing is kept as reusable.
        if (expected) touch(k, {data, token: expected, at: now(), stale: false});
        return {kind: "success", data, cached: false};
      } catch (error) {
        if ((error && error.name === "AbortError") || superseded()) return {kind: "obsolete"};
        return {kind: "error", message: (error && error.message) || "Could not refresh details."};
      }
    }

    return {load, cached, earlier, invalidate};
  }

  async function read(mode, id, signal) {
    const params = new URLSearchParams({id, mode});
    const response = await fetch("/api/listing?" + params.toString(), {signal});
    const data = await response.json().catch(() => null);
    if (!response.ok) throw new Error(data && typeof data.error === "string" ? data.error : `Request failed (${response.status})`);
    return data;
  }

  root.AuctionDetailsCache = {createCache, read};
})(typeof window !== "undefined" ? window : globalThis);
