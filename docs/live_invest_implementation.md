# Live and Invest — Contract-completion review

Audited 2026-10-15. Each F-number corresponds to the original audit finding.
Status: **implemented and verified** | **partial** | **not implemented** | **blocked**

---

## F01 — Pure evaluator / shadow mode (Milestone A)

| Field | Detail |
|---|---|
| Requirement | `evaluate(evidence, policy, now) → Decision` with 14 requirement checks; pure, no I/O |
| Implementation | `evaluator.py` — `evaluate()`, `Requirement`, `Decision` dataclasses |
| Tests | `tests/test_evaluator.py` |
| Status | **partial** |
| Gap | Shadow mode: `evaluate()` is never called from `db.load_listings()`, `dashboard.py`, or any publication path. It exists and is tested in isolation but has zero production wiring. Decision-time historical records and benchmark tooling: **not implemented**. |

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
| Implementation | `scoring.py` — `item["extreme_discount"] = True` set during invest scoring; `db.py` — `extreme_discount` in `_DERIVED` (persisted in score cache); `load_best()` collects flagged IDs across all mode passes and post-filters before returning finalist list |
| Tests | `tests/test_extreme_discount_gallery.py` — 5 tests incl. `test_extreme_discount_visible_in_listing_blocked_from_load_best`; `tests/test_modes.py::test_investment_does_not_believe_an_impossible_discount` |
| Status | **partial** |
| Gap | (1) The flag is stored in the per-mode score cache. All real `load_best` callers use `modes=None` or `EARNING_MODES` (includes "invest"), so invest-mode always runs in the same `load_best` call. The post-filter fires before the result is returned. A caller restricted to `modes=("home",)` would miss the flag — no such caller exists today. (2) **No investigation clearance record exists**: once flagged, an item can never reach `load_best` except after the score cache expires (cache key changes if the item's columns change). A dedicated clearance mechanism (investigation record, separate DB field) is needed for the "reviewed and promoted" path. |

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
| Implementation | `bidcap.py` — `_value()` returns raw `mv` (no condition factor); note says "After-work value — renovation is deducted separately" |
| Tests | `tests/test_valuation_cash.py` — 3 tests for F07 |
| Status | **partial** |
| Gap | The note says "after-work value" but `mv = area × local_price_per_m2` is a municipal median, not a surveyed value. There is no explicit "as-is" basis separate from "after-work". `market_value_confidence == "unknown"` does not block the bid cap or financial promotion — the cap is computed regardless. The "basis" field exists but `bidcap.py` has no code path to return `basis = None` when confidence is unknown; it only adds to `unknowns[]`. |

---

## F08 — Cash scenario (Milestone H)

| Field | Detail |
|---|---|
| Requirement | `bid + obligations + fees + required_works + risk_reserve` fits `available_cash`; bid ceiling, shortlist, detail view and offer checks use the same scenario |
| Implementation | `bidcap.py` — `all_in_low/high` now include `risk_reserve`; `costs.py` — `over_cash()` calls `money_out()` |
| Tests | `tests/test_valuation_cash.py` — 4 tests for F08 |
| Status | **partial** |
| Gap | `money_out()` (used by `over_cash()` for listing filter) computes `all_in["low"]` from `estimate()`, which sums `price + fees + renovation["low"]` — it does **not** include `risk_reserve`. The bidcap display shows `all_in_low = bid + fees + reno + reserve`, but the listing-filter gate (`over_cash`) does not include reserve. A property that is just within the visible cash threshold without reserve counted would be hidden by bidcap's all_in but visible in the listing filter. The two checks use different cash definitions. |

---

## F09 — Photo evidence quality gate

| Field | Detail |
|---|---|
| Requirement | `shows_house=True` insufficient alone; verify uncertainty, independent building matching, useful imagery and refresh behaviour |
| Implementation | `photos.py` — `looked_at()` checks `shows_house is not False`; `needs_photo_check()` checks gallery hash |
| Tests | `tests/test_extreme_discount_gallery.py` — 5 tests for F10 (gallery hash) |
| Status | **partial** |
| Gap | `looked_at()` treats any non-False value of `shows_house` as a satisfied check, including `None`. `shows_house=True` with confidence="low" passes the gate. No independent building-matching logic exists. The listing GET path does not write hazard evidence (F03 in the reviewer's phrasing — confirmed: `looked_at()` is checked in `common.light_checks_from()` before hazard scoring, not interleaved). |

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
| Implementation | `db.py` — `evidence_revisions` table, `record_evidence_revision()`, content-hash-based revision counter |
| Tests | No dedicated test file for F11; revision recording is called from `load_listings()` climate writes |
| Status | **partial** |
| Gap | Revision records are stored but the score cache key (`sha1(repr(scored_cols, status, offer_outcome, context, unconfirmed_since))`) does not include `evidence_revisions` state. A location or hazard revision does not automatically invalidate the cached score unless a scored column also changed. No freshness expiry logic exists that consults `evidence_revisions` to expire decisions. |

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
| Implementation | `db.py` — `scope TEXT`, `pages_seen INTEGER` in `scrape_log`; `log_scrape()` accepts `scope`; `last_ok_by_source()` excludes `scope = 'partial'` |
| Tests | `tests/test_db.py` (pre-existing scope tests) |
| Status | **partial** |
| Gap | No adapter in `adapters/` was found to pass `scope=` to `log_scrape()`. The schema and `last_ok_by_source()` logic are in place but no adapter writes a scope value — all runs record `scope = NULL`, which is treated as "full". The feature is inert. |

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
| Implementation | None |
| Tests | None |
| Status | **not implemented** |
| Gap | Each consumer (`dashboard.py`, `report.py`, `telegram_*.py`, AI export) calls `db.load_listings()` or `db.load_best()` independently. There is no central snapshot serialised after enrichment. A listing can appear in different states to different consumers if enrichment runs between calls. |

---

## F16 — Evidence revision propagation

See F11 above.

---

## Summary matrix

| Req | Description | Status |
|---|---|---|
| F01 | Pure evaluator / shadow mode | partial — wired to nothing |
| F02 | Identity map, stable keys | **implemented and verified** |
| F03 | Hazard coverage cap | **implemented and verified** |
| F04 | Location capability tiers | **implemented and verified** |
| F05 | Extreme-discount finalist gate | **partial — critical gap** |
| F06 | Duplicate relations | **implemented and verified** |
| F07 | Valuation basis | partial — no as-is basis, unknown doesn't block |
| F08 | Cash scenario (reserve in filter) | partial — `over_cash` excludes reserve |
| F09 | Photo evidence quality gate | partial — `shows_house` single-boolean |
| F10 | Gallery URL hash requeue | **implemented and verified** |
| F11 | Evidence revision tracking | partial — revisions not in score cache key |
| F12 | Typed availability states | **implemented and verified** |
| F13 | Scan scope tracking | partial — no adapter writes scope value |
| F14 | Sale state disambiguation | **implemented and verified** |
| F15 | Publication snapshot | **not implemented** |
| F16 | (see F11) | partial |

**Highest-risk unfinished prerequisite: F05 — extreme-discount finalist gate.**
An `extreme_discount=True` item can appear in the shortlist, report, and Telegram
alert at the same rank as a verified one. The flag is set during scoring but not
persisted and not read by `load_best()` or the listing filter. Fix: persist the
flag into `raw_json` during scoring, then gate finalist promotion in
`load_listings()`.
