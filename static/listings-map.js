// listings-map.js — the Listings page: a map of the located properties on this results page.
// Plain scripts sharing one global scope, loaded in order by templates/listings.html
// (listings-saved-searches.js, listings-view.js, listings-summary.js, listings-details.js,
//  listings-cards.js, listings-notes.js, listings-drawer.js, listings-comparison-store.js,
//  listings-comparison-render.js, listings-comparison.js, listings-map-model.js,
//  listings-map.js, listings.js).
//
// The map shows the page of results the server sent (the same items as the
// table or cards), never more: zero pins is not zero matches, and a property
// without a stored position stays in the results and in the list below the
// map. Pins are the stored ones (geo.map_summary): nothing is looked up here,
// and an approximate pin says so in words and is drawn dashed. Moving the map
// changes no filter or page. Leaflet and the OpenStreetMap tiles are the ones
// the Map page already uses, from the internet, and load only once the map is
// shown; without them the lists still hold every property.

const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const MAP_SHOWN_KEY = "auction-scanner:listings-map:v1";

// ── The one adapter around the map library (Leaflet) ──
function leafletAdapter(L) {
  let map = null;
  let tiles = null;
  let layer = null;
  let markers = new Map();        // listing id → its marker (properties at one spot share one)
  let selected = "";
  const style = (pin, on) => ({
    radius: on ? 11 : 8, weight: on ? 4 : 2, color: on ? "#facc15" : "#ffffff",
    fillColor: pin.precision === "exact" ? "#2563eb" : "#64748b", fillOpacity: 0.9,
    dashArray: pin.precision === "exact" ? null : "4 3"
  });
  return {
    mount(container, {onTileError} = {}) {
      map = L.map(container, {keyboard: true});
      tiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
                          {attribution: "© OpenStreetMap contributors", maxZoom: 19});
      tiles.on("tileerror", () => onTileError && onTileError());
      tiles.addTo(map);
      layer = L.layerGroup().addTo(map);
      map.setView([39.6, -8.0], 6);
    },
    // groups: [{key, pins: [pin…]}], one per spot; popup(group) builds its content when it opens.
    render(groups, popup) {
      layer.clearLayers();
      markers = new Map();
      groups.forEach(group => {
        const first = group.pins[0];
        const on = group.pins.some(p => p.id === selected);
        const marker = L.circleMarker([first.lat, first.lon], style(first, on));
        marker.bindPopup(() => popup(group));
        marker.bindTooltip(group.pins.length > 1 ? `${group.pins.length} properties at this spot` : first.title);
        marker.addTo(layer);
        marker._pins = group.pins;
        group.pins.forEach(p => markers.set(p.id, marker));
      });
    },
    select(id) {
      const previous = markers.get(selected);
      selected = id || "";
      if (previous) previous.setStyle(style(previous._pins[0], false));
      const marker = markers.get(selected);
      if (marker) { marker.setStyle(style(marker._pins[0], true)); marker.bringToFront(); }
    },
    reveal(id) {
      const marker = markers.get(id);
      if (!marker) return false;
      map.setView(marker.getLatLng(), Math.max(map.getZoom(), 12));
      marker.openPopup();
      return true;
    },
    fit(points) {
      if (!points.length) return false;
      map.fitBounds(L.latLngBounds(points), {padding: [24, 24], maxZoom: 14});
      return true;
    },
    resize() { if (map) map.invalidateSize(); },
    dispose() { if (map) map.remove(); map = tiles = layer = null; markers = new Map(); }
  };
}

const listingsMap = (() => {
  const section = $("listings-map-section");
  const toggle = $("show-map");
  const status = $("listings-map-status");
  let shown = false;
  try { shown = localStorage.getItem(MAP_SHOWN_KEY) === "shown"; } catch (e) { /* private window */ }
  let model = {located: [], unlocated: [], total: 0};
  let previous = false;           // the rows are a failed refresh's previous view
  let adapter = null;
  let library = null;             // the promise of Leaflet, once asked for
  let unavailable = false;
  let fitted = false;
  let tileWarning = false;
  let matched = null;

  function loadLeaflet() {
    if (library) return library;
    library = new Promise((resolve, reject) => {
      if (typeof L !== "undefined") { resolve(L); return; }
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = LEAFLET_CSS;
      document.head.appendChild(css);
      const script = document.createElement("script");
      script.src = LEAFLET_JS;
      script.onload = () => (typeof L !== "undefined" ? resolve(L) : reject(new Error("no Leaflet")));
      script.onerror = () => reject(new Error("Leaflet could not be loaded"));
      document.head.appendChild(script);
    });
    return library;
  }

  function groups() {
    const byspot = new Map();
    model.located.forEach(pin => {
      const key = `${pin.lat.toFixed(6)},${pin.lon.toFixed(6)}`;
      if (!byspot.has(key)) byspot.set(key, {key, pins: []});
      byspot.get(key).pins.push(pin);
    });
    return [...byspot.values()];
  }

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }

  function actions(pin, {locate = false} = {}) {
    const box = el("div", null, "map-actions");
    const details = el("button", "Details", "btn btn-sm");
    details.type = "button";
    details.dataset.role = "map-details";
    details.dataset.id = pin.id;
    details.setAttribute("aria-label", `Details: ${pin.title}`);
    box.appendChild(details);
    if (locate && adapter) {
      const show = el("button", "Show on map", "btn btn-sm");
      show.type = "button";
      show.dataset.role = "map-locate";
      show.dataset.id = pin.id;
      show.setAttribute("aria-label", `Show on map: ${pin.title}`);
      box.appendChild(show);
    }
    const holder = document.createElement("template");
    holder.innerHTML = compareButtonHtml(pin.id, pin.title);   // escapes the title (AS.esc)
    box.appendChild(holder.content);
    return box;
  }

  function entry(pin, located) {
    const li = el("li");
    li.dataset.id = pin.id;
    li.appendChild(el("span", pin.title, "map-entry-title"));
    li.appendChild(el("span", ` — ${pin.label}`, "small muted"));
    if (located && pin.source_label) li.appendChild(el("span", ` (from ${pin.source_label})`, "small muted"));
    li.appendChild(actions(pin, {locate: located}));
    return li;
  }

  function popup(group) {
    const box = el("div", null, "map-popup");
    if (group.pins.length > 1) box.appendChild(el("p", `${group.pins.length} properties at this spot:`, "small"));
    group.pins.forEach(pin => {
      const item = el("div", null, "map-popup-item");
      item.appendChild(el("strong", pin.title));
      item.appendChild(el("p", pin.label, "small"));
      if (pin.source_label) item.appendChild(el("p", `From ${pin.source_label}`, "small"));
      item.appendChild(actions(pin));
      box.appendChild(item);
    });
    return box;
  }

  function describe() {
    const parts = [];
    if (previous) parts.push("Previous results: the map and lists show your previous view, not the filters now selected.");
    if (unavailable) parts.push("The map could not be loaded (it needs the map library from unpkg.com and tiles from OpenStreetMap). The lists below still hold every property on this page.");
    else if (!adapter) parts.push("Loading the map…");
    if (tileWarning) parts.push("Some map tiles could not be loaded; the pins and lists are unaffected.");
    const of = matched != null && matched > model.total ? ` ${matched.toLocaleString("de-DE")} listings match in all; other pages are not on this map.` : "";
    parts.push(`${model.located.length} of ${model.total} properties on this page have a stored position; ${model.unlocated.length} have none.${of}`);
    status.textContent = parts.join(" ");
  }

  function drawLists() {
    $("listings-map-located").replaceChildren(...model.located.map(pin => entry(pin, true)));
    $("listings-map-unlocated").replaceChildren(...model.unlocated.map(pin => entry(pin, false)));
    $("listings-map-located-summary").textContent = `Located properties and pin precision (${model.located.length})`;
    $("listings-map-unlocated-summary").textContent = `Properties without established coordinates (${model.unlocated.length})`;
    if (typeof comparison !== "undefined") comparison.syncButtons();
  }

  function draw() {
    if (!shown) return;
    drawLists();
    if (adapter) {
      adapter.render(groups(), popup);
      if (typeof propertyDrawer !== "undefined" && propertyDrawer.panel.open) adapter.select(propertyDrawer.panel.dataset.for);
      // The first view of the map fits the page; later refreshes keep where you looked.
      if (!fitted) fitted = adapter.fit(model.located.map(p => [p.lat, p.lon]));
    }
    describe();
  }

  async function activate() {
    describe();
    try {
      const lib = await loadLeaflet();
      if (!shown || adapter) { draw(); return; }
      const candidate = leafletAdapter(lib);
      candidate.mount($("listings-map"), {onTileError: () => { if (!tileWarning) { tileWarning = true; describe(); } }});
      adapter = candidate;
      unavailable = false;
    } catch (error) {
      unavailable = true;
    }
    draw();
  }

  function setShown(next, {announce = true} = {}) {
    shown = next;
    try { localStorage.setItem(MAP_SHOWN_KEY, next ? "shown" : "hidden"); } catch (e) { /* not kept */ }
    section.hidden = !next;
    toggle.setAttribute("aria-pressed", String(next));
    toggle.textContent = next ? "Hide map" : "Show map";
    if (next) {
      if (adapter) { adapter.resize(); draw(); }
      else activate();
    }
    if (announce) AS.announce(next ? "Map shown below the results." : "Map hidden.");
  }

  // After the latest successful results: the same page, nothing more.
  function update(items, matchedTotal) {
    model = AuctionMapModel.pageModel(Array.isArray(items) ? items : []);
    matched = Number.isFinite(Number(matchedTotal)) ? Number(matchedTotal) : null;
    previous = false;
    draw();
  }

  function markPrevious() {
    previous = true;
    if (shown) describe();
  }

  function highlight(id) { if (adapter) adapter.select(id); }

  toggle.addEventListener("click", () => setShown(!shown));
  $("listings-map-fit").addEventListener("click", () => {
    if (adapter && adapter.fit(model.located.map(p => [p.lat, p.lon]))) fitted = true;
  });
  document.addEventListener("click", e => {
    const button = e.target.closest && e.target.closest('[data-role="map-details"], [data-role="map-locate"]');
    if (!button || !button.dataset.id) return;
    if (button.dataset.role === "map-details") propertyDrawer.open(button.dataset.id, button);
    else if (adapter) adapter.reveal(button.dataset.id);
  });
  setShown(shown, {announce: false});

  return {update, markPrevious, highlight, model: () => model, adapter: () => adapter};
})();
