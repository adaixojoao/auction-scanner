# Live and invest — Batch 1 eligibility evaluator

An isolated evaluator. It is not wired into the scanner. Budgets, countries, scoring, visibility, IDs, schema, letters, alerts, and the current lists are unchanged. Batch 0's findings are still the current application behaviour.

## 1. Starting point

| Item | Value |
| --- | --- |
| Base | `5462cf2277fff1c77be23a058657ea0bad28450f` (merge of PR #107) |
| Branch | `batch1-eligibility`, created from that commit |
| Master after the base | `git log 5462cf2..origin/master` was empty before this work, so the baseline stayed valid |
| Working tree before the branch | Clean. Nothing was reset, stashed, or discarded |
| First publication | `72d3d511269b7b11996a2652bbba34392bef9a28` on draft PR #108. At the earlier handoff these files were still local; that is no longer the case |
| Batch 1A | Corrections on the same branch and the same draft PR. The commit that contains this paragraph is reported in the handoff, not written into the paragraph |
| Files | `eligibility.py`, `tests/test_eligibility.py`, `docs/live_invest_batch1.md` |

No production database, scan, scheduled task, mail, or bid. PR #108 stays a draft. Nothing is merged.

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

If a reviewed record's details contradict that word, or contradict each other, the requirement is `unknown` with `evidence.inconsistent`. The evaluator does not rewrite the claim into a verified failure. A known field with the wrong type, a non-string enum, a non-finite number, an integer whose absolute value exceeds `10**11`, or any other non-JSON value raises `EligibilityError`. A missing field stays unknown.

Financial findings carry `financial_policy_id` and `financial_policy_version` inside `details`. Sea-distance and both hazard findings carry the eligibility `policy_id` and `policy_version`. A different pair is `evidence.policy_mismatch` and cannot pass, fail, or supersede. Identity, ownership, possession, title, access, use, inspection, work, floor area, building location, sale availability, and procedural readiness are facts about the asset or the sale. They are not bound to a policy version.

### Policy

A synthetic policy names `schema_version`, `policy_id`, `policy_version`, `mode` `live_and_invest`, `sea_max_km`, `heavy_rebuild_excluded: true`, `required_hazards`, `financial_policy_id`, `financial_policy_version`, and `freshness`.

Schema version 1 accepts `sea_max_km` of `10` or `10.0` and rejects every other value, including 0, negatives, 50, 100, booleans, and non-finite numbers. A different preference profile needs a later contract. `required_hazards` must be a non-empty list of distinct non-empty strings. There is no installed hazard-policy default.

`freshness` names every catalogue key and no others. Each rule is either `{"immutable": true}` or `{"max_age_seconds": <positive finite number>}`. The duration must also be at most `10**11` seconds so that adding it to a timestamp cannot overflow. There is no universal expiry. The example policy lives in the tests and is not a production default.

`disabled_requirements`, `exemptions`, `optional_requirements`, `require_title: false`, `heavy_rebuild_excluded` other than true, and a finding or detail marked `not_applicable` or `exempt` raise `EligibilityError`. No mandatory key is returned as `not_applicable`.

Extra fields such as `starred`, `score`, `source`, and `override` are ignored. They never appear in the result and never promote readiness.

### Time, scope, conflict

Timestamps are strings. A trailing `Z` is accepted. Naive or malformed timestamps raise. `reviewed_at` cannot precede `observed_at`. `observed_at` and `reviewed_at` cannot be after `now`.

Evidence is stale when `now` is at or after the earlier of `valid_until` and `observed_at + max_age_seconds`. A later review date does not refresh the observation. Stale evidence is unknown. A historic pass or adverse code is kept on `historic_reason_codes` and mentioned in the research text.

A finding whose property or sale id differs from the envelope is unknown (`evidence.mismatch`), not a pass.

Current reviewed pass and fail for the same key conflict. A current asserted or inferred finding that says the opposite of a current reviewed pass or fail also conflicts. The state is unknown with `evidence.conflict`, whatever the list order. Conflict is resolved only by explicit reviewed supersession.

`supersedes` must name another finding in the same requirement, property, and sale. The replacement itself must be reviewed, must be `meets` or `fails`, and must be an adequate consistent pass or fail. That applies even when the target is only asserted or inferred. Cycles (including a self-cycle and an equal-time pair), two replacements of one record, missing ids, cross-scope references, an older observation, and an older review date raise `EligibilityError`. An undetermined or otherwise inadequate replacement raises rather than erasing the target.

A replacement that is stale, or that was assessed under another policy, does not resolve its target. The target stays in force. A chain resolves each link only while that link's replacement is itself a current, in-policy, adequate review.

### States and readiness

Each requirement is `pass`, `fail`, or `unknown`.

1. Any fail → `rejected`.
2. Otherwise any unknown → `research_needed`.
3. Otherwise `recommended`.

Every key is returned, including unknowns that sit beside a failure. `rejected` does not dismiss, hide, or delete a listing.

Reason namespaces: `preference.*` (confirmed sea distance beyond 10 km, confirmed heavy rebuild), `safety.*` (identity, ownership, possession, title, access, use, inspection, unbounded work, an explicit absence of usable floor area, a confirmed wrong asset, an unacceptable hazard), `financial.*` (an invalid basis with no competing basis text, margin, cash), `execution.*` (the sale is sold, withdrawn, or unavailable; a known deadline cannot be met), `evidence.*` (missing, stale, insufficient, unreviewed, conflict, inconsistent, mismatch, policy mismatch).

Coarse location, a land or footprint figure, a generic search URL, an uncovered hazard, and an unknown deadline are unknown. They are not rejections.

Each evidence entry has `id`, `provenance`, and `disposition`: `supporting`, `conflicting`, `stale`, `superseded`, `policy_mismatch`, `insufficient`, or `mismatch`. A pass does not list a superseded adverse record as support. A conflict names the conflicting ids.

The result also carries schema and evaluator versions, policy id and version, mode, subject and sale ids, `evaluated_at` in UTC, ordered requirements, aggregated reason codes, failed keys, unknown keys, research-need descriptions, and fixed limitations. Research needs are descriptions, not tasks. There is no score, rank, or confidence probability.

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
| `floor_area` | `basis` floor and `compatible` true | `usable_floor_absent` true → `safety.floor_area`. A land or footprint figure is insufficient |
| `building_location` | `precision` building | `wrong_asset` true → `safety.building_location`. Street, town, municipality, or advertised is insufficient |
| `sea_distance` | `water` sea, finite `km` from 0 to 10, non-empty `basis`, and this policy's id and version | river, lake, or farther than 10 km → `preference.sea_distance` |
| `hazard_coverage` | every required hazard has reference, scale, `applicable` true, `covered` true, under this policy id and version | a gap in coverage is insufficient, not a safety failure |
| `hazard_acceptability` | `acceptable` true and the same per hazard with `acceptable` true, under this policy | overall or per-hazard `acceptable` false → `safety.hazard_acceptability`. Claiming both true and false is `evidence.inconsistent` |
| `specific_sale_available` | `specific_sale` true and `generic_url` not true | `availability` sold, withdrawn, or unavailable → `execution.specific_sale_available`. A generic URL or an unidentified sale is insufficient. Both `specific_sale` and `generic_url` true is `evidence.inconsistent` |
| `financial_evidence` | non-empty `basis`, `basis_ok` not false, and the financial policy ids match | `basis_ok` false without a basis string → `financial.evidence`. A basis string together with `basis_ok` false is `evidence.inconsistent` |
| `financial_margin` | `meets_margin` true, policy ids match | false → `financial.margin` |
| `cash_fit` | `fits_cash` true, policy ids match | false → `financial.cash_fit` |
| `procedural_readiness` | non-empty `method`, `deadline_known` true, `can_submit` true | `deadline_known` true and `can_submit` false → `execution.procedural_readiness`. An unknown deadline or method is insufficient |

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
{"key": "sea_distance", "state": "pass", "reason_codes": [], "historic_reason_codes": [], "evidence": [{"id": "f-sea_distance", "provenance": "reviewed", "disposition": "supporting"}], "research_need": null}
```

An empty findings list returns `research_needed` and `evidence.missing` on every key. A reviewed sea distance of 12 km returns `rejected` with `preference.sea_distance`, and still lists every other key.

## 4. Checks

Interpreter for the local runs: `C:\Users\adaix\AppData\Local\Programs\Python\Python313\python.exe`.

Historical, before the first push, on the uncommitted tree:

| Command | Exit | Summary |
| --- | --- | --- |
| `python -m pytest -q tests/test_eligibility.py` | 0 | `59 passed in 0.90s` |
| `python -m pytest -q` | 0 | `847 passed in 225.89s (0:03:45)` |
| pyflakes on top-level modules, `sources`, `tests`, and `scripts` | 0 | no findings |

GitHub then ran that commit, `72d3d51`, twice (push run `38550844875` and pull-request run `38551084750`). All eight jobs passed: Ubuntu 3.11 and 3.13, Windows 3.11 and 3.13.

Batch 1A, after the corrections in this file:

| Command | Exit | Summary |
| --- | --- | --- |
| `python -m pytest -q tests/test_eligibility.py` | 0 | `87 passed in 1.98s` |
| `python -m pytest -q` | 0 | `875 passed in 397.29s (0:06:37)` |
| pyflakes on top-level modules, `sources`, `tests`, and `scripts` | 0 | no findings |

## 5. Trust boundary

A recommended result means every mandatory key had adequate current reviewed support in the supplied objects, under the supplied policy, at the supplied time. It does not mean the property is safe, the reviewer is real, the document was read, or the financial assessment was recalculated. Batch 0's scoring and loader behaviour is untouched.

## 6. Next batch, still paused

Evidence persistence and review provenance stay paused until this correction is reviewed. Do not show a recommendation in the app in that batch.
