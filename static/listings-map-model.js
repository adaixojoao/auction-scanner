// listings-map-model.js — the Listings map: which properties on this results page have a usable stored pin.
// It validates coordinates and builds a display model; it fetches, geocodes and
// classifies nothing (the pin's precision and label come from the server's
// geo.map_summary). Runs in Node too (tests/test_batch10_map_model.py).
(function (root) {
  "use strict";

  function coordinates(map) {
    if (!map || typeof map.lat !== "number" || typeof map.lon !== "number") return null;
    if (!Number.isFinite(map.lat) || !Number.isFinite(map.lon)) return null;
    if (map.lat < -90 || map.lat > 90 || map.lon < -180 || map.lon > 180) return null;
    return [map.lat, map.lon];
  }

  function pageModel(items) {
    const located = [];
    const unlocated = [];
    const seen = new Set();
    items.forEach(item => {
      if (!item || typeof item.id !== "string" || !item.id || seen.has(item.id)) return;
      seen.add(item.id);
      const map = item.map;
      const point = coordinates(map);
      const summary = {
        id: item.id,
        title: typeof item.title === "string" && item.title ? item.title : "Untitled listing"
      };
      if (!point) {
        unlocated.push({...summary, label: "Location not established"});
      } else {
        located.push({
          ...summary, lat: point[0], lon: point[1],
          precision: typeof map.precision === "string" ? map.precision : "unknown",
          label: typeof map.label === "string" && map.label ? map.label : "Pin precision not established",
          source_label: typeof map.source_label === "string" ? map.source_label : ""
        });
      }
    });
    return {located, unlocated, total: seen.size};
  }

  root.AuctionMapModel = {coordinates, pageModel};
})(typeof window !== "undefined" ? window : globalThis);
