# Validert – Governed Regime Decision

## Version 2.0 – approved and hash-locked

**Decision ID:** `VALIDERT-GRD-2026-02`  
**Prepared:** 7 August 2026  
**Status:** **APPROVED – IMPLEMENTATION-AUTHORISING SUBJECT TO THE CONTROLLED CHANGE REQUIREMENTS IN THIS DOCUMENT**  
**Product owner:** Vegard Ravna  
**Intended use:** Phase A3 regime resolution, Phase A4 governed retrieval/assessment, scoring, state and public projection

This document defines the governing product decisions for Validert. It controls which source facts determine each rule category, how regulatory requirements and standard methodology are classified, how conflicts are resolved, and how accepted findings may affect score, gate, state and customer output.

This document is separate from report-specific acceptance specifications.

It does **not**, by itself:

- change the active v46 production route;
- change active governed JSON assets;
- authorize Model A or other previously unapproved harmonisation tracks;
- authorize publication of the current limited/shadow runs;
- permit new finding types outside the governed catalogue without a controlled governance change.

---

# 1. Binding product principles

## 1.1 Obligation classes

Every finding shall be classified as one or more of:

1. **`regulatory`**  
   A concrete requirement stated in forskrift til avhendingslova or another applicable public-law rule.

2. **`standard_methodology`**  
   A requirement derived from the applicable edition of NS 3600 or another governed professional-methodology source.

3. **`validert_product_quality`**  
   A Validert quality or scoring requirement that may be stricter than the regulatory minimum.

A customer-facing result shall not describe a standard-methodology or Validert-quality deficiency as a direct regulatory breach unless an independent regulatory basis is established.

## 1.2 Governing precedence

The following precedence applies:

1. Applicable regulation.
2. Authoritative DiBK transition/interpretation guidance.
3. The lawfully applicable edition of NS 3600.
4. Signed product-owner decisions in this document.
5. Active governed Validert assets for taxonomy, scoring, gates, output and implementation.

Where an active governed asset conflicts with an express signed decision in this document, the conflict must be resolved through a controlled asset change before runtime implementation. No code component may silently override either source.

## 1.3 ARKAT model

Validert uses four semantic functions:

- **Årsak**
- **Risiko**
- **Konsekvens**
- **ANBEFALT TILTAK**

ARKAT functions are semantic roles, not mandatory headings.

Correctly linked text elsewhere within the same report point may satisfy a function. Generic boilerplate, summary language or text from another report point does not repair a missing function.

The following are not interchangeable:

- observation/present condition;
- cause;
- risk;
- consequence;
- limitation;
- recommended measure.

## 1.4 Provider independence

Provider or template identity may be used for:

- extraction optimisation;
- structural fast paths;
- audit and observability;
- performance and precision improvements.

Provider identity is not a prerequisite for substantive analysis when the uploaded report is readable and can be safely structured.

Unknown provider or new layout alone shall not produce `unsupported`, `limited` or safe-stop.

---

# 2. Architecture allocation

## 2.1 Deterministic responsibilities

Deterministic code controls:

- file acceptance and safety checks;
- source hashes and reproducibility;
- validated document facts;
- date and regime resolution;
- point inventory and source-span lineage;
- rule retrieval constraints;
- permitted finding admission;
- deduplication;
- score, category caps and gate;
- analysis state;
- public-payload containment;
- invariant enforcement.

## 2.2 AI responsibilities

AI controls:

- general document understanding;
- structural interpretation where deterministic parsing is insufficient;
- point and subsection understanding;
- semantic ARKAT assessment;
- professional-language interpretation;
- candidate-link proposals subject to deterministic acceptance.

## 2.3 RAG responsibilities

RAG constrains AI to:

- the applicable regulation;
- the applicable NS edition;
- signed product decisions;
- the permitted governed taxonomy;
- the correct scoring and output rules.

General model knowledge is not an authorised substitute for a retrieved governed rule.

---

# 3. Controlled source register

| Source | SHA-256 | Role |
|---|---|---|
| v46 read-only governance/source bundle | `72d0bd59d9cc79d5b78d21c85b7309bdbfe04d31c1a7e837862af438b68ddb86` | Active governance/runtime reference |
| NS 3600:2018 PDF | `3a8973d82ec794ea13b94be1e5b0b39877640d1db884c85834a8da7fa5ada4eb` | 2018 standard regime |
| NS 3600:2025 PDF | `9a501420c36678076b037be5815cf09ad49b807a947248014fb4d0a088e396ae` | 2025 standard regime |
| Base regulation PDF | `0de056c178886816d029ad34aa7ff45b0d4cea3957e2fe4a1960b6bba929af3b` | Regulation and historical wording |
| Amending regulation of 16.12.2025 | `58b4e4218ad0a0000f9f5245077eda00b3b8c7a9d8583fe57c8e87041c975b54` | Effective dates and transition |
| DiBK case 44816, 05.06.2026 | `475d78dee3a6f120d77aeb03dcf44bbb72d35261e7189b9a78afe24c5645c6cc` | Transition and continued report validity |

Key v46 governed assets remain hash-pinned by the verified source bundle. This decision does not rename or modify them directly.

---

# 4. Required document facts

Each uploaded report must preserve separately:

- source PDF SHA-256;
- extracted-text SHA-256;
- original inspection date;
- original report/issue date;
- revision/correction/reprint/export date;
- report status: original, corrected, revised, fully updated or uncertain;
- declared/used NS edition;
- provider/template candidate and verification status;
- exact source evidence, page and offsets for every fact.

A later print, export, administrative correction or minor revision does not replace the original inspection date or original report/issue date.

A genuinely new report after a new full inspection/review may have a new controlling report date.

No runtime component may collapse all date questions into a single universal date switch.

---

# 5. Regime resolution

## 5.1 Regime table

| Regime ID | Original report/issue date | Substantive regulation | Permitted TG-criteria edition |
|---|---|---|---|
| `PRE_AMENDMENT` | before 17.12.2025 | pre-amendment provisions | NS 3600:2018 / then-applicable framework |
| `TRANSITION_DEC_2025` | 17.12.2025–31.12.2025 | pre-01.01.2026 substantive provisions | NS 3600:2018 or NS 3600:2025 |
| `TRANSITION_2026` | 01.01.2026–30.06.2026 | amended substantive provisions | NS 3600:2018 or NS 3600:2025 |
| `FULL_2026` | from 01.07.2026 | amended substantive provisions | NS 3600:2025 only |

The direct reference to NS 3600:2025 in § 2-23 entered into force on 17.12.2025. The other amendments entered into force on 01.01.2026. The transition provision allowed use of NS 3600:2018 criteria for selection of TG until 01.07.2026.

## 5.2 Applicable edition during transition

During `TRANSITION_DEC_2025` and `TRANSITION_2026`:

- the report may lawfully use NS 3600:2018 or NS 3600:2025 TG criteria;
- the declared edition is evidence but must be checked against report structure and content;
- edition-specific methodology follows the lawfully used edition;
- amended regulatory provisions effective from 01.01.2026 apply independently of which TG-criteria edition is used.

From 01.07.2026, NS 3600:2025 is mandatory for TG selection. A conflicting declaration of NS 3600:2018 is a `regime_conflict` diagnostic and shall not silently alter the applicable regime.

## 5.3 Missing or ambiguous edition

If the edition cannot be resolved during a transition period:

- regulation-independent checks may continue;
- Validert product-quality checks may continue where their applicability does not depend on edition;
- edition-specific TG, age and methodology findings shall abstain;
- the analysis state shall disclose the limitation if material.

## 5.4 Report validity

- One-year validity is measured using the controlling report/issue date when the buyer becomes bound.
- A valid report prepared before 01.07.2026 using NS 3600:2018 may remain usable after 01.07.2026 until it reaches one year.
- Template wording that calculates validity from inspection date is diagnostic only until a specific permitted scored finding is approved.

---

# 6. TG and ARKAT decisions

## 6.1 TG0 and TG1

No automatic ARKAT requirement applies solely because a point is TG0 or TG1.

A separate regulatory or methodology rule may still apply.

## 6.2 TG2 – Årsak and Konsekvens

For TG2:

- Årsak and Konsekvens are required by the applicable regulatory/standard framework.
- Correctly linked point text may satisfy them.
- A finding must use the exact permitted semantic error type.
- A present observation does not automatically satisfy Årsak.
- A technical process that stops before an actual effect may be insufficient as Konsekvens.

## 6.3 TG2 – Risiko

Risiko is a mandatory Validert product-quality function for TG2.

A pure limitation such as “cannot be documented” or “could not be inspected” does not satisfy Risiko unless it names the hidden technical risk or possible damage development.

Missing or semantically invalid Risiko uses the existing governed semantic taxonomy and scoring mapping.

## 6.4 TG2 – ANBEFALT TILTAK

### Reports lawfully using NS 3600:2018

For this release baseline:

- absence of a semantic ANBEFALT TILTAK shall not create a separate Validert finding or deduction;
- existing tiltak text may still be assessed where another permitted rule applies;
- this is an explicit product-scope decision and is not a declaration that NS 3600:2018 contained no methodology requirement concerning measures.

### Reports lawfully using NS 3600:2025

A semantic recommended/necessary measure is required for TG2.

If it is completely absent:

- register one governed finding;
- obligation class: `standard_methodology`;
- deduction: **3 points**;
- the absence alone does **not** block the 96 gate.

Applicability follows the lawfully used NS edition, not report date alone during the transition period.

Thus:

- a June 2026 report using NS 3600:2018 receives no separate missing-measure finding;
- a June 2026 report using NS 3600:2025 is subject to the 3-point rule;
- reports from 01.07.2026 are always subject to the rule.

A heading is not required. Semantically sufficient text may appear in a combined field or clearly linked free text.

Text that merely repeats the observation, risk or consequence does not satisfy the measure function.

NS 3600:2025 also requires proposed measures to be as clear and concrete as possible. Vague but real text must use a permitted form-error type; it must not be converted automatically into full absence.

## 6.5 TG3 ARKAT

TG3 requires:

- Årsak;
- Risiko;
- Konsekvens;
- ANBEFALT TILTAK;
- point-bound schematic cost estimate.

A TG3 measure may appear in a dedicated field, combined field or clearly linked point text.

Complete absence uses the exact permitted missing-measure type. A vague real measure uses the exact permitted catalogue type.

## 6.6 TG3 schematic cost

Every TG3 point requires a point-bound:

- cost class;
- interval;
- or other clearly schematic estimate accepted by the governed rule.

Generic cost guidance elsewhere in the report does not satisfy a TG3 point.

Locked scoring:

- missing point-bound TG3 cost: **8 points**;
- one unsupported single amount without class/interval: **4 points**, where the existing governed rule applies;
- missing TG3 cost blocks the 96 gate in a complete analysis.

---

# 7. TGIU

Only the dedicated TGIU taxonomy applies to a TGIU point.

Every TGIU point must preserve and assess:

- what was not investigated;
- why it was not investigated;
- relevant consequence/risk of the limitation where required;
- a technically relevant recommendation for further investigation or follow-up;
- moisture-risk status where the construction is a real moisture-risk construction;
- crawlspace-specific requirements where applicable.

A recommendation may consist of:

- inspection from another side;
- alternative measurement/investigation;
- specialist assessment;
- obtaining documentation;
- investigation during later opening/intervention;
- a concrete monitoring plan.

Ordinary TG2/TG3 ARKAT findings shall not duplicate the same TGIU defect.

A solid construction or unavailable ordinary method does not automatically eliminate the need for relevant follow-up; however, the recommended follow-up must be technically possible and proportionate.

---

# 8. TG-forbidden and special areas

## 8.1 Electrical installation

From 01.01.2026:

- no TG shall be assigned to the electrical installation;
- ordinary TG2/TG3 ARKAT logic is suppressed;
- the complete electrical section must be evaluated;
- correctly linked consequence and control guidance within that section may satisfy the requirement;
- unrelated generic disclaimers do not.

Before 01.01.2026, assessment follows the then-applicable methodology. Equivalent deficiencies must not be labelled regulatory unless the regulation directly supports that classification.

## 8.2 Legality and HMS

From 01.01.2026:

- no TG shall be assigned to matters governed by the amended no-TG provisions;
- identified deviations/errors require explained consequence;
- high-risk findings require direct evidence, resolved regime and a permitted rule ID;
- linked explanatory text within the same legality/HMS module may satisfy consequence.

The no-TG rule must not be overgeneralised to exceptions that retain TG treatment, including governed rules for:

- takstiger/snøfangere;
- ildsted/skorstein;
- specified structural/statics matters.

## 8.3 Wet-room hole drilling

Where required drilling is omitted:

- preserve the report’s reason;
- determine whether the reason is permitted;
- verify the prescribed alternative moisture control where applicable;
- do not treat alternative measurement alone as proof that omission was permitted;
- emit a governed abstention/gap where no exact permitted finding type exists.

## 8.4 Area measurement

Reports under the amended 2026 regime are assessed against the applicable NS 3940:2023/BRA presentation requirements.

Do not trigger solely because the year is absent from generic wording when the actual area tables use the correct method.

Stale boilerplate does not override the actual measurement module.

## 8.5 Detached buildings

Absence of assessment alone is neutral where a detached building is non-mandatory.

Once a report chooses to assess and describe concrete deviations:

- the assessment must not mislead;
- governed optional-assessed/methodology rules apply;
- concrete deviations without required explanatory structure may trigger the permitted building-level methodology finding;
- no TG is required solely because the structure is a garage, shed, boathouse or similar.

---

# 9. Evidence and finding admission

Minimum admission basis:

| Finding class | Minimum evidence |
|---|---|
| ARKAT semantic | complete point body, exact spans, permitted field/error type |
| TGIU | complete TGIU point, exact evidence, permitted TGIU type |
| Standard methodology | direct point/section evidence plus retrieved methodology rule |
| Regulatory/legal | direct evidence, resolved regime, permitted rule ID, correct obligation class |
| TG3 cost | exact TG3 binding and complete search for linked cost evidence |
| Date/edition conflict | evidenced dates/edition and governed conflict behaviour |

If evidence is incomplete or binding is ambiguous:

- high-risk findings abstain;
- the model may not invent a requirement;
- the material limitation must affect analysis state.

Every candidate point must be:

- admitted;
- explicitly merged with source trace;
- or abstained with a specific reason.

Silent disappearance is forbidden.

---

# 10. Deduplication, scoring and 96 gate

## 10.1 One underlying defect

One underlying deficiency may create multiple raw semantic flags where necessary for audit, but customer output shall normally contain one coherent item per underlying defect/point.

The same defect shall not be scored twice through:

- semantic and legacy pipelines;
- ARKAT and TGIU;
- summary and primary point;
- parent and child point;
- internal and customer projection.

## 10.2 Gate decisions

The following block the 96 gate in a complete analysis:

- missing required TG2/TG3 Årsak, Risiko or Konsekvens where the active gate rule applies;
- missing point-bound TG3 cost;
- other expressly governed blocking findings.

Missing ANBEFALT TILTAK on TG2 under NS 3600:2025, standing alone, does **not** block the 96 gate.

## 10.3 Existing mappings

Except for decisions expressly changed in this document, existing v46:

- deductions;
- category caps;
- severity;
- gate behaviour;
- output mapping

remain controlling until separately changed through governed versioning.

---

# 11. Analysis state

Supported public states:

- `complete_with_findings`
- `complete_no_findings`
- `limited`
- `unsupported`
- `failed`

A complete state requires:

- complete authorised point coverage;
- resolved required regimes;
- no material untraced dropped point;
- valid scoring;
- successful public projection.

For `limited`, `unsupported` or `failed`:

- `score_valid` must be false;
- no normal score may be shown;
- no “Ingen scoretrekk” or equivalent complete result may be shown;
- preliminary internal findings must not be presented as a complete analysis.

An invariant failure that prevents safe publication must never produce `complete_no_findings`.

For safe-stop, the customer message remains the governed canonical message and internal rule/finding evidence must remain withheld from the public payload.

---

# 12. Customer projection

Every admitted customer finding must preserve traceability through:

1. source point and evidence;
2. raw semantic/methodology assessment;
3. deterministic admission;
4. scoring/gate;
5. normalized customer result;
6. final serialized public payload;
7. frontend rendering.

Customer output must state:

- where the issue appears;
- what is missing or incorrect;
- the correct obligation class;
- why it matters;
- how the report could be improved;
- deduction and gate effect where applicable.

Internal IDs and implementation jargon shall not appear as unexplained customer labels.

---

# 13. Explicit conflict resolutions

| Conflict | Binding resolution |
|---|---|
| v46 blanket rule says missing TG2 ANBEFALT TILTAK never scores | Superseded conditionally: NS 3600:2018 remains neutral in this release baseline; NS 3600:2025 gets one 3-point methodology finding, no gate block alone |
| Date-only switch during transition | Incorrect; applicable edition plus report date controls edition-specific methodology |
| Report declares NS 3600:2018 after 01.07.2026 | Apply NS 3600:2025 and record regime conflict |
| 2018 TG criteria continue after 01.01.2026 for electrical/HMS | They do not displace amended substantive no-TG rules effective 01.01.2026 |
| Generic summary text repairs a point | Only where deterministic linkage proves it belongs to that point and the text semantically satisfies the function |
| Same defect found by semantic and legacy path | Semantic governed result is authoritative; duplicate suppressed |
| Unknown provider triggers safe-stop | Prohibited where the document remains readable and safe to structure |
| Finding type absent from catalogue | Abstention/governance-gap; no invented rule |

---

# 14. Required controlled governance change

Before implementation of the NS 3600:2025 TG2 missing-measure decision:

1. add or activate an exact permitted applicability rule for TG2 + NS 3600:2025;
2. bind it to the existing permitted missing `anbefalt_tiltak` semantic type, or create a separately approved type if no exact type exists;
3. map the deduction to 3 points;
4. set `blocks_96_gate = false`;
5. explicitly exclude NS 3600:2018;
6. add transition tests for:
   - June 2026 + NS 3600:2018 → no finding;
   - June 2026 + NS 3600:2025 → finding, 3 points;
   - July 2026 + NS 3600:2025 → finding, 3 points;
   - missing edition during transition → abstain from edition-specific measure finding;
7. provide exact before/after governed-asset diff and regression impact;
8. obtain product-owner approval before merge.

No silent code-only override is authorised.

---

# 15. A3 implementation contract

For each rule category A3 shall return:

- `rule_category_id`;
- `status`: resolved, abstained, conflict or pending governed decision;
- `obligation_class`;
- controlling fact and evidence ID;
- regime ID;
- applicable NS edition;
- retrieved asset ID/path and hash;
- retrieved rule ID;
- retrieval reason;
- excluded alternatives and reasons;
- uncertainty/conflict detail.

A3 shall not return `resolved` when a required controlling fact is missing or ambiguous for a date/edition-dependent high-risk rule.

---

# 16. Authorization

This decision is implementation-authorising following product-owner approval and external hash-locking.

**Product-owner decision:** ☒ Approved  ☐ Rejected  ☐ Approved with listed amendments  
**Name:** Vegard Ravna  
**Approval date:** 7 August 2026  
**Approval channel/reference:** ChatGPT conversation – explicit product-owner approval, 7 August 2026  
**Approval statement:** “Jeg godkjenner Validert Governed Regime Decision v2.0 og Validert A4 Acceptance Specification v2.0 uten endringer. Dokumentene kan fryses, hash-låses og sendes til Atul som styrende grunnlag for A3/A4.”  
**Approved SHA-256:** recorded in the accompanying `SHA256SUMS.txt` file

### Listed amendments

None. Approved without substantive changes.
