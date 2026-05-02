# Revision Specification: ARKAT Semantic Pipeline (Dommer B)

**Target files:**
- `arkat_semantic_rules.json` (currently v1.1.0 — will become v1.2.0)
- `arkat_evaluation_pipeline_step.json` (currently v1.0.0 — will become v1.1.0)

**Purpose:** Align Dommer B with the new ARKAT error-to-deduction mapping file, add missing error types, restructure TGIU handling, and clean up internal inconsistencies flagged during review.

**Companion file:** `arkat_error_to_deduction_mapping_v1.0.json` — the mapping table is the source of truth for valid error types and their deductions.

---

## Section A: Error type catalog — the authoritative list

The following 21 error types are the complete set that Dommer B must return. Every error type in Dommer B output must match one of these exactly. No other error types should be emitted.

### A.1 Konsekvens field (3 types)
- `MISSING (konsekvens)` — field entirely absent
- `TECHNICAL_DEVELOPMENT_AS_KONSEKVENS` — describes building processes without buyer relevance
- `PURE_DUPLICATION` — konsekvens is word-for-word identical to risiko, no buyer dimension added **(new — add to Dommer B)**

### A.2 Årsak field (3 types)
- `MISSING (aarsak)` — field entirely absent
- `OBSERVATION_AS_AARSAK` — describes what was seen, not why it occurred
- `RISK_AS_AARSAK` — describes future risk, not cause

### A.3 Risiko field (6 types)
- `MISSING (risiko)` — field entirely absent
- `CONSEQUENCE_AS_RISIKO` — contains buyer consequence, not building risk
- `PRESENT_STATE_AS_RISIKO` — describes current condition, not future development
- `LIMITATION_AS_RISIKO` — describes inspection limitation, not actual risk
- `LIMITATION_USED_AS_RISK_SUBSTITUTE` — entire field is a limitation with no risk
- `AARSAK_AS_RISIKO` — contains cause instead of future risk **(new — add to Dommer B)**

### A.4 Anbefalt tiltak field (5 types)
- `MISSING (anbefalt_tiltak)` — field entirely absent. **Fires only on TG3.**
- `EXPLANATION_AS_TILTAK` — explains cause or repeats risk instead of pointing to next step
- `CONSEQUENCE_AS_TILTAK` — describes buyer consequence instead of action guidance **(new — add to Dommer B)**
- `TILTAK_IMPERATIVE_FORM` — formulated as directive/specification instead of recommendation **(new — move from Dommer A)**
- `TILTAK_VAGUE_WITHOUT_NECESSITY` — vague wording that doesn't convey necessity at TG3 **(new — move from Dommer A)**

### A.5 TGIU-specific (4 types)
- `TGIU_MISSING_REASON` — TGIU given without explanation of why inspection was not possible **(new)**
- `TGIU_MISSING_FURTHER_INVESTIGATION` — TGIU without recommendation of further investigation **(new)**
- `TGIU_MISSING_MOISTURE_FLAG` — TGIU on moisture-risk building part without explicit risk assessment **(new)**
- `TGIU_CRAWLSPACE_MISSING_RISK_CONSEQUENCE` — crawlspace TGIU without risk/consequence per § 2-14 third paragraph **(new)**

---

## Section B: Changes to `arkat_semantic_rules.json`

### B.1 Update meta

Change version from `1.1.0` to `1.2.0`. Update description to reflect v1.2.0 changes: added missing error types (PURE_DUPLICATION, AARSAK_AS_RISIKO, CONSEQUENCE_AS_TILTAK, TILTAK_IMPERATIVE_FORM, TILTAK_VAGUE_WITHOUT_NECESSITY), added TGIU-specific error taxonomy, and aligned field_definitions with pipeline_step and scoring_bridge.

### B.2 Field definitions — konsekvens

Update `error_type_if_wrong` to be the complete list for konsekvens:
```
"error_type_if_wrong": "MISSING (konsekvens), TECHNICAL_DEVELOPMENT_AS_KONSEKVENS, or PURE_DUPLICATION"
```

Add a new detection guidance block for PURE_DUPLICATION:
```
"pure_duplication_detection_nb": "Hvis konsekvens-teksten er ordrett identisk med risiko-teksten, eller en nesten-identisk omformulering med samme kjernebetydning og uten å legge til informasjon om hva dette betyr for kjøper konkret, skal dette klassifiseres som PURE_DUPLICATION og ikke som TECHNICAL_DEVELOPMENT_AS_KONSEKVENS."
```

### B.3 Field definitions — risiko

Update `error_type_if_wrong` to include all six risiko-feiltypene:
```
"error_type_if_wrong": "MISSING (risiko), CONSEQUENCE_AS_RISIKO, LIMITATION_AS_RISIKO, PRESENT_STATE_AS_RISIKO, LIMITATION_USED_AS_RISK_SUBSTITUTE, or AARSAK_AS_RISIKO"
```

Add an error_signature entry for AARSAK_AS_RISIKO:
```
"Manglende vedlikehold har ført til [past tense causal explanation]",
"Dreneringen er utett fra byggeår [cause stated as risk]"
```

### B.4 Field definitions — anbefalt_tiltak

Update `error_type_if_wrong`:
```
"error_type_if_wrong": "MISSING (anbefalt_tiltak) [TG3 only], EXPLANATION_AS_TILTAK, CONSEQUENCE_AS_TILTAK, TILTAK_IMPERATIVE_FORM, or TILTAK_VAGUE_WITHOUT_NECESSITY"
```

Add detection patterns for the three new error types:
```
"tiltak_imperative_form_detection_nb": "Tiltak formulert som pålegg eller prosjekterende instruksjon. Kjennetegn: 'Skal utbedres...', 'Må utføres i henhold til...', 'Det kreves at...'. Korrekt form er anbefaling: 'Det anbefales å...', 'Bør utbedres...', 'Videre undersøkelser anbefales...'",

"tiltak_vague_without_necessity_detection_nb": "Tiltak er vagt formulert ved TG3 uten å gjøre det klart at utbedring er nødvendig. Kjennetegn: 'Kan vurderes...', 'Eventuelt kan...', 'Kan på sikt...'. Ved TG3 kreves mer forpliktende anbefaling om nødvendig handling.",

"consequence_as_tiltak_detection_nb": "Tiltak-feltet beskriver kjøperkonsekvens (kostnader, brukspåvirkning) i stedet for konkret handling. Kjennetegn: 'Må påregne kostnader...', 'Kjøper må være oppmerksom...', 'Forholdet vil medføre...'"
```

### B.5 Add new section: tgiu_field_definitions

Insert a new top-level section after `field_definitions`:

```json
"tgiu_field_definitions": {
  "_description_nb": "Evaluering av TGIU-punkter. TGIU-punkter skal IKKE evalueres mot feltdefinisjonene for aarsak/risiko/konsekvens/anbefalt_tiltak — takstmannen har ikke grunnlag for å vurdere disse feltene for en bygningsdel som ikke er undersøkt. TGIU-punkter skal kun evalueres mot de fire TGIU-spesifikke feiltypene definert under.",
  "_legal_basis_nb": "NS 3600:2018/2025 punkt 12.1 (alle TGIU skal begrunnes) og 13.1 siste avsnitt (for alle TGIU anbefales ytterligere undersøkelser). Kravene er identiske i begge NS-versjoner. Forskrift til avhendingsloven § 2-14 tredje ledd (gjeldende fra 01.01.2026) gir særskilt 'bør'-krav for krypkjeller.",
  
  "TGIU_MISSING_REASON": {
    "description_nb": "TGIU gitt uten tilstrekkelig begrunnelse av hvorfor bygningsdelen ikke var mulig å undersøke.",
    "correct_content_nb": "Forklaring av hvorfor inspeksjon ikke var mulig: ingen inspeksjonsluke, luke fastskrudd/låst, tak tildekt med snø, lukket konstruksjon uten destruktive inngrep, etc.",
    "error_signatures_nb": [
      "Kun 'lukket, ingen tilkomst' uten forklaring av hvorfor",
      "Kun 'ikke undersøkt' uten kontekst",
      "Ren konstatering av tilstand uten begrunnelse"
    ]
  },
  
  "TGIU_MISSING_FURTHER_INVESTIGATION": {
    "description_nb": "TGIU gitt uten anbefaling om ytterligere undersøkelser.",
    "correct_content_nb": "Eksplisitt anbefaling: 'Det anbefales å undersøke...', 'Ytterligere kontroll bør foretas av...', 'Det anbefales at luken åpnes ved overtakelse for å vurdere tilstand.'",
    "error_signatures_nb": [
      "TGIU-punkt uten noen form for anbefaling om videre undersøkelser",
      "Kun beskrivelse av hva som ikke ble undersøkt, uten veiledning videre"
    ]
  },
  
  "TGIU_MISSING_MOISTURE_FLAG": {
    "description_nb": "Bygningsdel som er eller åpenbart kan være særlig fuktutsatt er gitt TGIU uten at fuktrisiko er tatt stilling til.",
    "applies_to_building_parts_nb": [
      "krypkjeller", "rom under terreng", "yttertak med eldre tekking", "våtrom", "innvendig isolerte vegger under terreng"
    ],
    "correct_content_nb": "Eksplisitt vurdering: 'Konstruksjonen anses som særlig fuktutsatt grunnet...', 'Ved eldre bolig med denne konstruksjonen er det økt risiko for fuktskader...'",
    "note_nb": "Skal kun fyres når bygningsdelen er reell risikokonstruksjon. Ikke fyres på f.eks. loftkonstruksjon i nyere bolig eller på bygningsdeler uten iboende fuktrisiko."
  },
  
  "TGIU_CRAWLSPACE_MISSING_RISK_CONSEQUENCE": {
    "description_nb": "Krypkjeller gitt TGIU uten at skaderisiko og konsekvens av manglende inspeksjon er omtalt.",
    "legal_basis_nb": "Forskrift § 2-14 tredje ledd: 'Den bygningssakkyndige bør i tilfelle også opplyse om skaderisiko og konsekvens av at krypkjelleren ikke er mulig å undersøke.'",
    "applies_only_to_nb": "Skal kun fyres på krypkjeller-punkter (ikke andre TGIU-bygningsdeler).",
    "correct_content_nb": "Omtale av hva skaderisiko og konsekvens er ved manglende inspeksjon av krypkjeller, f.eks. 'Manglende inspeksjon medfører at eventuelle fuktskader, råte eller skadedyr ikke kan avdekkes. Kjøper må påregne videre undersøkelser ved overtakelse.'"
  }
}
```

### B.6 Update scoring_integration

Replace the entire `scoring_integration` block with:

```json
"scoring_integration": {
  "_note_nb": "Denne filen er ikke lenger eneansvarlig for feil-til-straff-mapping. Mapping av feiltyper til poengtrekk er flyttet til den separate filen arkat_error_to_deduction_mapping_v1.0.json. Dommer B returnerer strukturerte feilklassifiseringer; scoring-laget slår opp poengverdier i mapping-tabellen.",
  "source_of_truth": "arkat_error_to_deduction_mapping_v1.0.json",
  "integration_contract": "See 'integration_contract' section in the mapping file for input/output schema."
}
```

### B.7 Remove removed_error_type block

The block `removed_error_type.RISIKO_AS_KONSEKVENS` can be kept as a comment for historical reference, but move it under a new `deprecated_error_types` section with a note explaining it was removed in v1.1.0 and must not be used.

---

## Section C: Changes to `arkat_evaluation_pipeline_step.json`

### C.1 Update meta

Change version from `1.0.0` to `1.1.0`. Update description:
```
"description": "Defines the discrete pipeline step for semantic ARKAT field evaluation. Runs as a separate LLM call on TG2, TG3, and TGIU points. v1.1.0 changes: aligned error taxonomy with arkat_semantic_rules v1.2.0 and arkat_error_to_deduction_mapping v1.0; separated TGIU evaluation logic from TG2/TG3; standardized MISSING output format; removed ambiguous cross-category deduplication rule."
```

### C.2 Restructure step_trigger

Replace current step_trigger with:

```json
"step_trigger": {
  "runs_on": ["TG2", "TG3", "TGIU"],
  "runs_on_each": "individual point — one call per point, not one call per report",
  "evaluation_mode": {
    "TG2_TG3": "Full ARKAT field evaluation (aarsak, risiko, konsekvens, anbefalt_tiltak)",
    "TGIU": "TGIU-specific evaluation only. Do NOT evaluate aarsak/risiko/konsekvens/anbefalt_tiltak as MISSING for TGIU points — these fields are not required by regulation when the building part has not been inspected."
  },
  "tg2_anbefalt_tiltak_rule_version_dependent": {
    "_description": "The tiltak requirement for TG2 depends on which NS version the report follows. The ns_version input field must be used to determine the correct rule.",
    "NS3600:2018_TG2": "anbefalt_tiltak is NOT required. If absent, output status = NOT_APPLICABLE (not MISSING, not CORRECT). If present, evaluate for form errors only (EXPLANATION_AS_TILTAK, CONSEQUENCE_AS_TILTAK, TILTAK_IMPERATIVE_FORM). Do NOT fire TILTAK_VAGUE_WITHOUT_NECESSITY.",
    "NS3600:2025_TG2": "anbefalt_tiltak IS required (per NS 3600:2025 punkt 13). If absent, fire MISSING (anbefalt_tiltak). If present, evaluate for form errors (EXPLANATION_AS_TILTAK, CONSEQUENCE_AS_TILTAK, TILTAK_IMPERATIVE_FORM). Do NOT fire TILTAK_VAGUE_WITHOUT_NECESSITY — vague tiltak at TG2 are acceptable under 2025 standard because TG2 allows 'tiltak in nær fremtid'.",
    "TG3_all_versions": "anbefalt_tiltak is required. All five error types can fire, including TILTAK_VAGUE_WITHOUT_NECESSITY."
  },
  "skip_conditions": [
    "TG0 or TG1 points",
    "Points with no descriptive text (label only)"
  ]
}
```

### C.2b Expand input_schema to include report_context

The moisture_flag logic in the revised system prompt requires context beyond the point text itself (e.g. building year, building method, whether ventilation is discussed elsewhere in the report). Extend the input_schema to include a new required field `report_context`:

```json
"input_schema": {
  "description": "What must be passed into the evaluation call for each point",
  "fields": {
    "point_id": "string — e.g. '1.1', '7.1.3', '10.5'",
    "point_label": "string — e.g. 'Byggegrunn, fundamenter, grunnmur, drenering'",
    "tg_grade": "string — 'TG2' | 'TG3' | 'TGIU'",
    "report_format": "string — 'structured_arkat' | 'compressed_mixed' | 'unlabeled_prose'",
    "ns_version": "string — 'NS3600:2018' | 'NS3600:2025'",
    "raw_point_text": "string — the complete text of the point as extracted from the report",
    "extracted_fields": {
      "aarsak": "string",
      "risiko": "string",
      "konsekvens": "string",
      "anbefalt_tiltak": "string"
    },
    "report_context": {
      "description": "Contextual information from the report needed for moisture_flag evaluation and other rules that depend on context outside the point itself.",
      "building_year": "integer | null — year the building was constructed",
      "dwelling_type": "string — e.g. 'enebolig', 'rekkehus', 'leilighet', 'fritidsbolig'",
      "building_method_summary": "string — short summary from the report's 'Om byggemetoden' section or equivalent, describing construction method, materials, roof type",
      "relevant_component_context": "string — point-specific contextual summary extracted from elsewhere in the report. Should include where relevant: roof type and cladding, alleged/stated age of roofing, whether undertak is known or unknown, whether ventilation is discussed elsewhere, whether the construction is partially below terrain, whether the point concerns a våtrom/krypkjeller/loft"
    }
  }
}
```

The `report_context.relevant_component_context` field is the most important addition. Without it, Dommer B's moisture_flag logic will be unreliable because several indicators require knowledge from outside the point text itself.

**Pipeline responsibility:** The pipeline step that prepares Dommer B input must populate `report_context` by reading the report's introductory sections (OM BYGGEMETODEN, BEFARINGEN, etc.) and the related points within the same component group (e.g. for point 5.1 Loft, the pipeline should include context from point 4.1 and 4.2 Tak). This extraction can be done by a lightweight LLM call or by deterministic pattern matching — implementation is at the developer's discretion.

### C.2c NS version detection — pipeline responsibility

The `ns_version` input field is critical for correct evaluation of tiltak requirements at TG2. The pipeline step that calls Dommer B must determine which NS version the report uses before calling Dommer B, and pass the value as an input field.

**Detection rules (in priority order):**

1. **Explicit statement in report:** If the report contains an explicit statement about which NS version is used (e.g. "For valg av tilstandsgrad blir NS 3600:2018 lagt til grunn" or "Som del av en overgangsordning benyttes NS 3600:2018" or "For valg av tilstandsgrad gjelder de kriteriene som fremgår av NS 3600:2025"), use that version. This is the most reliable signal because takstmannen explicitly declares which standard is followed.

2. **Report date fallback:** If no explicit statement is found, use the report's creation date (rapportdato):
   - rapportdato < 2025-07-01 → NS3600:2018
   - rapportdato >= 2025-07-01 → NS3600:2025

3. **Transitional period note:** During the transitional period from 2025-07-01 to 2026-07-01, takstmenn may choose to use NS3600:2018 even on reports dated after 2025-07-01. The explicit statement (rule 1) always overrides the date (rule 2) during this period.

4. **After 2026-07-01:** NS3600:2025 is mandatory. If a report dated after this date explicitly states it uses NS3600:2018, log a warning but still respect the declared version — it is takstmannen's stated standard.

**Implementation note:** The pipeline can use simple string matching on the report's opening sections for the explicit statement detection. Common phrases to match:
- "NS 3600:2018" or "NS3600:2018"  
- "NS 3600:2025" or "NS3600:2025"
- "overgangsordning" (indicates 2018 is being used under transitional rules)

If neither version string is found and no date is available, default to NS3600:2018 (the safer choice that applies less strict rules).

### C.3 Update system_prompt

Replace the `system_prompt.content` with the full revised system prompt provided in companion file `dommer_b_system_prompt_v6.md`.

The revised prompt:
1. Separates TG2/TG3 evaluation from TGIU evaluation at the top level (evaluation mode selection)
2. Defines all 21 error types from Section A with signatures and examples
3. Places TGIU as its own evaluation module with four independent checks
4. Uses the four-status model (CORRECT, WRONG, MISSING, NOT_APPLICABLE) consistent with the output_schema in C.4
5. States explicitly that MISSING (anbefalt_tiltak) fires only at TG3 and that all ARKAT fields are NOT_APPLICABLE at TGIU
6. Retains the critical rule that conditional language ("kan", "dersom", "hvis") is permitted in konsekvens per NS 3600:2025
7. Contains a strict, deterministic moisture_flag decision procedure (Steg A / B / C with strong vs support indicators)
8. Distinguishes between TGIU-begrunnelse (access limitations) and konstruksjonsusikkerhet (construction uncertainty) to prevent double-counting

**Note for Atul:** The revised system_prompt is provided as markdown for readability. When embedding it in `arkat_evaluation_pipeline_step.json` as the value of `system_prompt.content`, preserve all content but escape as a JSON string (escape double quotes and newlines appropriately).

### C.4 Update output_schema

Standardize MISSING representation. The top-level output structure has `field_results`, `tgiu_findings`, and `has_errors` as sibling fields (NOT nested inside each other). Replace the current output schema with:

```json
{
  "point_id": "string — e.g. '5.1', '7.1.3'",
  "tg_grade": "TG2 | TG3 | TGIU",
  "field_results": {
    "aarsak": {
      "status": "CORRECT | WRONG | MISSING | NOT_APPLICABLE",
      "error_type": "string | null — one of the valid error types if status=WRONG, 'MISSING (aarsak)' if status=MISSING, null otherwise",
      "explanation": "string in Norwegian — one sentence max, empty if CORRECT or NOT_APPLICABLE"
    },
    "risiko": {
      "status": "CORRECT | WRONG | MISSING | NOT_APPLICABLE",
      "error_type": "string | null",
      "explanation": "string"
    },
    "konsekvens": {
      "status": "CORRECT | WRONG | MISSING | NOT_APPLICABLE",
      "error_type": "string | null",
      "explanation": "string"
    },
    "anbefalt_tiltak": {
      "status": "CORRECT | WRONG | MISSING | NOT_APPLICABLE",
      "error_type": "string | null",
      "explanation": "string",
      "_note": "status depends on tg_grade AND ns_version. See version-dependent rule below. TGIU always outputs NOT_APPLICABLE regardless of field presence."
    }
  },
  "tgiu_findings": {
    "_note": "Populated only when tg_grade = TGIU. Must be {\"findings\": []} (empty array) for TG2/TG3 points. This is a sibling field to field_results, NOT nested inside it.",
    "findings": [
      {
        "error_type": "TGIU_MISSING_REASON | TGIU_MISSING_FURTHER_INVESTIGATION | TGIU_MISSING_MOISTURE_FLAG | TGIU_CRAWLSPACE_MISSING_RISK_CONSEQUENCE",
        "explanation": "string in Norwegian — one sentence max"
      }
    ]
  },
  "has_errors": "boolean — true if any field has status WRONG or MISSING, OR if tgiu_findings.findings is non-empty"
}
```

This standardizes MISSING handling: the `status` field uses `MISSING` as value, and the `error_type` field carries the scored error identifier (e.g. `MISSING (aarsak)`). The scoring layer looks up `error_type` in the mapping table. This removes the ambiguity between status-level and scoring-level representations.

**Rule for TG2 anbefalt_tiltak absence (version-dependent — make explicit to avoid ambiguity):**

- When tg_grade = TG2 AND ns_version = NS3600:2018 AND anbefalt_tiltak field is absent: output MUST be `{"status": "NOT_APPLICABLE", "error_type": null, "explanation": ""}`. Tiltak is not required under NS 3600:2018 at TG2.

- When tg_grade = TG2 AND ns_version = NS3600:2025 AND anbefalt_tiltak field is absent: output MUST be `{"status": "MISSING", "error_type": "MISSING (anbefalt_tiltak)", "explanation": "..."}`. Tiltak is required under NS 3600:2025 at TG2 per punkt 13.

- When tg_grade = TG3 AND anbefalt_tiltak field is absent (any NS version): output MUST be `{"status": "MISSING", "error_type": "MISSING (anbefalt_tiltak)", "explanation": "..."}`.

- When tg_grade = TGIU: output MUST be `{"status": "NOT_APPLICABLE", "error_type": null, "explanation": ""}` regardless of field content.

**Additional rule for TILTAK_VAGUE_WITHOUT_NECESSITY:** This error type fires ONLY at TG3, regardless of ns_version. Vague tiltak formulations at TG2 (e.g. "kan vurderes ved ordinært vedlikehold", "bør inngå i fremtidig vedlikeholdsplan") are professionally acceptable because TG2 allows "tiltak in nær fremtid" and does not require immediate action.

### C.5 Remove and replace scoring_bridge

Remove the entire existing `scoring_bridge` section and replace with:

```json
"scoring_bridge": {
  "_note_nb": "Mapping av feiltyper til poengtrekk er flyttet til arkat_error_to_deduction_mapping_v1.0.json. Denne filen definerer kun output-formatet fra Dommer B; scoring-laget slår opp poeng i mapping-tabellen basert på error_type-verdien.",
  "source_of_truth": "arkat_error_to_deduction_mapping_v1.0.json",
  "deduplication_note": "Identical (point_id, field, error_type) findings within a single run must be deduplicated before scoring. Cross-category deduplication (e.g. between Category A and Category F) is NOT handled here and must be resolved upstream or in the scoring layer with explicit rules — not inferred."
}
```

Rationale: The existing deduplication rule ("If the same defect triggers both a field-level ARKAT error and a format-level check, only the higher-severity category fires") is too vague to implement safely. Defining "same defect" across categories requires explicit rules that don't exist. Removing the ambiguous rule prevents silent incorrect deduplication. If cross-category deduplication is needed, it should be added as an explicit rule set in a later specification.

### C.6 Update four_structural_error_types

The section currently named `four_structural_error_types` contains SE-1, SE-2, SE-4 but no SE-3 — this implies SE-3 was either removed or the numbering was skipped. Rather than investigating the history, treat this as technical debt to be cleaned up:

**Task 1: Rename section.** Rename `four_structural_error_types` to `structural_error_types` (removing the numeric prefix that implies a fixed count). Add a `_note_nb` explaining that the numbering is historical and that the number of types is not fixed.

**Task 2: Add SE entries for the newly added error types.** Use the next available numbers:

- SE-5 for `PURE_DUPLICATION` (Konsekvens-feltet er identisk med Risiko)
- SE-6 for `AARSAK_AS_RISIKO` (Risiko-feltet inneholder årsak)
- SE-7 for `CONSEQUENCE_AS_TILTAK` (Tiltak-feltet beskriver kjøperkonsekvens)
- SE-8 for `TILTAK_IMPERATIVE_FORM` (Tiltak formulert som pålegg)
- SE-9 for `TILTAK_VAGUE_WITHOUT_NECESSITY` (Tiltak vagt ved TG3)

Each SE entry should follow the existing format: id, name, description, detection_pattern, example_wrong, example_correct, maps_to_error_type. Do NOT reuse SE-3 — let the gap remain.

---

## Section D: Validation requirements

After implementation, run the following checks:

1. **Error type consistency check:** Verify that every error_type referenced in `arkat_semantic_rules.json` field_definitions, every error_type in `arkat_evaluation_pipeline_step.json` system_prompt, and every error_type in `arkat_error_to_deduction_mapping_v1.0.json` deductions — match exactly. A simple diff script that extracts the three sets and confirms they are equal would be sufficient.

2. **TGIU isolation check:** Run Dommer B on a TGIU test point. Verify that output contains `tgiu_findings` entries only, and that `field_results.aarsak/risiko/konsekvens/anbefalt_tiltak` all have status `NOT_APPLICABLE`.

3. **TG2 anbefalt_tiltak isolation check:** Run Dommer B on a TG2 test point where anbefalt_tiltak is absent. Verify that `field_results.anbefalt_tiltak.status` is `NOT_APPLICABLE`, not `MISSING`.

4. **Error type recognition check:** Send a synthetic Dommer B output to the scoring layer containing each of the 21 error types. Verify each produces a deduction. Send one unrecognized error type (e.g. `FAKE_ERROR_TYPE`). Verify it is logged as warning and does not produce a deduction.

---

## Section E: Out of scope for this revision

The following are known issues but should NOT be addressed in this revision:

- Cross-category deduplication rules (between Category A and Category F for same building defect) — requires separate specification
- Revisions to Dommer A scoring model (removal of the 7 semantic rules that now belong in Dommer B) — covered in companion specification
- Revisions to hovedsystem-prompt (`system_prompt_validert_v1.6.txt`) to remove conflicting ARKAT instructions — covered in companion specification
- Updates to Category F rules for rekkverkshøyde (forskrift § 2-13 fjerde ledd) — separate scope

---

## Section F: Estimated effort

Implementation of this specification should be approximately 4–8 hours of focused work:
- Updating two JSON files: 2–3 hours
- Rewriting the system_prompt: 1–2 hours
- Running validation checks and fixing inconsistencies: 1–2 hours
- Testing on sample reports: 1 hour

This is an estimate — actual time may vary based on the codebase integration points.
