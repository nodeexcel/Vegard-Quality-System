# Validert – Frozen A4 Acceptance Specification

## Version 2.0 – approved, frozen and hash-locked

**Specification ID:** `VALIDERT-A4-2026-02`  
**Prepared:** 7 August 2026  
**Status:** **APPROVED AND FROZEN – GOVERNING A4 ACCEPTANCE BASIS**  
**Governing decision:** `VALIDERT-GRD-2026-02`  
**Scope:** IVIT, Bolavi and BMTF controlled A4 runs, followed by a sealed Fremtind blind test

This document defines the expected end-to-end product result. Infrastructure, schemas, hashes and invariants are necessary but are not sufficient for acceptance. A4 passes only when the substantive findings reach the actual customer-facing result with correct evidence, score, gate and state.

The current authorised report runs remain shadow-only and unpublished until this specification is approved and implemented.

---

# 1. Acceptance inputs

| Report | File | PDF SHA-256 | Inspection date | Report date | Declared/used edition | Role |
|---|---|---|---|---|---|---|
| IVIT | `ivit-svak_arkat.pdf` | `249d6f04984ed45bcbea40afb7fa06fecb84b451634f721b57c655f5b9ab78f1` | 16.06.2026 | 22.06.2026 | NS 3600:2018 | Weak ARKAT, TGIU, methodology, legality/no-TG |
| Bolavi | `bolavi-egen-mangler_kostnadtg3.pdf` | `36141d9a02d3e7a9afad7466579c8432690d1ebbbdb4c4f21794b19614291c4a` | 26.05.2026 | 10.06.2026 | NS 3600:2025 | Narrow TG3-cost and point-binding test |
| BMTF | `Tilstandsrapport_Fritidsbolig-God_rapport.pdf` | `775ff7ca565ecd7785b7df7811b1698c8de2fdb0d996421da2a24dc448f1fb7d` | 10.07.2026 | 13.07.2026 | NS 3600:2025 | Good-report false-positive control |
| Fremtind | sealed owner-controlled report | withheld | withheld | withheld | determined only in blind run | Generalisation test |

The Fremtind report and its owner-side expectation record shall not be sent to the developer before the blind execution checkpoint.

---

# 2. Prerequisites

A4 execution may begin only after:

1. `VALIDERT-GRD-2026-02` is approved;
2. required governed changes are hash-pinned and implemented;
3. the two registered v46 test failures are either fixed or formally superseded:
   - `test_tgiu_normalization_forces_arkat_fields_not_applicable`;
   - `test_markdown_deterministic_regression_cases[5]`;
4. no existing signed v46 production behaviour is changed outside the approved diff;
5. the three report routes remain shadow-only until acceptance is complete.

The source-structure foundation is accepted. No additional source-structure round is required unless a concrete reproducible defect appears.

---

# 3. Required end-to-end flow

For every report the delivered evidence shall show:

1. source PDF and extracted-text hashes;
2. document facts and regime resolution;
3. physical source inventory;
4. summary/navigation/primary-point classification;
5. point and subsection binding;
6. governed retrieval with rule IDs and asset hashes;
7. raw semantic/methodology/legal assessments;
8. deterministic accepted/rejected/abstained decisions;
9. deduplication and category caps;
10. score and 96 gate;
11. normalized customer result;
12. final public payload and frontend rendering.

Every source candidate must be admitted, explicitly merged with trace, or abstained with reason. No point may disappear silently.

---

# 4. Result taxonomy

## 4.1 Mandatory accepted finding

A raw/customer finding that must be present.

## 4.2 Mandatory non-finding

A finding that must not be present. Its presence is a false positive and fails the run unless the product owner changes the frozen expectation.

## 4.3 Governed abstention/gap

Evidence is preserved and the relevant rule is retrieved, but no scored finding is invented because taxonomy, regime or evidence is insufficient.

## 4.4 Additional candidate

An additional high-confidence candidate is not automatically accepted. It must be reported as a variance with:

- exact evidence;
- permitted rule/type;
- correct regime;
- obligation class;
- score/gate effect;
- deduplication analysis.

It shall not be used to force or normalise the expected score.

---

# 5. IVIT acceptance

## 5.1 Frozen regime and coverage

Expected regime:

- `TRANSITION_2026`;
- amended substantive regulation effective 01.01.2026;
- NS 3600:2018 TG criteria lawfully used during transition;
- Validert ARKAT product-quality layer.

Required coverage:

- 30 TG2 points;
- 3 TGIU points;
- legality module;
- complete electrical module;
- HMS/radon/railing module across pages 27–28;
- three detached buildings;
- area tables and report assumptions.

Missing ANBEFALT TILTAK on TG2 shall not create a separate finding because this report lawfully uses NS 3600:2018.

## 5.2 Mandatory raw ARKAT findings

### `A4-IVIT-01` – Balcony/terrace

**Point:** Balkonger, terrasser og rom under balkonger, page 11  
**TG:** TG2  
**Evidence:**

- `Eldre terrasse mot nord i underetasjen trenger vedlikehold og utbedring.`
- `Årsak: Alder.`
- `Konsekvens: Har noe setningsskader og trenger utbedringer.`

**Expected raw findings:**

- `field=risiko`, `error_type=MISSING`;
- `field=konsekvens`, `error_type=TILTAK_AS_KONSEKVENS`.

**Deduction:** 5 + 2  
**Gate:** blocks 96 due to missing required Risiko.

**Normalized customer item:**  
Terrace point lacks a real risk description, and the consequence field mainly repeats present damage/repair need rather than explaining the technical or practical consequence.

### `A4-IVIT-02` – Basement bathroom wall/ceiling

**Point:** Underetasje > Bad > Overflater vegger og himling, pages 12–13  
**TG:** TG2  
**Evidence:**

- `Noen fuktmerker i himling over dusjkabinett.`
- `Årsak: Fuktbelastning fra bruk av dusj.`
- `Konsekvens: Risiko for videre utvikling av skade hvis forholdene vedvarer.`

**Expected raw finding:**

- `field=konsekvens`, `error_type=RISIKO_AS_KONSEKVENS`.

**Deduction:** 2

**Normalized customer item:**  
The text states continued risk but does not identify the concrete effect, such as material damage, reduced lifetime, function loss, indoor-climate impact or repair consequence.

### `A4-IVIT-03` – Foundation

**Point:** Tomteforhold > Grunnmur og fundamenter, page 26  
**TG:** TG2  
**Evidence:**

- `Trapp og platting på side nord, øst og sør er støpt imot grunnmur på boligen uten fuktsikring i mellom.`
- `Årsak: Utførelse.`
- `Konsekvens: Økt fuktbelasting på mur.`

**Expected raw findings:**

- `field=risiko`, `error_type=MISSING`;
- `field=konsekvens`, `error_type=TECHNICAL_DEVELOPMENT_AS_KONSEKVENS`.

**Deduction:** 5 + 2  
**Gate:** blocks 96 due to missing required Risiko.

**Normalized customer item:**  
The point stops at increased moisture load and does not explain what may develop or the actual damage/function consequence.

**IVIT ARKAT deduction:** 16.

## 5.3 Mandatory TGIU findings

Ordinary TG2/TG3 ARKAT findings are prohibited on the same TGIU defects.

### `A4-IVIT-04` – Septic tank

**Point:** Tomteforhold > Septiktank, page 27  
**Evidence:** `Septiktank er ikke inspisert.`

**Expected raw findings:**

- `TGIU_MISSING_REASON`;
- `TGIU_MISSING_FURTHER_INVESTIGATION`.

**Deduction:** 4 + 4

**Normalized customer item:**  
The report does not explain why the tank was not inspected and gives no concrete follow-up recommendation.

### `A4-IVIT-05` – Oil tank

**Point:** Tomteforhold > Oljetank, page 27  
**Evidence:** `Oljetank av ukjent type og alder. Lufte og påfyllingsrør er lokalisert på side øst. Er ikke inspisert.`

**Expected raw findings:**

- `TGIU_MISSING_REASON`;
- `TGIU_MISSING_FURTHER_INVESTIGATION`.

**Deduction:** 4 + 4

**Normalized customer item:**  
The report identifies the tank but does not explain the inspection limitation or recommend a concrete investigation/follow-up.

### `A4-IVIT-06` – First-floor washroom adjacent construction

**Point:** 1. etasje > Vaskerom > Tilliggende konstruksjoner våtrom, pages 21–22  
**Evidence:** `Hulltaking ikke mulig da vegger er i Ytong og ikke inneholder hulrom.`

**Expected raw finding:**

- `TGIU_MISSING_FURTHER_INVESTIGATION`.

**Not expected:** `TGIU_MISSING_REASON`.

**Deduction:** 4

**Normalized customer item:**  
The reason is stated, but the report does not give a technically relevant alternative investigation, documentation step, later-opening check or monitoring recommendation.

**IVIT TGIU deduction:** 20.

## 5.4 Mandatory methodology findings – detached buildings

The buildings are not scored merely because TG is absent. The finding concerns concrete described deviations without the governed explanatory structure after the report chose to assess them.

### `A4-IVIT-07` – Sjøbod

**Rule:** `E_METHOD.garasje_avvik_uten_arkat`  
**Page:** 29  
**Deduction:** 5  
**Gate:** no

One customer item shall explain that multiple concrete deviations are listed but no coherent cause/risk/consequence/measure assessment is provided.

### `A4-IVIT-08` – Båtbu

**Rule:** `E_METHOD.garasje_avvik_uten_arkat`  
**Page:** 30  
**Deduction:** 5  
**Gate:** no

### `A4-IVIT-09` – Garasje

**Rule:** `E_METHOD.garasje_avvik_uten_arkat`  
**Page:** 31  
**Deduction:** 5  
**Gate:** no

**Category E raw deduction:** 15  
**Category E scored deduction after cap:** 10

Prohibited:

- a deduction solely because no TG is assigned;
- one finding for every listed observation;
- treating observations as valid Årsak;
- duplicate TGIU findings unless an actual TGIU point is separately assigned and bound.

## 5.5 Mandatory IVIT non-findings

The following shall not be accepted:

1. **TG2 missing ANBEFALT TILTAK**  
   No separate absence finding under the applicable NS 3600:2018 release policy.

2. **Legality `L-AV-01`**  
   Page-5 deviations must be linked to the explanatory legality module on page 32. If linkage fails, abstain rather than invent a high-risk finding.

3. **Electrical `L-SE-01`**  
   The complete electrical section explains that errors may remain undetected and recommends qualified/full control.

4. **Railing/HMS `L-RK-01`**  
   Pages 27–28 include explicit fall/person-injury consequence.

5. **Area method**  
   Actual area tables use NS 3940:2023 and BRA/BRA-i/BRA-e/BRA-b. Stale boilerplate must not override them.

6. **TG3 cost**  
   The main dwelling has no TG3 point. No cost finding may be inferred from detached buildings or generic text.

7. **Duplicate scoring**  
   No semantic/legacy, ARKAT/TGIU or summary/primary duplication.

## 5.6 IVIT governed abstentions

### Basement washroom omitted hole drilling

Preserve the evidence, retrieve the applicable wet-room rule and emit a governance gap if no exact permitted type exists. Do not invent a scored regulatory finding.

### Validity wording

The report states validity from inspection date. Preserve as diagnostic only under the current catalogue.

## 5.7 IVIT expected result

| Item | Expected |
|---|---:|
| ARKAT deduction | 16 |
| TGIU deduction | 20 |
| Detached-building raw deduction | 15 |
| Detached-building scored deduction | 10 |
| Total scored deduction | 46 |
| Expected score | **54/100** |
| 96 gate | **blocked** |
| State | `complete_with_findings` |

Expected normalized customer items: 9.

---

# 6. Bolavi acceptance

## 6.1 Frozen regime and role

Expected regime:

- `TRANSITION_2026`;
- amended substantive regulation;
- report explicitly uses NS 3600:2025;
- NS 3600:2025 edition-specific methodology applies.

This is a narrow high-confidence TG3-cost test, not a broad ARKAT stress test.

## 6.2 Mandatory accepted finding

### `A4-BOLAVI-01` – Missing TG3 cost estimate

**Point:** 3 – Terrengforhold  
**Page:** 13  
**TG:** TG3  
**Evidence:** The point contains observation/cause, risk/consequence and recommended measure, but no point-bound cost class, interval or other schematic cost estimate.

**Expected rule:** `E_METHOD.tg3_cost_missing`  
**Expected finding ID:** `E_METHOD_tg3_cost_missing_3`  
**Obligation class:** regulatory + standard methodology where supported  
**Deduction:** 8  
**96 gate:** blocked

**Normalized customer item:**  
Point 3 is TG3 but lacks the required schematic cost estimate. Generic information explaining cost classes elsewhere in the report does not satisfy this specific point.

## 6.3 Mandatory Bolavi non-findings

The run shall not:

- use generic cost-class guidance on pages 4–5 as the point-3 estimate;
- flag point 3 for missing Årsak, Risiko, Konsekvens or ANBEFALT TILTAK solely because risk and consequence share a heading;
- reject NS 3600:2025 because the report was produced during the transition;
- create a legality finding where the report states drawings/completion documentation exist and use corresponds;
- create additional ARKAT findings on the accepted 12-point baseline without a product-owner-reviewed variance;
- return complete/no-findings if the cost finding is lost in admission or projection;
- corrupt valid Norwegian text through OCR/glyph repair.

## 6.4 Bolavi text and binding invariants

The accepted report run must preserve:

- point 3 binding;
- all accepted point text and field boundaries;
- letter-preserving normalization;
- resolvable evidence offsets;
- stable verdicts except for expressly approved governed changes.

Valid Norwegian words must not be altered by repair logic, including:

- `skjøter`;
- `skjulte`;
- `konstruksjon`.

## 6.5 Bolavi expected result

| Item | Expected |
|---|---:|
| Accepted findings | 1 |
| Total deduction | 8 |
| Expected score | **92/100** |
| 96 gate | **blocked** |
| State | `complete_with_findings` |

---

# 7. BMTF acceptance

## 7.1 Frozen regime and role

Expected regime:

- `FULL_2026`;
- NS 3600:2025;
- amended regulation;
- good-report false-positive control.

The report must be understood as structured ARKAT. Summary sections may support navigation and linked customer presentation but must not contaminate primary-point fields.

## 7.2 Mandatory accepted finding

### `A4-BMTF-01` – Limitation used as Risiko

**Point:** 7.4 – Bad – Dokumentasjon for våtrom  
**Page:** 12  
**TG:** TG2

**Frozen text:**

- Årsak: `Badet er oppført som egeninnsats uten fremlagt dokumentasjon.`
- Risiko: `Utførelse og materialvalg kan ikke dokumenteres.`
- Konsekvens: `Manglende dokumentasjon gir usikkerhet om oppbygning og utførelse, noe som kan vanskeliggjøre vurdering av teknisk kvalitet og eventuelle senere arbeider.`
- ANBEFALT TILTAK: `Ingen umiddelbare tiltak er nødvendig dersom badet fungerer uten tegn til skader eller lekkasjer. Ved fremtidige arbeider anbefales det å dokumentere utførelse og benyttede produkter.`

**Expected raw finding:**

- `field=risiko`;
- `error_type=LIMITATION_USED_AS_RISK_SUBSTITUTE`;
- rule `A_ARKAT_SEMANTIC.RISIKO.LIMITATION_USED_AS_RISK_SUBSTITUTE`.

**Deduction:** 3  
**96 gate:** not blocked

**Reason:**  
The Risiko field states only that documentation is unavailable. It does not state a possible technical defect, damage development or functional risk.

**Normalized customer item:**  
The report explains the documentation limitation and consequence, but the Risiko field should identify what technical uncertainty or hidden defect may exist because execution and materials cannot be verified.

## 7.3 Mandatory BMTF non-findings

Exactly one scored finding is expected.

The following shall pass:

- point 7.2 Årsak: the hidden sluk/membrane explanation performs a valid cause/rationale function;
- point 7.2 Risiko: the text names the hidden technical risk category and is not a pure limitation;
- point 20.2 Årsak: the loose/displaced slate explanation performs a valid rationale function;
- point 7.4 Årsak, Konsekvens and ANBEFALT TILTAK;
- bathroom ventilation point;
- room-below-ground ventilation point;
- all other reviewed primary points in the accepted 15-point semantic baseline;
- the TG3 cost class/estimate;
- legality/no-TG handling.

Prohibited findings include:

- missing consequence on 17.2 or 20.2 caused by backward field contamination;
- duplicate ANBEFALT TILTAK findings caused by forward spillover into 7.4, 8.1 or 11.4;
- treating point 7.2 Risiko as a pure limitation;
- treating valid observations/rationales in 7.2 or 20.2 as missing Årsak;
- any E-category or legality finding without a new approved variance;
- any missing TG2 measure finding where a semantic measure is present.

## 7.4 Hierarchical linkage controls

All 15 summary records must have unique hierarchy-compatible primary links.

Required examples:

- bathroom ventilation summary → `Bad – Installasjoner og ventilasjon`, section 7;
- room-below-ground ventilation summary → `Ventilasjon`, section 11.

A plausible near-tie within the governed ambiguity threshold must:

- remain unlinked;
- be recorded as `ambiguous`;
- create a completion blocker.

The system shall never choose a primary point merely because the title is similar when section hierarchy conflicts.

## 7.5 BMTF diagnostic-only inconsistency

The cover summary and detail inventory contain a known TG1-count inconsistency.

Required behaviour:

- preserve the source discrepancy;
- do not alter the physical point inventory to force the cover count;
- do not create a scored customer finding unless a specific permitted rule is approved.

## 7.6 BMTF expected result

| Item | Expected |
|---|---:|
| Accepted findings | 1 |
| Total deduction | 3 |
| Expected score | **97/100** |
| 96 gate | **not blocked** |
| State | `complete_with_findings` |

---

# 8. Cross-report acceptance assertions

A4 fails if any of the following occurs:

1. A backend accepted finding disappears before the customer payload.
2. A limited run is presented as complete.
3. A score is shown when `score_valid=false`.
4. “No findings” is shown after invariant suppression of a real finding.
5. The same defect is scored twice.
6. A summary creates an independent substantive finding without a valid primary-point basis.
7. A point is silently dropped during deduplication.
8. A provider-specific path changes the substantive rule outcome.
9. An ungoverned rule/type is emitted.
10. A high-risk finding is admitted with unresolved regime or ambiguous evidence.
11. OCR/text repair changes valid letters or reorders substantive point content.
12. The public payload exposes internal IDs, rule machinery or invariant details.

Repeated runs must produce stable:

- physical inventory;
- bindings;
- raw findings;
- accepted/rejected decisions;
- deductions;
- gate;
- normalized customer items.

---

# 9. Required customer-output structure

Each customer item shall include:

- point/section;
- tightly bounded report evidence;
- deficiency type in plain language;
- obligation class;
- why it matters;
- practical improvement guidance;
- deduction and gate effect.

Customer output shall not use unexplained internal taxonomy labels.

One customer item may combine multiple raw semantic errors at the same point where they describe one coherent underlying deficiency.

---

# 10. Blind Fremtind protocol

## 10.1 Sealing

Before the blind run:

- the developer shall not receive the report;
- the developer shall not receive the owner-side expected findings;
- no provider-specific substantive rule shall be added for the report;
- no test shall be tuned to its text.

## 10.2 Execution

The report is uploaded through the same A4 route after IVIT, Bolavi and BMTF acceptance.

The run must produce, without developer intervention:

1. document facts and regime;
2. physical inventory;
3. bindings;
4. governed retrieval;
5. raw findings and abstentions;
6. deterministic admission;
7. score/gate/state;
8. normalized customer result.

## 10.3 Evaluation

After the immutable run artifact is delivered:

- the owner-side sealed expectation is opened;
- every expected finding and non-finding is compared;
- unexpected findings are reviewed individually;
- no post-run adjustment may be counted as a blind pass.

The blind run passes only if it demonstrates provider-independent generalisation and no material false-positive or false-negative pattern.

---

# 11. Delivery package required from implementation

The A4 handoff shall contain:

- source hashes;
- runtime manifest;
- governed asset hashes;
- A2 physical inventories;
- A3 regime/retrieval outputs;
- A4 raw assessments;
- deterministic admission log;
- scoring/gate output;
- customer normalized output;
- final serialized public payload;
- frontend proof;
- test report;
- exact diff of any governed changes;
- variance report against this specification.

All three controlled runs must be fresh executions under the approved manifest. Redelivered old artifacts do not count.

---

# 12. Pass/fail summary

| Report | Required findings | Score | Gate | Required state |
|---|---:|---:|---|---|
| IVIT | 9 normalized customer items / governed raw findings as specified | 54 | blocked | complete_with_findings |
| Bolavi | 1 | 92 | blocked | complete_with_findings |
| BMTF | exactly 1 | 97 | not blocked | complete_with_findings |
| Fremtind | compared against sealed owner expectation | determined after blind run | determined after blind run | complete or honestly limited/unsupported |

A result does not pass merely because hashes, schemas and invariants are green. It must be substantively correct and reach the actual customer interface.

---

# 13. Authorization

This specification is frozen following product-owner approval and external hash-locking.

**Product-owner decision:** ☒ Approved for A4  ☐ Rejected  ☐ Approved with listed amendments  
**Name:** Vegard Ravna  
**Approval date:** 7 August 2026  
**Approval channel/reference:** ChatGPT conversation – explicit product-owner approval, 7 August 2026  
**Approval statement:** “Jeg godkjenner Validert Governed Regime Decision v2.0 og Validert A4 Acceptance Specification v2.0 uten endringer. Dokumentene kan fryses, hash-låses og sendes til Atul som styrende grunnlag for A3/A4.”  
**Approved SHA-256:** recorded in the accompanying `SHA256SUMS.txt` file

### Listed amendments

None. Approved without substantive changes.
