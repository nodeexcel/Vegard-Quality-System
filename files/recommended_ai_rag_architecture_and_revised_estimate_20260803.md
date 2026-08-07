# Recommended AI/RAG architecture and revised staged estimate

Hi Vegard,

I have reviewed the architecture note against the current implementation and the earlier 16–22 working-day plan. I agree with the proposed division of responsibility: AI should perform document understanding and semantic professional assessment; RAG should constrain that assessment to governed professional rules; deterministic code should control facts, regime resolution, schemas, validation, scoring, state, and safety.

My recommendation is to use the currently governed fallback chain as the foundation, extend it into a general evidence-bound AI/RAG analysis path, and selectively reuse safe components from the full analyzer. I do not recommend re-enabling the existing full analyzer as a complete route.

## 1. Recommended responsibility split
### AI responsibilities
- Extract candidate document facts, including provider, template, relevant dates, and declared standards.
- Identify document structure and segment the report into points and professional subjects.
- Bind relevant report text to components and assessment points.
- Evaluate Årsak, Risiko, Konsekvens, and Anbefalt tiltak semantically.
- Evaluate methodology and legality questions against retrieved governed rules.
- Return structured findings with exact report evidence and an abstention state where evidence is insufficient.

### RAG responsibilities
- Retrieve only the governed definitions, obligations, examples, finding types, and regime rules relevant to the current point and assessment category.
- Supply rule identity, source asset/version, permitted conclusion types, and required evidence conditions to the AI.
- Prevent the model from treating general model knowledge as an authorized Validert requirement.

### Deterministic responsibilities
- Read and validate PDF content.
- Validate extracted facts and preserve their source, confidence, and evidence.
- Resolve the governing regime separately for each rule category from signed regime logic.
- Validate schemas, evidence spans, point bindings, allowed finding types, and retrieved rule identities.
- Enforce higher evidence thresholds for methodology, legality, and regulatory findings.
- Deduplicate accepted findings and apply deterministic scoring.
- Control analysis state, invariants, leakage prevention, public projection, and fail-closed behavior.

Provider-specific mappings should remain available as precision and performance fast paths. If no fast path matches, readable reports should continue through the general AI/RAG path rather than being rejected solely because their layout is new.

## 2. Analyzer approach comparison
### Option 1 – Adapt and re-govern the existing full analyzer
Useful parts that can be reused:

- PDF normalization and preflight checks.
- Model invocation and audit capture.
- Existing governed-asset loaders.
- Detected-point validation.
- Post-processing utilities that have an identified governed basis.
- Feedback construction, scoring reconciliation, and output schemas.

Main concerns:

- The current route sends the whole report together with a broad prompt context to a single primary model call.
- The model can generate a broad analysis object before rule-catalogue and evidence validation are applied.
- Previously documented ungoverned finding generation and unsafe post-processing make the route unsuitable for direct reactivation.
- Re-governing the complete route would require identifying and proving every finding-generation and transformation path before it could safely become customer-facing.

This option offers reusable utilities, but adapting the full route as the foundation creates a larger safety and verification scope.

### Option 2 – Extend the governed fallback chain
Existing strengths:

- It already uses the signed analysis route and no-score customer boundary.
- Point extraction/binding and per-point semantic ARKAT evaluation already exist.
- Findings flow through structured feedback construction, governed-rule validation, evidence checks, and policy invariants.
- The public projection and leakage controls are already separated from canonical analysis.
- The chain has reproducible BMTF and Bolavi baselines that can be protected during development.

Required extensions:

- General AI-based document structure and point segmentation when a deterministic provider mapping is unavailable.
- Candidate extraction and deterministic validation of dates, declared standards, provider, and template facts.
- Rule-category-specific regime resolution from the signed regime decision document.
- Targeted RAG retrieval rather than supplying a broad undifferentiated prompt context.
- Governed AI/RAG methodology and legality evaluators.
- Explicit complete, limited, abstained, unsupported, and failed analysis states.

### Recommendation
I recommend **Option 2**.

The fallback chain provides the shortest safe route because its accepted governance, evidence, validation, invariant, and public-response boundaries can be retained. Safe utilities from the full analyzer should be extracted and reused behind those boundaries, but the existing full analyzer should not be re-enabled as-is and should not remain an independent source of customer findings.

This produces one substantive analysis architecture:

1. Provider-specific fast path where a signed mapping exists.
2. General AI document-understanding path where it does not.
3. A shared governed RAG and professional-assessment layer for both paths.
4. A shared deterministic acceptance, scoring, state, and safety layer.

## 3. Proposed end-to-end flow
1. Deterministically extract readable PDF text, pages, and source offsets.
2. Ask AI for candidate document facts and structure using a strict schema.
3. Validate each fact deterministically and retain its evidence, confidence, and status.
4. Resolve the applicable regime independently for each rule category using the signed governed regime decision.
5. Use a provider mapping when available; otherwise use AI segmentation and subject classification.
6. Retrieve the minimum relevant governed rules for each bound section.
7. Ask AI to assess only the permitted questions against those retrieved rules.
8. Require structured findings containing rule identity, finding type, point/section, report evidence, explanation, and proposed customer guidance.
9. Deterministically reject findings with invalid rules, unsupported types, inadequate evidence, or incorrect binding.
10. Apply deterministic deduplication, state, scoring, invariants, and safety checks.
11. Project accepted findings into the existing sanitized customer response.
12. Show the analysis scope and state clearly in the frontend.

The higher-risk methodology, legality, and regulatory evaluators will require a retrieved governed rule and direct report evidence. If either is missing or ambiguous, the evaluator must abstain rather than create a finding.

## 4. Existing components to preserve and reuse
The following foundation does not need to be rebuilt:

- PDF extraction and readability checks.
- Bedrock/model invocation and raw-call audit capture.
- Governed JSON asset loading and runtime provenance.
- Existing canonical point and provider mapping assets.
- Current per-point ARKAT semantic evaluator and its structured field results.
- Validated detected-point payload and evidence binding.
- Governed finding catalogue and deduction mappings.
- Legality rule assets and ARKAT legality templates.
- Feedback v11 construction.
- Deterministic policy invariants and incomplete-analysis safety behavior.
- Public-response sanitization and leakage scanning.
- Existing BMTF and Bolavi regression baselines.

The main new work is orchestration: general segmentation, targeted retrieval, rule-category regime resolution, methodology/legality assessment, deterministic finding admission, and explicit customer-facing analysis states.

## 5. Date and regime handling
I will not implement a universal switch based only on `befaringsdato`, `report_date`, or a declared NS version.

The system should first represent these as separate evidenced facts:

- inspection date;
- report/issue date;
- other relevant event dates;
- declared NS/version;
- provider and template generation.

The signed governed regime decision document must then specify, rule category by rule category:

- the controlling fact or date;
- transition boundaries;
- conflict resolution;
- behavior when a fact is missing or uncertain;
- whether a declared standard is consistent with the controlling regime.

A declared NS version is evidence only. If it conflicts with the governed regime, it must not override the regime and may become a governed finding.

Implementation of regime routing is therefore dependent on delivery and written approval of that decision document. Before it arrives, I can implement the fact-extraction schema, evidence capture, and validation interface, but not the substantive regime decisions.

## 6. Revised implementation stages
The estimate assumes eight working hours per day and five working days per week. It excludes waiting time for the signed regime decision and client-authored acceptance materials.

### Phase A – Core functional RAG path
#### A1. Architecture contract and regression harness — 1–1.5 working days
Deliverables:

- Freeze the existing signed BMTF/Bolavi baselines.
- Define schemas for extracted facts, segments, retrieval records, assessments, evidence, abstentions, and analysis states.
- Add end-to-end trace identifiers connecting source text, retrieved rule, raw assessment, accepted finding, and public result.

#### A2. General document understanding and fact validation — 2–3 working days
Deliverables:

- AI candidate extraction for dates, declared standards, provider/template, sections, points, and subjects.
- Deterministic source-offset, page, type, and confidence validation.
- Provider-specific mapping as an optional fast path with general AI segmentation as fallback.

#### A3. Governed regime resolver and targeted retrieval — 1.5–2.5 working days
Deliverables:

- Rule-category regime interface based on the signed regime decision document.
- Targeted retrieval over the existing governed assets with asset ID, version/hash, rule ID, and retrieval reason.
- Deterministic rejection of undeclared or inapplicable rules.

Dependency: the signed governed regime decision document is required to complete this stage.

#### A4. Evidence-bound professional assessment — 3–4 working days
Deliverables:

- General ARKAT evaluation using the retrieved governed definitions.
- Methodology and legality evaluators with stricter admission requirements.
- TG3-cost evaluation combining semantic assessment with deterministic enforcement.
- Permitted finding catalogue, strict structured output, abstention, evidence validation, and deduplication.

#### A5. Customer result and core acceptance readiness — 2–3 working days
Deliverables:

- Explicit complete, limited, abstained, unsupported, and failed states.
- Evidence-bound findings in the existing sanitized public projection.
- Customer presentation of point/section, evidence, deficiency, reason, obligation type, and improvement guidance.
- Upload-to-frontend trace test proving accepted findings are neither removed nor silently omitted.

**Phase A total: 12–14 working days**, approximately **2–3 working weeks**.

The first functional end-to-end version should be available for internal testing after A4, approximately **8–11 working days** after implementation begins, assuming the signed regime decision is available on time.

### Phase B – Provider-specific hardening and optimization
#### B1. Initial provider hardening — 4–6 working days
Priority order:

1. Current and historical BMTF/Eierskifterapport.
2. Bolavi.
3. Fremtind.
4. IVIT.
5. Norsk Takst.

Deliverables:

- Deterministic extraction and binding fast paths where they materially improve accuracy.
- Historical template profiles and aliases.
- Provider regression fixtures and negative template tests.
- Performance and token-use optimization.
- Edge-case handling without creating provider-specific professional rule engines.

#### B2. Product-owner acceptance and production stabilization — 2–3 working days
Deliverables:

- Run the client-selected and client-authored acceptance set through the actual product.
- Compare raw analysis and normalized customer results to the written expectations.
- Correct integration defects without changing approved professional decisions.
- Re-run signed BMTF/Bolavi baselines, safety invariants, and false-positive controls.

**Phase B initial total: 6–9 working days**.

Additional provider/template generations should normally require **1–3 working days each** after the shared core exists, depending on extraction quality and layout variation. They will extend the same core analyzer rather than introduce new substantive engines.

### Combined planning range
- Core functional RAG path: **12–14 working days**.
- Initial provider hardening and acceptance: **6–9 working days**.
- Combined initial program: **15–20 working days**.

The combined range is close to the previous overall estimate, but the sequencing is materially different: a general substantive product becomes testable after approximately **8–11 working days**, while provider-specific optimization continues afterwards.

## 7. Acceptance milestone
The first product acceptance milestone will use at least four reports selected by the product-owner side with written expected results prepared before execution:

1. A weak report with known ARKAT deficiencies.
2. A report with known methodology and/or legality deficiencies.
3. A good report expected to produce few or no findings, specifically testing false positives.
4. A previously unseen report, specifically testing generalization.

For each report, acceptance must cover:

- relevant facts and the applied regime per rule category;
- extracted segments and point/section binding;
- retrieved governed rules;
- raw structured assessments and abstentions;
- deterministically accepted/rejected findings;
- evidence in the source report;
- the normalized customer result;
- confirmation that accepted findings reached the actual frontend.

Infrastructure checks alone will not constitute acceptance. The customer-facing output must be substantively useful and agree with the pre-written expectations within the approved normalization rules.

## 8. Governed assets and approval boundaries
This architecture does not conflict with the existing governed JSON foundation. It changes how those assets are selected and supplied to AI; it does not authorize the AI to override them.

No governed JSON change is proposed at this planning stage. If implementation identifies a genuine gap, it will be presented separately with:

- a reproducible failing case;
- the affected rule category and asset;
- an exact before/after diff;
- technical and professional justification;
- regression impact;
- a written approval request before implementation.

Until this revised plan is approved in writing:

- no implementation work will begin;
- the current v46 production state will remain unchanged;
- no date/regime routing decision will be implemented before the signed governed regime document is delivered;
- the registered Model A harmonization track will remain unauthorized;
- the existing governed JSON assets and signed BMTF/Bolavi behavior will not be altered.

Please confirm whether this recommended foundation, Phase A scope, acceptance approach, and revised staged timeline are approved.
