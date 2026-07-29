# Regression fixture retirement decision — 2026-07-29

Decision: retire the three regression tests that depend on `dommer_b_real_report_1806_full.json`, `dommer_b_real_report_1807_full.json`, and `dommer_b_real_report_1808_full.json` until a separately governed fixture restoration is approved.

Reason:

- The accepted pre-change and current suites both demonstrated the same three missing-fixture failures.
- Fixtures 1807 and 1808 do not exist in the repository history, host evidence archives, or current workspace.
- A historical 1806 file can be recovered from commit `57c5271`, but it does not match the test's current declared expected point and therefore is not the locked fixture represented by the test.
- Reconstructing these files from current database state would fabricate new baselines rather than restore the missing locked artifacts.

Implementation: the three affected tests are explicitly marked skipped with a reference to this decision. They are not represented as passing. The two independent semantic expectation failures remain failing and are registered as known issues deferred to the controlled judge-chain round.

Runtime impact: none. No extraction, mapping, semantic, judge, canonical feedback, or production runtime code is changed by this decision.
