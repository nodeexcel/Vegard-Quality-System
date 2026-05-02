# Dommer B Test Execution Sheet (v1)

Use this sheet to run and document validation before further fixes.

Source of truth:
- `files/dommer_b_test_set_v1.md`

---

## 1) Test Run Metadata

- Date:
- Runner:
- Branch/commit:
- Environment (local/stage/prod):
- Model/provider used by Dommer B:
- Notes:

---

## 2) 9-Case Baseline Results

### Quick Summary

| Case | Point | Expected | Actual | Result |
|---|---|---|---|---|
| 1 | Halden 5.1 Loft (TGIU) | 11 deductions |  | PASS/FAIL |
| 2 | Halden 2.1 Yttervegger (TG3) | 0 deductions |  | PASS/FAIL |
| 3 | Halden 1.1 Byggegrunn (TG2, 2018) | 0 deductions |  | PASS/FAIL |
| 4 | Halden 1.2 Krypekjeller (TG2, 2018) | 9 deductions |  | PASS/FAIL |
| 5 | Halden 7.2.2 Vaskerom gulv (TG3) | 5 deductions |  | PASS/FAIL |
| 6 | Fredrikstad Nedløp og beslag (TG3) | 0 deductions |  | PASS/FAIL |
| 7 | Fredrikstad Veggkonstruksjon (TG2, 2018) | 9 deductions |  | PASS/FAIL |
| 8 | Synthetic Etasjeskille (TG2, 2025) | 0 deductions |  | PASS/FAIL |
| 9 | Synthetic TG2 missing tiltak (2025) | 5 deductions |  | PASS/FAIL |

Total expected deductions across set: **39**

---

## 3) Detailed Case Logs

For each case:
- Copy the input from `dommer_b_test_set_v1.md`
- Run through Dommer B pipeline
- Paste full actual JSON response
- Compare against expected

### Case 1
- Case ID: 1
- Input reference: `dommer_b_test_set_v1.md` Case 1
- Actual output JSON:
```json
{}
```
- Comparison:
  - `field_results`: PASS/FAIL
  - `tgiu_findings`: PASS/FAIL
  - `has_errors`: PASS/FAIL
  - deductions via mapping: expected 11 / actual __
- Result: PASS/FAIL
- Diff notes:

### Case 2
- Case ID: 2
- Input reference: `dommer_b_test_set_v1.md` Case 2
- Actual output JSON:
```json
{}
```
- Comparison:
  - `field_results`: PASS/FAIL
  - `tgiu_findings`: PASS/FAIL
  - `has_errors`: PASS/FAIL
  - deductions via mapping: expected 0 / actual __
- Result: PASS/FAIL
- Diff notes:

### Case 3
- Case ID: 3
- Input reference: `dommer_b_test_set_v1.md` Case 3
- Actual output JSON:
```json
{}
```
- Comparison:
  - `field_results`: PASS/FAIL
  - `tgiu_findings`: PASS/FAIL
  - `has_errors`: PASS/FAIL
  - deductions via mapping: expected 0 / actual __
- Result: PASS/FAIL
- Diff notes:

### Case 4
- Case ID: 4
- Input reference: `dommer_b_test_set_v1.md` Case 4
- Actual output JSON:
```json
{}
```
- Comparison:
  - `field_results`: PASS/FAIL
  - `tgiu_findings`: PASS/FAIL
  - `has_errors`: PASS/FAIL
  - deductions via mapping: expected 9 / actual __
- Result: PASS/FAIL
- Diff notes:

### Case 5
- Case ID: 5
- Input reference: `dommer_b_test_set_v1.md` Case 5
- Actual output JSON:
```json
{}
```
- Comparison:
  - `field_results`: PASS/FAIL
  - `tgiu_findings`: PASS/FAIL
  - `has_errors`: PASS/FAIL
  - deductions via mapping: expected 5 / actual __
- Result: PASS/FAIL
- Diff notes:

### Case 6
- Case ID: 6
- Input reference: `dommer_b_test_set_v1.md` Case 6
- Actual output JSON:
```json
{}
```
- Comparison:
  - `field_results`: PASS/FAIL
  - `tgiu_findings`: PASS/FAIL
  - `has_errors`: PASS/FAIL
  - deductions via mapping: expected 0 / actual __
- Result: PASS/FAIL
- Diff notes:

### Case 7
- Case ID: 7
- Input reference: `dommer_b_test_set_v1.md` Case 7
- Actual output JSON:
```json
{}
```
- Comparison:
  - `field_results`: PASS/FAIL
  - `tgiu_findings`: PASS/FAIL
  - `has_errors`: PASS/FAIL
  - deductions via mapping: expected 9 / actual __
- Result: PASS/FAIL
- Diff notes:

### Case 8
- Case ID: 8
- Input reference: `dommer_b_test_set_v1.md` Case 8
- Actual output JSON:
```json
{}
```
- Comparison:
  - `field_results`: PASS/FAIL
  - `tgiu_findings`: PASS/FAIL
  - `has_errors`: PASS/FAIL
  - deductions via mapping: expected 0 / actual __
- Result: PASS/FAIL
- Acceptable drift note:
  - `risiko` may be `CORRECT` or `MISSING (risiko)` (allowed by spec)
- Diff notes:

### Case 9
- Case ID: 9
- Input reference: `dommer_b_test_set_v1.md` Case 9
- Actual output JSON:
```json
{}
```
- Comparison:
  - `field_results`: PASS/FAIL
  - `tgiu_findings`: PASS/FAIL
  - `has_errors`: PASS/FAIL
  - deductions via mapping: expected 5 / actual __
- Result: PASS/FAIL
- Diff notes:

---

## 4) One Real-Report Diagnostic Log (Required)

Report file:
- Name:
- Run ID / timestamp:

### Point Flow Counters

- extracted_points_total:
- extracted_points_tg2_tg3_tgiu:
- sent_to_dommer_b:
- dommer_b_responses_received:
- dommer_b_valid_responses:
- points_in_funn_per_bygningsdel:
- dropped_points_total:

### Dropped Points Breakdown

| Point ID | Stage dropped | Reason |
|---|---|---|
|  |  |  |

Suggested reason labels:
- `filtered_not_relevant_tg`
- `missing_point_text`
- `dommer_b_call_failed`
- `dommer_b_invalid_json`
- `normalization_rejected`
- `dedupe_suppressed`
- `output_filter_suppressed`

---

## 5) Final Baseline Status

- 9-case baseline: PASS/FAIL
- Real-report diagnostics complete: YES/NO
- Ready for targeted fix list from client: YES/NO
- Blocking failures summary:

