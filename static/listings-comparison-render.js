// listings-comparison-render.js — the Listings page: the comparison table, from display strings only.
// It calculates nothing (no cost, score, verification or time): listings-comparison.js
// turns each listing's own summaries into the strings shown here. Every value is
// text (textContent), and nothing is coloured or ranked as a winner.
(function (root) {
  "use strict";
  const ROWS = [
    ["location", "Location / pin confidence"],
    ["kind", "Property kind"],
    ["goal_score", "Goal and opportunity score"],
    ["price", "Price basis"],
    ["all_in", "Estimated all-in"],
    ["cost_basis", "Cost basis and missing costs"],
    ["area", "Floor / plot area"],
    ["valuation", "Valuation / benchmark basis"],
    ["checks", "Ownership, possession, and legal access"],
    ["evidence", "Unresolved evidence"],
    ["next_action", "Next investigation action"],
    ["climate", "Climate / hazard uncertainty"],
    ["deadline", "Deadline and precision"],
    ["reasons", "Existing opportunity reasons"]
  ];

  function node(tag, text, className) {
    const result = document.createElement(tag);
    if (text != null) result.textContent = String(text);
    if (className) result.className = className;
    return result;
  }

  function button(text, role, model, label) {
    const control = node("button", text, "btn btn-sm");
    control.type = "button";
    control.dataset.id = model.id;
    control.dataset.role = role;
    control.setAttribute("aria-label", `${label}: ${model.title || "property"}`);
    return control;
  }

  function render(container, models, actions) {
    const wrapper = node("div", null, "comparison-table-scroll");
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "region");
    wrapper.setAttribute("aria-label", "Property comparison table");
    const table = node("table", null, "comparison-table");
    table.appendChild(node("caption", "Research comparison — estimates and evidence bases may differ."));
    const head = node("thead");
    const headings = node("tr");
    const label = node("th", "Criterion");
    label.scope = "col";
    headings.appendChild(label);
    models.forEach(model => {
      const cell = node("th");
      cell.scope = "col";
      cell.appendChild(node("div", model.title || "Untitled listing"));
      if (model.scope_note) cell.appendChild(node("p", model.scope_note, "small muted"));
      const open = button("Open details", "compare-open", model, "Open details");
      open.addEventListener("click", () => actions.open(model.id, open));
      cell.appendChild(open);
      const refresh = button("Refresh", "compare-refresh", model, "Refresh comparison");
      refresh.disabled = model.state === "loading";
      refresh.addEventListener("click", () => actions.refresh(model.id));
      cell.appendChild(refresh);
      cell.appendChild(button("Remove", "compare-remove", model, "Remove from comparison"));   // listings-comparison.js handles it
      headings.appendChild(cell);
    });
    head.appendChild(headings);
    table.appendChild(head);
    const body = node("tbody");
    ROWS.forEach(([key, title]) => {
      const row = node("tr");
      const header = node("th", title);
      header.scope = "row";
      row.appendChild(header);
      models.forEach(model => {
        let text;
        if (model.state === "loading") text = "Loading…";
        else if (model.state === "error") text = "Unavailable — refresh to retry";
        else text = model.values && model.values[key];
        row.appendChild(node("td", typeof text === "string" && text ? text : "Not established"));
      });
      body.appendChild(row);
    });
    table.appendChild(body);
    wrapper.appendChild(table);
    container.replaceChildren(wrapper);
  }

  root.AuctionComparisonRender = {ROWS, render};
})(typeof window !== "undefined" ? window : globalThis);
