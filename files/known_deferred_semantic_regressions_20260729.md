# Known deferred semantic regressions — 2026-07-29

The identical regression suite was executed against the preserved pre-containment runtime and the current runtime. Before fixture retirement, both executions produced the same result: 5 failed and 75 passed. Three failures were caused solely by unavailable locked fixture files and are covered by `regression_fixture_retirement_decision_20260729.md`.

The following two semantic expectation failures occur unchanged in both runtimes and are registered as known deferred issues:

1. `test_tgiu_normalization_forces_arkat_fields_not_applicable`
   - Current output also contains `TGIU_MISSING_FURTHER_INVESTIGATION` and `TGIU_MISSING_MOISTURE_FLAG`.
   - The test expects only `TGIU_MISSING_REASON`.
2. `test_markdown_deterministic_regression_cases[5]`
   - Current output classifies `anbefalt_tiltak` as `CORRECT`.
   - The test expects `MISSING (anbefalt_tiltak)`.

Disposition: deferred to a separately governed semantic/judge review. They are not changed, suppressed, or represented as passing by the public-containment release.

Runtime impact: none. This registration does not modify extraction, mapping, semantic, judge, scoring, or canonical `feedback_v11` behavior.
