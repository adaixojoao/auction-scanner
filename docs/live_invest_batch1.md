# Live and invest — Batch 1 eligibility evaluator

An isolated evaluator. It is not wired into the scanner. Budgets, countries, scoring, visibility, IDs, schema, letters, alerts, and the current lists are unchanged. Batch 0's findings are still the current application behaviour.

## 1. Starting point

| Item | Value |
| --- | --- |
| Base | `5462cf2277fff1c77be23a058657ea0bad28450f` (merge of PR #107) |
| Branch | `batch1-eligibility`, created from that commit |
| Master after the base | `git log 5462cf2..origin/master` was empty before this work, so the baseline stayed valid |
| Working tree before the branch | Clean. Nothing was reset, stashed, or discarded |
| This batch's commit | None. The files are local and uncommitted |
| Files | `eligibility.py`, `tests/test_eligibility.py`, `docs/live_invest_batch1.md` |

No production database, scan, scheduled task, mail, or bid. No push and no merge.

## 2. Public contract

```text
evaluate(evidence, policy, now) -> dict
```

`EligibilityError` subclasses `ValueError`. Malformed contracts raise it. A missing or unresolved property fact is `unknown`, not an exception.

`evidence` and `policy` are JSON-compatible objects. `now` is a timezone-aware `datetime` and is normalised to UTC. A naive `now` raises `EligibilityError`. The function does not read the clock.

The result satisfies `json.dumps(result, allow_nan=False)`. Inputs are not mutated. Finding order does not change the outcome or the output order. There is no configuration, database, network, filesystem, or randomness, and no import of the scorer.

Schema and evaluator versions are `"1"`. The only mode is `live_and_invest`.

### Evidence

Envelope: `schema_version` `"1"`, non-empty `subject_id` and `sale_id`, and a `findings` list.

Each finding needs a unique `id`, a catalogue `requirement_key`, its own `subject_id` and `sale_id`, `finding` (`meets`, `fails`, or `undetermined`), `provenance` (`reviewed`, `asserted`, or `inferred`), a non-empty `reference`, and `observed_at`. A reviewed finding also needs a non-empty `reviewer_ref` and `reviewed_at`. Optional fields are `valid_until`, `supersedes`, and a `details` object.

Reviewer and date fields record a claimed review. They do not authenticate the reviewer or certify the document.

Only a current reviewed finding whose `details` actually support the predicate can pass or fail. The word `meets` or `fails` alone is not enough. Asserted and inferred findings are research leads.

Financial findings carry `financial_policy_id` and `financial_policy_version` inside `details`. A different pair is `evidence.policy_mismatch` and cannot fail the requirement.

### Policy

A synthetic policy names `schema_version`, `policy_id`, `policy_version`, `mode` `live_and_invest`, `sea_max_km` (the initial rule is 10), `heavy_rebuild_excluded: true`, `required_hazards`, `financial_policy_id`, `financial_policy_version`, and `freshness`.

`freshness` names every catalogue key and no others. Each rule is either `{"immutable": true}` or `{"max_age_seconds": <positive finite number>}`. There is no universal expiry. The example policy lives in the tests and is not a production default.

`disabled_requirements`, `exemptions`, `optional_requirements`, `require_title: false`, `heavy_rebuild_excluded` other than true, and a finding or detail marked `not_applicable` or `exempt` raise `EligibilityError`. No mandatory key is returned as `not_applicable`.

Extra fields such as `starred`, `score`, `source`, and `override` are ignored. They never appear in the result and never promote readiness.

### Time, scope, conflict

Timestamps are strings. A trailing `Z` is accepted. Naive or malformed timestamps raise. `reviewed_at` cannot precede `observed_at`. `observed_at` and `reviewed_at` cannot be after `now`.

Evidence is stale when `now` is at or after the earlier of `valid_until` and `observed_at + max_age_seconds`. A later review date does not refresh the observation. Stale evidence is unknown. A historic pass or adverse code is kept on `historic_reason_codes` and mentioned in the research text.

A finding whose property or sale id differs from the envelope is unknown (`evidence.mismatch`), not a pass.

Current reviewed pass and fail for the same key conflict. A current asserted or inferred finding that says the opposite of a current reviewed pass or fail also conflicts. The state is unknown with `evidence.conflict`, whatever the list order. Conflict is resolved only by explicit reviewed supersession.

`supersedes` must name another finding in the same requirement, property, and sale. Cycles, missing ids, cross-scope references, an unreviewed finding superseding a review, and an older observation superseding a newer one raise `EligibilityError`.

### States and readiness

Each requirement is `pass`, `fail`, or `unknown`.

1. Any fail → `rejected`.
2. Otherwise any unknown → `research_needed`.
3. Otherwise `recommended`.

Every key is returned, including unknowns that sit beside a failure. `rejected` does not dismiss, hide, or delete a listing.

Reason namespaces: `preference.*` (sea distance, heavy rebuild), `safety.*` (identity, ownership, possession, title, access, use, inspection, work uncertainty, floor area, location, hazards), `financial.*` (evidence basis, margin, cash), `execution.*` (this sale, procedure), `evidence.*` (missing, stale, insufficient, unreviewed, conflict, mismatch, policy mismatch).

The result also carries schema and evaluator versions, policy id and version, mode, subject and sale ids, `evaluated_at` in UTC, ordered requirements (state, reason codes, historic codes, evidence id and provenance, research need), aggregated reason codes, failed keys, unknown keys, research-need descriptions, and fixed limitations. Research needs are descriptions, not tasks. There is no score, rank, or confidence probability.

### What an adequate detail says

These predicates are structural. They do not interpret a statute or compute a price. A future adapter must only set them from evidence that genuinely supports them.

| Key | Pass | Reviewed adverse code |
| --- | --- | --- |
| `residential_identity` | `asset_kind` residential, and `bundle` is not true | land, movable, bundle → `safety.residential_identity` |
| `complete_ownership` | `right` complete | fraction or bare → `safety.complete_ownership` |
| `vacant_possession` | `possession` vacant | occupied or refused → `safety.vacant_possession` |
| `title_and_charges` | `charges_reviewed` true and `exposure` none or reviewed | adverse → `safety.title_and_charges` |
| `legal_access` | `access` legal | none → `safety.legal_access` |
| `residential_use` | `use` residential_supported | prohibited → `safety.residential_use` |
| `inspection` | `inspection` adequate and a non-empty `scope` | inadequate → `safety.inspection` |
| `work_scope` | `heavy_rebuild` false and `uncertainty` bounded | heavy rebuild → `preference.heavy_rebuild`; unbounded → `safety.work_scope` |
| `floor_area` | `basis` floor and `compatible` true | land or footprint → `safety.floor_area` |
| `building_location` | `precision` building | street, town, municipality, advertised → `safety.building_location` |
| `sea_distance` | `water` sea, finite `km` within `sea_max_km`, non-empty `basis` | river, lake, or farther than the maximum → `preference.sea_distance` |
| `hazard_coverage` | every required hazard has reference, scale, `applicable` true, `covered` true | a required hazard `covered` false → `safety.hazard_coverage` |
| `hazard_acceptability` | `acceptable` true and the same per hazard with `acceptable` true | overall or per-hazard `acceptable` false → `safety.hazard_acceptability` |
| `specific_sale_available` | `specific_sale` true and `generic_url` not true | otherwise those flags → `execution.specific_sale_available` |
| `financial_evidence` | non-empty `basis`, and the financial policy ids match | `basis_ok` false → `financial.evidence` |
| `financial_margin` | `meets_margin` true, policy ids match | false → `financial.margin` |
| `cash_fit` | `fits_cash` true, policy ids match | false → `financial.cash_fit` |
| `procedural_readiness` | non-empty `method`, `deadline_known` true, `can_submit` true | either flag false → `execution.procedural_readiness` |

A favourable hazard grade does not replace coverage. Booleans are not numbers. NaN and infinity anywhere in the objects raise.

## 3. Synthetic example

The fixtures use subject `home-1`, sale `sale-1`, reviewer alias `reviewer-alias`, policy `live-invest-synthetic` version `1`, financial policy `fin-synth` version `1`, hazards `fire` and `flood`, and `now` of 2026-10-14 12:00 UTC. Nothing here is a real property or a real reviewer.

Calling `evaluate` on a complete set of adequate reviewed findings returned:

```text
readiness: recommended
evaluated_at: 2026-10-14T12:00:00+00:00
failed_keys: []
unknown_keys: []
```

The sea-distance row from that same call:

```json
{"key": "sea_distance", "state": "pass", "reason_codes": [], "historic_reason_codes": [], "evidence": [{"id": "f-sea_distance", "provenance": "reviewed"}], "research_need": null}
```

An empty findings list returns `research_needed` and `evidence.missing` on every key. A reviewed sea distance of 12 km returns `rejected` with `preference.sea_distance`, and still lists every other key.

## 4. Checks

Interpreter: `C:\Users\adaix\AppData\Local\Programs\Python\Python313\python.exe` (the PATH miniforge interpreter has no pytest). Counts are from this run.

| Command | Exit | Summary |
| --- | --- | --- |
| `python -m pytest -q tests/test_eligibility.py` | 0 | `59 passed in 0.90s` |
| `python -m pytest -q` | 0 | `847 passed in 225.89s (0:03:45)` |
| `python -m pyflakes` on top-level `*.py`, `sources`, `tests`, and `scripts` | 0 | no findings |

The GitHub matrix was not run. Passing locally is not a green Ubuntu/Windows 3.11/3.13 result.

## 5. Trust boundary

A recommended result means every mandatory key had adequate current reviewed support in the supplied objects, under the supplied policy, at the supplied time. It does not mean the property is safe, the reviewer is real, the document was read, or the financial assessment was recalculated. Batch 0's scoring and loader behaviour is untouched.

## 6. Next batch, not started

Add persistence for evidence records and review provenance. Do not show a recommendation in the app in that batch.
