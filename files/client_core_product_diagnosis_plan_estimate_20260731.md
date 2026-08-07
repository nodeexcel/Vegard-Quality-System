# Validert – concise diagnosis and implementation schedule

Hi,
I have reviewed the revised priority and understand that the next milestone is working substantive analysis across real report families—not another infrastructure or closure-package round.

## Diagnosis of the four production cases

### 1. Older Eierskifterapport (before 01.07.2026)
- PDF text extraction succeeded, so the document was readable.
- It did not match a currently signed template signature and was stopped by the verified-template gate before the analyzer ran.
- Consequently, no provider mapping, ruleset selection, governed semantic rules, ARKAT checks, methodology checks, or legality checks were executed.
- This was a routing/support failure, not a PDF-extraction failure.

### 2. Fremtind report (after 01.07.2026)
- PDF text extraction succeeded, so the document was readable.
- Fremtind is not in the current signed-template allow-list and was stopped before analysis.
- No substantive governed rules were executed and no findings reached the frontend.
- This was also a routing/template-support failure, not an extraction failure.

### 3. Bolavi report
- The report matched the signed Bolavi signature and completed the `local_postprocess_dommer_b_fallback` route.
- That route is a limited signed fallback, not the complete intended analyzer.
- Extraction, point binding, component/TG mapping, and the fallback ARKAT evaluation ran. The broader full-analysis path, including the complete methodology and legality review, did not run.
- Backend findings existed, but policy-invariant safe-stop removed canonical `feedback_v11` from the customer projection. The frontend therefore received components/TGs but no substantive findings to render.
- The frontend did not independently discard findings; the public response supplied no findings.

### 4. Current Eierskifterapport/BMTF
- The report matched the signed BMTF signature and ran through `local_postprocess_dommer_b_fallback`.
- It received the signed, limited fallback analysis rather than the complete intended product analysis.
- The fallback executed point extraction/binding and ARKAT semantic evaluation and produced the accepted 7.4 risk finding. Its policy invariants passed and that finding reached the customer response.
- The complete methodology and legality analysis was not executed, so the result remains narrower than the clarified product requirement.

## Cross-cutting causes
1. The production upload route currently treats the signed-template allow-list as the boundary for whether analysis may run. Unknown but readable templates are safe-stopped instead of being sent to a controlled general-analysis route.
2. Signed Bolavi and BMTF reports are routed explicitly to the limited fallback analyzer, not to the complete substantive analyzer.
3. The current regime logic detects a `report_date` and NS marker. It does not yet model `befaringsdato` as a separate, confidence-validated field that exclusively selects the substantive ruleset.
4. If canonical feedback is absent or suppressed following an invariant failure, the public projection contains no findings. The frontend correctly renders what it receives, but cannot explain that only a limited/incomplete analysis occurred.
5. The customer result does not yet expose explicit states for complete analysis, limited analysis, unsupported template, and failed extraction/analysis.

## Current support status
| Report family | Current state | Main gap |
|---|---|---|
| Current BMTF/Eierskifterapport | Signed limited fallback | Full ARKAT, methodology, and legality analysis |
| Bolavi | Signed limited fallback | Invariant-related feedback suppression and full substantive analysis |
| Older Eierskifterapport | Rejected before analysis | Historical template profile, mapping, and pre-01.07.2026 regime |
| Fremtind | Rejected before analysis | Provider profile/mapping and full analysis route |
| IVIT | Not customer-enabled | Provider verification, mapping, and regression corpus |
| Norsk Takst | Not customer-enabled | Provider verification, mapping, and regression corpus |
| Other readable templates | Safe-stop | Controlled general/limited analysis route |

## Shortest controlled implementation path

### Stage 1 – Reproducible fixtures and analysis-state contract
Deliverables:
- Preserve the four production documents as regression fixtures.
- Add explicit result states: complete with findings, complete without findings, limited, unsupported, and failed.
- Add layer-level tests covering extraction, routing, date detection, binding, governed-rule execution, public projection, and frontend rendering.

Estimate: **1.5–2 working days**.

### Stage 2 – Inspection-date regime selection and controlled routing
Deliverables:
- Extract `befaringsdato` independently of provider, template version, and report date.
- Record confidence/source and require resolution when the date is ambiguous.
- Select pre-01.07.2026 or post-01.07.2026 substantive rules from `befaringsdato`.
- Replace permanent template safe-stop with provider-specific analysis where supported and a clearly labelled general/limited route for other readable reports.

Estimate: **3–4 working days**.

### Stage 3 – Complete BMTF and Bolavi substantive analysis
Deliverables:
- Invoke the existing governed ARKAT, methodology, and legality assets against correctly bound report text.
- Resolve the Bolavi invariant/public-feedback loss without changing signed rule decisions.
- Ensure every backend customer finding reaches the public projection and frontend.
- Clearly prevent a limited run from being presented as a complete no-findings result.

Estimate: **3.5–5 working days**.

### Stage 4 – Older Eierskifterapport and Fremtind support
Deliverables:
- Add provider/template extraction profiles and mappings for the tested historical Eierskifterapport and Fremtind generations.
- Run the correct date-selected governed rules.
- Verify known Årsak, Risiko, Konsekvens, Anbefalt tiltak, TG3-cost, methodology, and legality deficiencies on real fixtures.

Estimate: **5–7 working days**.

### Stage 5 – Customer result and end-to-end acceptance
Deliverables:
- Present point/section, evidence, deficiency, reason, obligation type, and practical improvement for each finding.
- Present the analysis scope/status explicitly.
- Run fresh production acceptance tests for the four cases and regression tests for signed BMTF/Bolavi behavior.

Estimate: **3–4 working days**.

## Estimate for the immediate core milestone
Stages 1–5 total: approximately **16–22 working days**.

My availability is **8 hours per working day and 5 working days per week**. On that schedule, the immediate core milestone is expected to take approximately **3 weeks and 1 day to 4 weeks and 2 days**.

This schedule may be refined after the representative historical provider fixtures and expected outputs are confirmed.

## Remaining provider onboarding
After the core milestone, IVIT, Norsk Takst, and other template generations should be enabled one controlled family at a time. A reasonable initial allowance is **3–5 working days per provider/template generation**, depending on extraction and mapping differences. This can be refined after representative real PDFs and expected findings are available.

## Governed JSON assets
No governed JSON change is proposed or authorized at this diagnosis stage. The first implementation approach is to invoke the existing assets correctly with the correct bound text and date-selected regime.

If a reproducible test proves that a governed rule is genuinely missing or incorrect, I will provide separately, before implementation:

- the failing report and expected behavior;
- the affected JSON file and rule;
- an exact before/after diff;
- the technical/professional justification;
- the regression impact;
- an approval request.

No major development or governed JSON modification should begin until this staged plan and the acceptance fixtures are approved.
