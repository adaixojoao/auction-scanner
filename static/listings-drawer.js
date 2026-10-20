// listings-drawer.js — the Listings page: one property drawer for the table and the research cards.
// Plain scripts sharing one global scope, loaded in order by templates/listings.html
// (listings-saved-searches.js, listings-view.js, listings-summary.js, listings-details.js,
//  listings-cards.js, listings-notes.js, listings-drawer.js, listings.js).
//
// A native modal <dialog>: the page behind it is inert until it closes. It
// owns no data: fillPanel and the one details cache (listings-details.js)
// draw and refresh its details, and the one note store (listings-notes.js)
// keeps drafts, so closing it or switching listing never loses a note.
// The listing shown is in the address as ?property=, replaced rather than
// pushed, so inspecting listings adds no history entries.
const propertyDrawer = (() => {
  const panel = $("property-drawer");
  const heading = $("property-drawer-title");
  const notice = $("property-drawer-notice");
  const star = $("property-drawer-star");
  const dismiss = $("property-drawer-dismiss");
  const viewer = $("property-image-viewer");
  const largeImage = $("property-image");
  const imageError = $("property-image-error");
  const body = panel.querySelector(".details-body");
  const sections = $("property-sections");
  let selected = null;
  let returnFocus = null;
  let imageTrigger = null;
  let observed = {mode: "", token: ""};
  let staleMessage = "";

  function writeSelection(id) {
    const url = new URL(location.href);
    if (id) url.searchParams.set("property", id); else url.searchParams.delete("property");
    if (url.href !== location.href) history.replaceState(history.state, "", url);
  }

  // Every Details trigger says whether the drawer shows its listing.
  function syncTriggers() {
    document.querySelectorAll('#rows [data-role="details"], #rows [data-role="title"], #listing-cards [data-role="details"], #research-candidates [data-role="details"]')
      .forEach(trigger => {
        const active = panel.open && trigger.dataset.id === selected;
        trigger.setAttribute("aria-expanded", String(active));
        if (active) trigger.setAttribute("aria-controls", panel.id); else trigger.removeAttribute("aria-controls");
      });
  }

  // The footer acts only on a listing in the results now shown: one outside
  // them has no status this page knows, so its buttons wait.
  function syncActions() {
    if (!selected) return;
    const item = shownItems.get(selected);
    star.dataset.id = dismiss.dataset.id = selected;
    const pending = statusPending.has(selected);
    const starred = !!item && item.status === "shortlisted";
    const dismissed = !!item && item.status === "dismissed";
    star.textContent = dismissed ? "Restore" : starred ? "Remove from shortlist" : "Shortlist";
    star.dataset.role = dismissed ? "restore" : "star";
    if (dismissed) star.removeAttribute("aria-pressed"); else star.setAttribute("aria-pressed", String(starred));
    star.disabled = !item || pending;
    dismiss.hidden = dismissed;
    dismiss.disabled = !item || pending;
    const lines = [];
    if (!item) lines.push("This listing is not in the results shown now, so it cannot be shortlisted or dismissed from here. Its details and your note are still here.");
    if (staleMessage) lines.push(staleMessage);
    notice.hidden = !lines.length;
    notice.textContent = lines.join(" ");
  }

  // Section links rebuilt from what was drawn: buttons, not #anchors, so the address stays as it is.
  function syncSections() {
    sections.replaceChildren(...[...body.querySelectorAll("section.d-section")].map(section => {
      const title = section.querySelector(".d-section-title");
      const link = document.createElement("button");
      link.type = "button";
      link.className = "btn btn-sm";
      link.textContent = title.textContent;
      link.addEventListener("click", () => {
        section.scrollIntoView({block: "start"});
        title.focus({preventScroll: true});
      });
      return link;
    }));
    sections.hidden = !sections.childElementCount;
  }
  new MutationObserver(syncSections).observe(body, {childList: true});

  // While a photograph is enlarged, the rest of the drawer is inert, so Tab stays on the viewer.
  const behindViewer = () => [...panel.children].filter(el => el !== viewer);
  function hideImage({restoreFocus = true} = {}) {
    if (viewer.hidden) return;
    viewer.hidden = true;
    behindViewer().forEach(el => { el.inert = false; });
    largeImage.removeAttribute("src");
    imageError.hidden = true;
    if (restoreFocus && imageTrigger && imageTrigger.isConnected) imageTrigger.focus({preventScroll: true});
    imageTrigger = null;
  }
  function showImage(trigger) {
    const image = trigger.querySelector("img");
    let url;
    try { url = new URL(image && (image.currentSrc || image.getAttribute("src")), location.href); } catch (error) { return; }
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return;
    imageTrigger = trigger;
    imageError.hidden = true;
    largeImage.hidden = false;
    largeImage.alt = `${image.alt || "Listing photograph"}, enlarged: ${heading.textContent}`;
    largeImage.src = url.href;
    behindViewer().forEach(el => { el.inert = true; });
    viewer.hidden = false;
    $("property-image-close").focus();
  }
  largeImage.addEventListener("error", () => {
    if (viewer.hidden) return;
    largeImage.hidden = true;
    imageError.hidden = false;
  });

  async function open(id, trigger, {writeURL = true, fresh = false} = {}) {
    if (typeof id !== "string" || !id) return null;
    if (selected !== id) {
      const read = panelReads.get(panel);
      if (read) read.controller.abort();
      panelReads.delete(panel);
      hideImage({restoreFocus: false});
      body.replaceChildren();
      panelShown.delete(panel);
      staleMessage = "";
      panel.scrollTop = 0;
    }
    selected = id;
    panel.dataset.for = id;
    if (trigger) returnFocus = trigger;
    heading.textContent = (shownItems.get(id) || {}).title || "Property details";
    observed = {mode: view.mode, token: detailToken(id)};
    if (!panel.open) {
      panel.showModal();
      $("property-drawer-close").focus({preventScroll: true});
    }
    syncActions();
    syncTriggers();
    if (writeURL) writeSelection(id);
    const shown = await fillPanel(panel, id, {fresh});
    if (shown && selected === id && shown.title && !shownItems.has(id)) heading.textContent = shown.title;
    return shown;
  }

  // Tidies up however the dialog closed (the Close button, Escape, Back).
  function settle({writeURL = true} = {}) {
    if (!selected) return;
    const read = panelReads.get(panel);
    if (read) read.controller.abort();          // the details read only; note saves carry on
    panelReads.delete(panel);
    const previous = selected;
    selected = null;
    staleMessage = "";
    hideImage({restoreFocus: false});
    delete panel.dataset.for;
    syncTriggers();
    if (writeURL) writeSelection(null);
    // The trigger was redrawn (or the layout changed): the same listing's Details where it is shown now.
    const fallback = rowFor(previous) || listingCardFor(previous) || cardFor(previous);
    const target = returnFocus && returnFocus.isConnected ? returnFocus
      : fallback ? fallback.querySelector('[data-role="details"]') : null;
    (target || $("result-count")).focus({preventScroll: true});
    returnFocus = null;
  }
  function close(options = {}) {
    if (panel.open) panel.close();
    settle(options);
  }
  // The close event comes a task later: if the drawer was opened again meanwhile (Back), it stays.
  panel.addEventListener("close", () => { if (!panel.open) settle(); });

  // After the results are redrawn: the footer and triggers follow them, and
  // details no longer current (a scan, a change, another goal) are read again.
  // A note draft lives in the note store and survives that; a location you
  // are typing does not, so then it only says so.
  function reconcile() {
    if (!panel.open || !selected) return;
    const token = detailToken(selected);
    const changed = observed.mode !== view.mode || observed.token !== token
      || (!!token && !detailsCache.cached(view.mode, selected, token));
    if (changed) {
      const active = document.activeElement;
      const typing = panel.contains(active) && active.matches("input, select") && !active.matches("[type=checkbox]");
      if (typing) {
        staleMessage = "Newer details may be available; your typing is kept. Use Refresh details when ready.";
      } else {
        staleMessage = "";
        observed = {mode: view.mode, token};
        fillPanel(panel, selected, {fresh: true});
      }
    }
    syncActions();
    syncTriggers();
  }

  // A finished scan whose redraw waits for an unsaved note: the drawer says so too.
  function markStale() {
    if (!panel.open || !selected) return;
    staleMessage = "A scan finished: these details may be out of date. Use Refresh details when ready.";
    syncActions();
  }

  // The address decides: on start-up and on Back/Forward.
  function restoreURL() {
    const id = new URLSearchParams(location.search).get("property");
    if (id) {
      if (selected !== id || !panel.open) open(id, null, {writeURL: false});
    } else if (panel.open) close({writeURL: false});
  }

  $("property-drawer-close").addEventListener("click", () => close());
  panel.addEventListener("cancel", event => {
    // Escape closes an enlarged photograph first, then the drawer.
    if (!viewer.hidden) { event.preventDefault(); hideImage(); }
  });
  star.addEventListener("click", () => {
    const item = shownItems.get(selected);
    if (!item || statusPending.has(selected)) return;
    setStatus(selected, item.status === "dismissed" || item.status === "shortlisted" ? null : "shortlisted");
  });
  dismiss.addEventListener("click", async () => {
    if (!selected || !shownItems.has(selected) || statusPending.has(selected)) return;
    const id = selected;
    // The reason dialog opens over the drawer; Cancel or Escape there changes nothing.
    // Once dismissed, the drawer closes so Undo, below it, can be reached.
    if (await setStatus(id, "dismissed") && selected === id) close();
  });
  $("property-drawer-refresh").addEventListener("click", () => {
    if (!selected) return;
    staleMessage = "";
    observed = {mode: view.mode, token: detailToken(selected)};
    syncActions();
    fillPanel(panel, selected, {fresh: true});
  });
  $("property-image-close").addEventListener("click", () => hideImage());
  panel.addEventListener("click", event => {
    const trigger = event.target.closest && event.target.closest('[data-role="enlarge-photo"]');
    if (trigger && body.contains(trigger)) showImage(trigger);
  });

  return {open, close, reconcile, markStale, restoreURL, panel, selected: () => selected};
})();
