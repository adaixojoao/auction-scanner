# Live and invest — Batch 0 baseline

Inspection and characterisation only. No production module, schema, tax rate, UI, or `AGENTS.md` change. No live scan, mail, or bid.

The earlier static audit named `34192bccc057100ffe625a7d7d13f83c5f7b497f`. That commit is now the `lean-start` merge on `master`, and its tree is the inspection commit `821ebcef`. The audit text itself is not in the checkout. Every finding below was checked against that tree. Historical comments in the code are labelled as such.

## 1. Environment

| Item | Value |
| --- | --- |
| Inspection commit | `821ebcef6ba740e8f672945067b1337796b83f4e` on `lean-start` (2026-10-14, "Describe the French parcel, the saved photos, and where those files live.") |
| `origin/master` when this file was landed | `34192bccc057100ffe625a7d7d13f83c5f7b497f` ("Merge pull request #105 from adaixojoao/lean-start"). That merge's tree matches `821ebcef` (`git diff 821ebcef origin/master` is empty). The findings below are the behaviour of that tree. |
| Where Batch 1 starts | Branch from `master` after this documentation pull request is merged. Do not branch Batch 1 from `lean-start` or from `batch0-live-invest-baseline`. |
| Working tree before this batch | Clean. Nothing was reset, stashed, or discarded. |
| OS | Windows 10.0.26200 |
| Python used for the suite | 3.13.14 (`C:\Users\adaix\AppData\Local\Programs\Python\Python313\python.exe`), pytest 9.1.1, pyflakes already installed |
| Default `python` on PATH | 3.13.13 (miniforge). `python -m pytest` there exits 1: `No module named pytest`. The global install was not modified. |
| Dependencies | `requirements.txt`, `requirements-dev.txt` (pytest, pyflakes), `requirements-climate.txt` (optional climate libraries). The suite ran with the existing 3.13 environment. |
| Tests are offline | `tests/conftest.py` refuses `requests`, `urlretrieve`, and SMTP, and points climate and price files at an empty temporary directory. The `db` fixture uses a temporary database. `db.DB_PATH` can also be overridden with `AUCTION_SCANNER_DB`. The production database was not opened. |

CI (`.github/workflows/tests.yml`): push and pull request; Ubuntu and Windows; Python 3.11 and 3.13; `pip install -r requirements-dev.txt`; on Linux/3.13 only, `python -m pyflakes *.py sources/ tests/ scripts/`; then `python -m pytest -q`.

Schema: `db.SCHEMA_VERSION` is 20. New changes add `_migrate_vN` and must use `_add_column`. This batch does not migrate.

`load_best()` still scores home, invest, land, and forest only. Live and invest (`both`) is a tab and does not compete (`db.py` `load_best`, around lines 1559–1566).

## 2. Checks actually run

| Command | Exit | Summary |
| --- | --- | --- |
| `python -m pytest -q` with miniforge 3.13.13 | 1 | `No module named pytest` |
| `Python313\python.exe -m pytest -q tests/test_live_invest_batch0.py` | 0 | 17 passed |
| `Python313\python.exe -m pyflakes` on the top-level modules, `sources`, `tests`, and `scripts` (PowerShell does not expand the CI glob) | 0 | no findings |
| Full `Python313\python.exe -m pytest -q` on `821ebcef` | 0 | `788 passed in 335.42s (0:05:35)` |
| Same command on this master-based branch | 0 | `788 passed in 309.77s (0:05:09)`. `pyflakes` exit 0. No assertion differed from the inspection commit. |

Not run: the GitHub matrix (no Ubuntu, no Python 3.11 in this session), climate-library reads, and any live source. A missing platform here is not a green CI run.

## 3. Findings

| ID | Status | Where | What happens now | Consequence | Fix batch |
| --- | --- | --- | --- | --- | --- |
| LI-01 | confirmed | `scoring._score_both` 1681–1686; `_investment_unpriced` 1629–1637 | No local price, or a province average that includes its cities, returns the living rank. `invest_score` is None. | A home with no valuation still ranks on Live and invest. | 11, after 9 |
| LI-02 | confirmed | `db.load_listings` 1499–1505; `dashboard.api_listings` 426 and 454 | Saved `min_score` in both mode compares `live_score`. The API then requires `score` (the combined display score) to clear the request threshold. | One threshold keeps a weak investment; another drops it. A shortlist skips the saved bar. | 11–12 |
| LI-03 | confirmed | `scoring.w` 276–295; `score_detail` 1610–1620; `MODE_WEIGHTS` has no `both` | Nested `home` / `invest` weights are ignored while the mode context is `both`. Components run at weight 1. A flat weight dict still applies to both. | The both-mode rank is not the worse of the two standalone weighted scores. | 3 |
| LI-04 | confirmed | `climate_score` 2272–2303 calls `_climate_points`, which multiplies by `w("heat")`, `w("water")`, `w("risks")`, `w("amoc")` | Heat-only evidence grades good at weight 1 and caution at weight 0. The docstring says the grade does not move with the sliders. | A preference slider changes the hazard grade. | 3 |
| LI-05 | confirmed | `climate_score` 2272–2303; `_climate_flags` 2232–2268 | Heat, nearby water, and low stress, with no flood, fire, or fire-danger fields, grade excellent. | Missing critical layers are not unknown. They are simply absent from the grade. | 8 |
| LI-06 | confirmed | `geo.EXACT_ENOUGH` 745; `_score_both` 1660–1662; `location_confidence` 849–855; `LEVEL_OF` 807 | `street` and `advertised` pass the both-mode building gate. Street confidence says "the street, not the building". `advertised` is missing from `LEVEL_OF`, so its confidence level falls through to `area`. | A street centroid or an unguaranteed pin is treated as the building. | 8 |
| LI-07 | confirmed | `costs.building_m2` 362–391; `scoring._score_invest` 2901; `market_value_estimate` 718–728; `bidcap._value` 40–57 | Stored land 150 m² plus a notice floor of 80 m²: the gate and the renovation use 80 (`notice`). Market value and the bid-cap note use 150. | The same listing is "80 m²" for one decision and "150 m²" for another. | 4 |
| LI-08 | confirmed | `scoring._score_invest` 2904–2907; `db.load_listings` 1515–1519; `costs.over_cash` 802–813 | A learned close of 4× base changes the investment reasons. `over_cash` is called with `bid=None`, so the cash figure uses `price_to_pay` (current bid, else minimum, else base). | Ranking can assume a much higher acquisition cost than the cash filter. | 5 |
| LI-09 | confirmed | `db.mark_duplicates` 643–716; `_twin_units` 601–629 | Two portals, different street text, €200 and 2 m² apart, same municipality: one is hidden. Two Citius rows with the same title and the same first 80 characters of description, different unit text after that, collapse to the cheaper row. | A possible match is stored as identity. The other legal unit disappears from the lists. | 6A |
| LI-10 | confirmed | `sources/pt.py` `scrape_citius` 918–931; `_keep_citius_details` 788–802 | The first bem of a processo keeps the unsuffixed id. The next becomes `-2`, `-3`, by encounter order. Removing the first lot writes the next lot onto the unsuffixed id and copies that id's learned raw text and pin onto it. | Offers and learned details can attach to a different lot. Rows are not deleted; the old `-2` row remains. | 6B |
| LI-11 | confirmed | `INVEST_DISCOUNT_TRUST` 2833; `_score_invest` 2945–2957 | At €2,000/m² and 80 m², all-in on €50,000 is 64% under and keeps rent points. €30,000 is 77% under: +curve is replaced by `INVEST_TOO_CHEAP` (-15) and rent is cleared. €15,000 (87%) scores the same as €30,000. | A deeper, still-unverified discount does not rank above a smaller one past the cliff. `costs.estimate` itself can still carry a rent; the score drops it. | 9 |
| LI-12 | confirmed for a bid seen within 6 hours of the end; the older "freeze as unknown and never watch" path is already guarded | `outcomes.record_results` 79–90; `watch_closing` 110–114; `FRESH_HOURS` 33; `UNKNOWN_AFTER_H` 39 | A €9,000 bid last seen at 10:00, end 11:00, recorded at 12:00 becomes `sold`. The watch's SQL skips `sold`. A bid last seen more than 6 hours before the end waits, then becomes `unknown`, and the watch may still upgrade that row (`tests/test_outcomes.py`). | A pre-close bid inside the fresh window is never re-read. | 7 |
| LI-13 | confirmed | `confirm.page_gone` 37–56; `check_top` 84–92; Citius `url` is `CITIUS_URL` (`sources/pt.py` 736–737) | 404 and 410 are gone. 403 and 500 are stored `gone: false`. A generic search page with no sold word is stored the same way. Citius has no per-sale page. | Failure and "this URL is not the sale" become "checked, still for sale". | 7 |
| LI-14 | confirmed | `pipeline.run_scan` 272–324 | Report, then alerts, then `record_results`, then geocoding, climate, photos, then `confirm.check_top`. | Alerts and the report can describe a listing before its climate, photos, and sale check exist. `load_best` does not include Live and invest. | 12 |
| LI-15 | confirmed | `db.last_ok_by_source` 901–908; staleness 1448–1449; `sources.run_source` sets `ok` when `count > 0` | A scrape log with `count=1` moves `last_ok`. A listing last seen 10 days earlier, in a place that run never returned, is hidden as stale. | A partial positive sweep is treated as evidence that the rest of the source is gone. | 7 |
| LI-16 | confirmed | score inputs `db.py` 1404–1410 and 1466; `climate.stored` 558–571; `assess` cache key `climate.py` 469–471; `prices._load` is mtime-keyed | Refresh keeps a score when town, close, and land-market counts are unchanged. Replacing €4,000/m² with €800/m² did not change the cached rank until `forget_scores`. `stored()` returns a result whose layer stamp is old and whose version is not `climate.VERSION`. | Market or hazard content can change without a new decision. A file-mtime refresh inside `prices.py` does not help once the score is cached. | 8 |

Side observation, not one of the sixteen. `costs._liquidity_vacancy` (495–510) does not match its docstring. At population 10,000, `tanh(10000/25000)` is about 0.38, and vacancy is more than twice `RENT_VACANCY_SHARE`. The docstring says factor ≈ 0.76 and vacancy × 1.3. Not repaired in this batch.

## 4. Disagreement with a "this was already fixed" reading

Current comments show partial earlier work. They do not close the findings:

- Unpriced Live and invest is documented on purpose (`buyer_priorities` and `_score_both`). It is still a living-fit rank.
- `UNKNOWN_AFTER_H` stops a long-stale bid being frozen before the watch. It does not stop a bid seen inside `FRESH_HOURS` of the end.
- `climate.assess_pending` re-reads when the pin or `built.json` stamp changes. `stored()` does not apply that stamp, and the in-process assess cache is only the coordinate.
- `building_m2` refuses a stored land figure for the gate and the renovation. The investment discount and the bid cap still start from `area_m2`.

## 5. Tests that already encode current behaviour

Do not weaken these when a later batch changes the behaviour. Replace the expectation in the same change, with a reason:

- `tests/test_both.py::test_a_province_average_is_not_a_price_for_this_house` (LI-01)
- `tests/test_modes.py::test_investment_does_not_believe_an_impossible_discount` (LI-11)
- `tests/test_db.py::test_duplicates_are_flagged_not_deleted` and `test_flats_of_one_building_show_once_cheapest_first` (LI-09)
- `tests/test_sources.py::test_citius_keeps_every_bem_of_a_processo` (order suffix, LI-10)
- `tests/test_sources.py::test_a_licitor_pin_is_kept_and_is_not_the_building` (`advertised` is in `EXACT_ENOUGH`)
- `tests/test_outcomes.py::test_ended_sales_are_recorded_once_with_their_last_bid` (a bid an hour before the end is `sold`)

`tests/test_live_invest_batch0.py` is characterisation. Its assertions describe today. They are not the acceptance tests for later batches.

## 6. Behaviour, database, recovery

No production behaviour change. No schema, config, listing id, offer, or letter change. Recovery is `git checkout` of this branch's two added files. Nothing was written to `auctions.db`.

## 7. Batch 1 — pure eligibility evaluator

Not implemented in this batch.

Add one pure function, in a new module, with no network, database, email, file download, invented price, or read of the scoring weight context. Suggested shape:

```text
evaluate(evidence, policy, now) -> result
```

`evidence`, `policy`, and `result` are JSON-serialisable. `now` is an explicit datetime the caller passes in.

`result`:

- `policy_id`, `policy_version`, `evaluated_at`
- `readiness`: `recommended` | `research_needed` | `rejected`
- `requirements`: map of stable keys to
  - `state`: `pass` | `fail` | `unknown` | `not_applicable`
  - `reason_code`
  - `evidence_ids`
  - `provenance`: `reviewed` | `asserted` | `inferred` | `missing`
- `unmet`: reason codes that are not pass
- `next_evidence`: what would move an unknown critical requirement
- `disclaimer`: decision support, not a legal, engineering, or valuation certificate

Initial keys: `residential_identity`, `ownership_right`, `possession`, `title_charges`, `legal_access_use`, `inspection_work_scope`, `building_location`, `sea_beach_policy`, `hazard_coverage`, `specific_sale_availability`, `financial_evidence`, `financial_margin`, `cash_fit`, `procedural_timing`.

Rules for this first policy version:

- `recommended` only when every critical requirement is `pass` on reviewed evidence. A high score, a seller sentence, or an AI inference is not that.
- A personal preference miss (`sea_beach_policy`, heavy rebuild inside `inspection_work_scope`) is `fail` with a `preference.*` code and readiness `rejected`.
- A confirmed safety or title defect is `fail` with a `defect.*` code and readiness `rejected`.
- An unknown critical fact is `unknown`, readiness `research_needed`, with `next_evidence`. It is not pass.
- `not_applicable` is only for a requirement the policy says does not apply to that kind of sale.
- `shortlisted: true` is recorded and does not change any state.
- The sea is the existing sea-beach rule. A river or lake is not a pass. Budget and country lists are inputs in `policy`, copied from current settings, not new numbers.

Coexistence with `AGENTS.md`: `scoring.score` and `db.load_listings` / `load_best` stay the discovery rank. The evaluator does not sort the tabs, hide rows, or replace `load_best`. A later recommendation feed (Batch 11–12) is a new contract. Batch 1 has no UI and no migration.

Acceptance tests for Batch 1, to be written then so they pass:

- Each requirement can be pass, fail, unknown, and not-applicable.
- Seller wording and a high score do not pass ownership, possession, title, access, or hazard coverage.
- Missing flood or fire coverage is unknown, not pass.
- A street or advertised pin does not pass `building_location`.
- Area 150 stored and 80 in the notice does not pass floor identity.
- No local valuation does not pass `financial_evidence`.
- Predicted close above cash does not pass `cash_fit` when the policy cash is the current-price all-in only if the policy says so; the test passes the scenario in, it does not read Settings.
- `shortlisted` leaves a failed or unknown requirement unchanged.
- Preference fail and defect fail have different reason-code prefixes.
- Zero inputs that pass yield `research_needed` or `rejected`, never `recommended`.
- The function does not touch the database, the network, or `scoring._WEIGHTS`.

## 8. Owner decisions before Batch 1

Proposed defaults, not silent new preferences. Contradict any of them before Batch 1 if they are wrong:

1. A confirmed miss of the existing sea-within-10 km rule, or a confirmed heavy rebuild, is `rejected` (`preference.*`), not left as research.
2. Unknown title, access, hazard coverage, sale identity, or valuation is `research_needed`, not `rejected` and not `recommended`.
3. A star or shortlist never produces `recommended`.
4. Batch 1 does not change budgets, countries, the scorer, or the lists.
