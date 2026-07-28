# Post-LLM finding transformation inventory v1.0

Scope: the signed `local_postprocess_dommer_b_fallback` route. This is an inventory of existing behavior, not a new rule set. No listed transformation was changed during verified-template gate implementation.

## Dommer B response normalization

| Code reference | Trigger | Action | Governing source |
|---|---|---|---|
| `arkat_semantic_pipeline.py:4210-4213` | Model returns `_raw_text` | Parse plaintext into the structured verdict shape | Code-resident compatibility behavior |
| `:4214-4243` | Missing/unusable structured verdict or TGIU | Construct heuristic field defaults and TGIU findings | Code-resident fallback behavior |
| `:4244-4254`, `:4426-4436` | Every normalized verdict | Apply regression guards, raw consequence/action second passes, and TGIU not-applicable forcing | Dommer B spec regression guards in code |
| `:4267-4296` | TGIU findings returned | Allow-list, deduplicate, discard invalid types, and supplement required heuristic TGIU findings | Allowed error taxonomy plus code safety net |
| `:4297-4321` | Each ARKAT field | Normalize status; synthesize missing type; replace an unapproved WRONG type with a valid heuristic type or `CORRECT` when none exists | `_DOMMER_B_ALLOWED_ERROR_TYPES`; code-resident decision explicitly declared |
| `:4322-4348` | Field text presence differs from verdict | Recover consequence text; enforce TGIU/NS2018 applicability; convert false MISSING using heuristic evaluation | Input-handling contract in code |
| `:4349-4419` | Known risk/consequence/duplication/action edge cases | Rewrite status/error type using deterministic semantic guards and clear error data for CORRECT/NOT_APPLICABLE | Code-resident regression behavior |
| `:4668-4677` | Two field/error combinations | Reclassify through `_FIELD_BOUND_ERROR_ALIASES` | Hard-coded alias table |
| `:4681-4719` | WRONG/MISSING becomes a scored finding | Map by governed deduction table; if unmapped, apply hard-coded default/low/high severity and points | Governed mapping at 4691-4700; declared code fallback at 4702-4719 |

## Shared post-processing chain

The following is the complete ordered finding-affecting chain in `ai_analyzer.py:16329-16396`. Prefix semantics are literal: `_filter_`/`_drop_` suppress, `_ensure_`/`_force_` may add or hydrate, `_normalize_`/`_polish_`/`_sanitize_` rewrite or reclassify, `_dedupe_` removes duplicates, `_sync_` mirrors canonical findings into derived views, and `_mark_` changes metadata/status. Each helper's trigger and exact action are its function body at the named symbol; governance is the referenced governed files where loaded, otherwise it is code-resident and therefore declared here.

1. `_attach_exact_point_sources_to_findings`
2. `_filter_tg3_cost_missing_false_positives`
3. `_drop_tg_and_consequence_false_positives`
4. `_filter_regime_conditioned_rules`
5. `_drop_no_tg_hms_as_regular_tg_findings`
6. `_drop_false_electrical_tg_forbidden_findings`
7. `_ensure_issue_evidence`
8. `_ensure_driver_evidence`
9. `_normalize_scoring_output`
10. `_run_ark_arkat_per_segment_validation`
11. `_ensure_semantic_tg3_cost_backstop`
12. `_drop_arkat_false_positives`
13. `_drop_good_enough_content_false_positives`
14. `_drop_segment_arkat_for_tg2_only_points`
15. `_drop_tg2_tiltak_requirement_false_positives`
16. `_soften_no_tg_hms_findings`
17. `_ensure_electrical_no_tg_hms_findings`
18. `_ensure_generic_backstop_findings`
19. `_drop_age_only_false_positives`
20. `_drop_unexpected_jargon_findings`
21. `_ensure_non_buyer_oriented_consequence_findings`
22. `_normalize_non_buyer_oriented_consequence_findings`
23. `_drop_buyer_only_consequence_public_claims`
24. `_ensure_finding_suggestions_differentiated`
25. `_normalize_report_level_finding_targets`
26. `_ensure_writing_help_fields`
27. `_dedupe_all_findings_duplicate_safe`
28. `_force_required_public_findings`
29. `_normalize_legal_finding_labels`
30. `_drop_report_level_false_positives`
31. `_drop_known_client_false_positives`
32. `_drop_false_freestanding_garage_findings`
33. `_drop_missing_tiltak_when_raw_action_present`
34. `_drop_duplicate_missing_tiltak_findings`
35. `_drop_tg3_missing_tiltak_false_positives_from_point_text`
36. `_drop_overlapping_consequence_missing_findings`
37. `_drop_missing_claims_when_semantic_field_correct`
38. `_drop_legacy_consequence_unclear_when_semantic_missing`
39. `_finalize_dommer_b_canonical_output`
40. `_drop_tg3_cost_top_issues_if_segments_have_cost`
41. `_ensure_tg3_missing_cost_compliance_from_segments`
42. `_sync_gate_from_all_findings`
43. `_sync_category_breakdown_with_score_by_category`
44. `_scrub_age_only_category_summary_without_finding`
45. `_normalize_zero_score_language_findings`
46. `_ensure_tgiu_deductions_visible_in_all_findings`
47. `_sync_public_output_views`
48. `_normalize_user_facing_child_titles`
49. `_polish_analysis_text_fields`
50. `_sanitize_analysis_output_text`
51. `finalize_client_arkat_semantic_pipeline_output`
52. `_drop_tg3_missing_tiltak_for_semantic_tg2_not_applicable`
53. `_mark_duplicate_f001_informational`
54. `_normalize_category_summary_consequence_wording`
55. `_sanitize_user_facing_text_contracts`
56. `_finalize_category_summary_public_contracts`
57. `_remove_dead_public_visibility_fields`
58. `_mark_incomplete_fallback_output`

Some helpers deliberately repeat later in the chain after canonical/public synchronization: false electrical TG, freestanding garage, scoring normalization, writing help, legacy consequence, buyer-only consequence, category scrub, and TGIU visibility. Their repeated invocation is part of the existing ordered behavior.

## Gate implementation boundary

The verified-template gate runs before report creation, cache lookup, S3 upload, and either analyzer. It does not invoke or modify any transformation above. Bedrock evidence capture records request and response bytes after invocation; it does not modify inference inputs or outputs.
