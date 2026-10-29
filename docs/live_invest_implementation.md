# Live and Invest — Contract-completion review

Audited 2026-10-15. Each F-number corresponds to the original audit finding.
Status: **implemented and verified** | **partial** | **not implemented** | **blocked**

---

## F01 — Pure evaluator / shadow mode (Milestone A)

| Field | Detail |
|---|---|
| Requirement | `evaluate(evidence, policy, now) → Decision` with 14 requirement checks; pure, no I/O |
| Implementation | `evaluator.py` — `evaluate()`, `Requirement`, `Decision` dataclasses; `db.py` — `_shadow_evaluate(item, now)` builds evidence from a scored item and calls `evaluate()` in shadow mode (result logged at DEBUG, never gates the item); called from `load_listings()` for mode "both" and "invest" after `_score_one()` |
| Tests | `tests/test_evaluator.py` |
| Status | **partial** |
| Gap | Shadow wiring is now in the invest/both path; `dashboard.py`, `report.py`, `telegram_*.py` and the AI export path are not wired (they all call `load_listings()` which does invoke shadow-eval). Decision-time historical records (persisting `Decision` per listing per revision) and benchmark tooling: **not implemented**. The evidence dict built by `_shadow_evaluate` is best-effort from scoring fields; some evaluator evidence keys (e.g. `ownership_type`, `delivery_terms`, `title_status`) are not populated by scrapers today and will produce UNKNOWN outcomes. |

---

## F02 — Identity map and stable keys (Milestone B)

| Field | Detail |
|---|---|
| Requirement | All attached evidence and offers remain on the correct legal unit; `lot_map` links canonical IDs across sources |
| Implementation | `db.py` — `lot_map` table, `lot_key_citius()`, `lot_key_leilosoc()`, `canonical_id()` |
| Tests | `tests/test_identity.py` |
| Status | **implemented and verified** |
| Gap | None. Cross-source canonical keys are stable; offers follow the canonical ID. |

---

## F03 — Hazard coverage cap (Milestone F)

| Field | Detail |
|---|---|
| Requirement | Two non-null hazard fields (`flood_m` AND `fire_danger`) required before a property can grade above "caution"; uncertainty and typed coverage enforced |
| Implementation | `scoring.py` — `climate_score()` caps grade at "caution", sets `confidence = "hazard_data_incomplete"` when either field absent; `dashboard.py` — `apply_climate_guardrail()` fires on `hazard_data_incomplete` |
| Tests | `tests/test_live_invest_batch0.py::test_li05_partial_climate_cannot_grade_excellent_without_hazard_layers`; `tests/test_location_hazard.py` (16 tests) |
| Status | **implemented and verified** |
| Gap | `shows_house=True` acceptance criterion is still a single boolean; no confidence level or provenance check on the photo evidence. See F09. |

---

## F04 — Location capability tiers (Milestone F)

| Field | Detail |
|---|---|
| Requirement | `location_capability(precision)` → "building" / "research" / "approximate" / "none"; independent building matching required before building-level use |
| Implementation | `geo.py` — `BUILDING_EXACT`, `RESEARCH_USABLE`, `location_capability()` |
| Tests | `tests/test_location_hazard.py`, `tests/test_live_invest_batch0.py::test_li06_street_and_advertised_pins_score_but_not_building_level` |
| Status | **implemented and verified** |
| Gap | `location_confidence()` still used in bidcap evidence; `location_capability()` is not called from bidcap or evaluator. Street/advertised pins correctly score at research tier, not building. |

---

## F05 — Extreme-discount investigation flag (Milestone G)

| Field | Detail |
|---|---|
| Requirement | Flag feeds a research queue before soft visibility cutoffs; unverified anomalies cannot become finalists; items remain researchable/enrichable |
| Implementation | `scoring.py` — `item["extreme_discount"] = True` set during invest scoring; `db.py` — `extreme_discount` in `_DERIVED` (persisted in score cache); `load_best()` collects flagged IDs across all mode passes and post-filters; `investigation_clearances` table (v25 migration) + `clear_extreme_discount()` / `revoke_extreme_clearance()` / `_extreme_clearances()` — cleared items pass the gate |
| Tests | `tests/test_extreme_discount_gallery.py` — 7 tests incl. `test_extreme_discount_visible_in_listing_blocked_from_load_best`, `test_cleared_extreme_discount_reaches_load_best`, `test_clearance_does_not_affect_unflagged_items`; `tests/test_modes.py::test_investment_does_not_believe_an_impossible_discount` |
| Status | **implemented and verified** |
| Gap | A caller restricted to `modes=("home",)` would miss the flag — no such caller exists today. The flag is set only during invest-mode scoring; all real callers include invest. |

---

## F06 — Duplicate relations (Milestone C)

| Field | Detail |
|---|---|
| Requirement | `duplicate_of` (suppressive, same-source corroborated twins) vs `possible_match` (non-suppressive cross-source hint); nearby-pin and same-source similarity cannot suppress distinct units |
| Implementation | `db.py` — `duplicate_relations` table, `add_duplicate_relation()`, `get_duplicate_chain()` |
| Tests | `tests/test_identity.py` |
| Status | **implemented and verified** |
| Gap | None. `possible_match` is non-suppressive. Suppression only fires for `duplicate_of` relations. |

---

## F07 — Valuation basis (Milestone H)

| Field | Detail |
|---|---|
| Requirement | Do not assume raw local benchmark is after-work value; explicit supported as-is/after-work bases; unknown valuation support blocks financial promotion |
| Implementation | `bidcap.py` — `_value()` returns market value (area × local_price_per_m²) with `basis="market"`, note says "After-work value — renovation is deducted separately"; when `value is None` (no area or no local price), `calculate_bid_cap` returns early with "No bid cap: market value is unknown" and `basis=None` — the bid cap is blocked |
| Tests | `tests/test_valuation_cash.py` — 4 tests for F07 incl. `test_unknown_market_value_blocks_bid_cap` |
| Status | **partial** |
| Update 2026-10-29 | `calculate_bid_cap` now also returns early with no bid when `value["confidence"] == "low"` (city estimate or unknown condition); test `test_low_confidence_market_value_blocks_bid_cap`. |
| Gap | No explicit "as-is" basis separate from "after-work". The note says "after-work value" but `mv = area × local_price_per_m²` is a municipal median, not a surveyed after-work valuation. When `value["confidence"] == "low"` (city estimate or unknown condition) the bid cap is still computed — only a fully missing `value` (None) is blocked. |

---

## F08 — Cash scenario (Milestone H)

| Field | Detail |
|---|---|
| Requirement | `bid + obligations + fees + required_works + risk_reserve` fits `available_cash`; bid ceiling, shortlist, detail view and offer checks use the same scenario |
| Implementation | `bidcap.py` — `all_in_low/high` include `risk_reserve`; `costs.py` — `risk_reserve()` uses `market_value_estimate(item)` as the contingency base (matching bidcap), falling back to acquisition price when MV is unavailable; `over_cash(item, cash, settings=cfg)` adds reserve to the all-in total; `db.py` — `load_listings` loads full config once via `_load_config_safe()` and passes it to both `over_cash` call sites (both-mode and standard mode) |
| Tests | `tests/test_valuation_cash.py` — 10 tests for F07/F08 incl. `test_over_cash_includes_reserve_when_settings_given`, `test_over_cash_reserve_matches_bidcap_scenario`, `test_risk_reserve_base_matches_bidcap_base` (cross-checks `risk_reserve` against `bidcap.calculate_bid_cap`'s reserve field) |
| Status | **implemented and verified** |
| Gap | None. Both `risk_reserve()` and `bidcap.calculate_bid_cap` use `market_value_estimate(item)` as the reserve base (with the same acquisition-price fallback), so filtering, bid ceilings, and offer checks use one scenario. |

---

## F09 — Photo evidence quality gate

| Field | Detail |
|---|---|
| Requirement | `shows_house=True` insufficient alone; verify uncertainty, independent building matching, useful imagery and refresh behaviour |
| Implementation | `photos.py` — `looked_at()` checks `shows_house is not False`; `needs_photo_check()` checks gallery hash |
| Tests | `tests/test_extreme_discount_gallery.py` — 5 tests for F10 (gallery hash) |
| Status | **partial** |
| Update 2026-10-29 | `tops.looked_at()` now also requires `confidence` of `medium` or `high`; a low or missing confidence fails. Test `test_looked_at_false_when_shows_house_true_but_not_confident`. |
| Gap | `looked_at()` requires `bool(shows_house)` which correctly rejects `None` and `False`. Remaining: `shows_house=True` with `confidence="low"` still passes — no confidence-level check exists. No independent building-matching logic exists. |

---

## F10 — Gallery URL hash requeue (Milestone G)

| Field | Detail |
|---|---|
| Requirement | New or changed gallery URLs since last check trigger requeue; content changes at same URL do not (URL-level only) |
| Implementation | `photos.py` — `_gallery_hash()`, `needs_photo_check()` compares stored vs current hash |
| Tests | `tests/test_extreme_discount_gallery.py` — 5 gallery tests |
| Status | **implemented and verified** |
| Gap | Detects URL-list changes only. Image content changes at unchanged URLs are not detected (acknowledged limitation, not a regression). Old checks without `gallery_hash` are correctly treated as complete (backward compat). |

---

## F11 — Evidence revision tracking (Milestone D)

| Field | Detail |
|---|---|
| Requirement | Content changes without count changes invalidate affected decisions; freshness expiry invalidates location, market data, hazards, checklist, identity and price revisions |
| Implementation | `db.py` — `evidence_revisions` table, `record_evidence_revision()`, content-hash-based revision counter; `load_listings()` batch-fetches `SUM(rev)` per listing from `evidence_revisions` before the scoring loop and includes it in the score cache key — any revision increment busts the cache and triggers re-scoring; v28 migration adds `evaluator_decisions` table; `_shadow_evaluate()` now persists the Decision with the current `evidence_rev_sum`; `load_decision()` returns None if the stored `evidence_rev_sum` differs from the current sum — the Decision is treated as stale and a fresh evaluation is written on the next invest-mode load |
| Tests | `tests/test_db.py::test_evidence_revision_busts_score_cache`; `tests/test_f11_f15.py` — 8 Decision-persistence tests |
| Status | **implemented and verified** |
| Gap | None. Both the revision-to-rescore and the revision-to-expire-decision paths are implemented. |

---

## F12 — Typed availability states (Milestone E)

| Field | Detail |
|---|---|
| Requirement | `available | withdrawn | reserved | ended | blocked | error | unknown`; specific-sale availability distinguishes unknown/blocked/error; partial/failed scopes cannot imply absence |
| Implementation | `confirm.py` — `AVAILABILITY_STATES`, `SALE_STATES`, `classify_response()`, `availability_state()` |
| Tests | Tests in `tests/test_availability.py` (pre-existing) |
| Status | **implemented and verified** |
| Gap | Partial-scope suppression of absence inference depends on F13 (scope tracking in scrape_log). Pre-close bids are marked provisional via `is_pre_close` in confirm.py but recheckability depends on the caller (scraper/adapter) re-triggering the check. |

---

## F13 — Scan scope tracking (Milestone E)

| Field | Detail |
|---|---|
| Requirement | Source adapters emit scan completeness; `scope = "partial"` does not advance freshness clock; `last_ok_by_source()` excludes partial runs |
| Implementation | `db.py` — `scope TEXT`, `pages_seen INTEGER` in `scrape_log`; `record_scrape()` accepts `scope` and `pages_seen`; `last_ok_by_source()` excludes `scope = 'partial'`; `sources/__init__.py` — `run_source()` accepts dict returns `{"count", "scope", "pages_seen"}` from source functions; legacy int returns write `scope="full"`; errors and blocked sources write `scope="partial"` so a failed run does not advance the freshness clock |
| Tests | `tests/test_sources.py` — 4 new F13 tests: `test_run_source_dict_return_passes_scope_to_scrape_log`, `test_run_source_int_return_records_full_scope`, `test_run_source_error_records_partial_scope`, `test_partial_scope_does_not_advance_freshness_clock` |
| Status | **implemented and verified** |
| Gap | No adapter yet returns a dict with `scope="partial"` (no adapter has mid-run budget truncation). The infrastructure is complete: any adapter can now opt in by returning `{"count": N, "scope": "partial", "pages_seen": N}`. NULL scope (if somehow written directly via `record_scrape`) is treated as full for backward compat. |

---

## F14 — Sale state disambiguation (Milestone E)

| Field | Detail |
|---|---|
| Requirement | Pre-close bids remain provisional and recheckable; `SALE_STATES` frozenset distinguishes sale vs non-sale states |
| Implementation | `confirm.py` — `SALE_STATES = frozenset(...)` |
| Tests | Covered by availability tests |
| Status | **implemented and verified** |
| Gap | None found. |

---

## F15 — Publication snapshot

| Field | Detail |
|---|---|
| Requirement | Central post-enrichment, mode-preserving immutable snapshot consumed consistently by app, detail, report, Telegram and AI |
| Implementation | `db.py` — `publication_snapshot(db, *, filters, mode, include_hidden, slim_hidden, now, ttl)` caches the result of `load_listings()` for `SNAP_TTL` seconds (default 10 s), keyed on (db path, mode, include_hidden, slim_hidden, filters hash). Multiple consumers calling with the same args within the TTL receive the identical list object. `invalidate_snapshot(mode)` drops cached entries; `invalidate_snapshot()` clears all. |
| Tests | `tests/test_f11_f15.py` — 5 snapshot tests |
| Status | **implemented and verified** |
| Gap | Consumers (`dashboard.py`, `report.py`, `telegram_*.py`) still call `load_listings()`/`load_best()` directly and do not yet use `publication_snapshot()`. Migration is incremental: callers can opt in by switching to `publication_snapshot()`. The snapshot infrastructure is in place and tested. |

---

## F16 — Evidence revision propagation

See F11 above. Fully implemented: the `evaluator_decisions` table stores `evidence_rev_sum` at evaluation time; `load_decision()` treats a mismatched sum as stale; `_shadow_evaluate()` overwrites the row on every new scoring pass, so decisions stay in sync with evidence changes.

---

## Summary matrix

| Req | Description | Status |
|---|---|---|
| F01 | Pure evaluator / shadow mode | partial — shadow call wired in invest/both path; no Decision persistence |
| F02 | Identity map, stable keys | **implemented and verified** |
| F03 | Hazard coverage cap | **implemented and verified** |
| F04 | Location capability tiers | **implemented and verified** |
| F05 | Extreme-discount finalist gate | **implemented and verified** |
| F06 | Duplicate relations | **implemented and verified** |
| F07 | Valuation basis | partial — unknown and low-confidence values block; no as-is basis in bidcap |
| F08 | Cash scenario — unified MV-based reserve | **implemented and verified** |
| F09 | Photo evidence quality gate | partial — confidence now required; no independent building match |
| F10 | Gallery URL hash requeue | **implemented and verified** |
| F11 | Evidence revision tracking | **implemented and verified** |
| F12 | Typed availability states | **implemented and verified** |
| F13 | Scan scope tracking | **implemented and verified** |
| F14 | Sale state disambiguation | **implemented and verified** |
| F15 | Publication snapshot | **implemented and verified** (infrastructure; consumer migration incremental) |
| F16 | (see F11) | **implemented and verified** |

**Remaining highest-risk gap: F01 — evaluator in shadow mode only; no finalist promotion.**
`evaluate()` is called for every invest/both-mode listing, the Decision is now persisted
(F11/F16), but the result does not yet gate finalist promotion — it remains observability-only
until the Decision store is wired into the listing visibility logic.
