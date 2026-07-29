# Complete post-LLM transformation inventory v1.0

Each record cites the complete function body and its SHA-256, enumerates every AST If trigger and every direct mutation statement, and declares its governing source or frozen code-resident ruling.

Records: 61. No runtime logic was changed.

## 1. `_attach_exact_point_sources_to_findings`

- Code: `backend/app/services/ai_analyzer.py:3183-3217`
- Source SHA-256: `54df78980156f3c588dc579eadc24cb87b3726da2bf9add091ea0a3e10862d68`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Attaches exact source-point evidence to matching findings under the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 2. `_filter_tg3_cost_missing_false_positives`

- Code: `backend/app/services/ai_analyzer.py:3547-3621`
- Source SHA-256: `c5b98d128178adb6be6cfe36e3ac8cf27983f12d112eca40330bda8db2730cc4`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 3. `_drop_tg_and_consequence_false_positives`

- Code: `backend/app/services/ai_analyzer.py:3767-3921`
- Source SHA-256: `75979fff90406bf9cafbdc5350cf45c6cbcbbfc62d557e65fd936232960663f3`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic.", "references": ["get_points_overview_mapping_config"], "type": "governed_or_code_bridge"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 4. `_filter_regime_conditioned_rules`

- Code: `backend/app/services/ai_analyzer.py:6119-6254`
- Source SHA-256: `3aec25339b4237d1e65ed87ac59360a05c6a9fec4a810961fceba01e4e0ce721`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 5. `_drop_no_tg_hms_as_regular_tg_findings`

- Code: `backend/app/services/ai_analyzer.py:6019-6091`
- Source SHA-256: `88fb8959722b11a935536265f45e345f3ea2b96c8cfa463ce684804ab0881a89`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 6. `_drop_false_electrical_tg_forbidden_findings`

- Code: `backend/app/services/ai_analyzer.py:6387-6439`
- Source SHA-256: `285621940b984b935eed2a5fcd8fa2d8c5f4165469b016caa7a69a5711aa5dbd`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 7. `_ensure_issue_evidence`

- Code: `backend/app/services/ai_analyzer.py:15375-15392`
- Source SHA-256: `566149ea90495f26e36399961a618d03ca283bda6d38b3947ecdcb68aff55d47`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Adds, hydrates, or restores the named finding/evidence/backstop only when the exact branch conditions below are satisfied.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 8. `_ensure_driver_evidence`

- Code: `backend/app/services/ai_analyzer.py:15395-15433`
- Source SHA-256: `dddf4ca44f8add7a754dc1815c5b06c3cf6e667fde6581250a84d0ee6f75ac4f`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Adds, hydrates, or restores the named finding/evidence/backstop only when the exact branch conditions below are satisfied.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 9. `_normalize_scoring_output`

- Code: `backend/app/services/ai_analyzer.py:15759-15970`
- Source SHA-256: `e2bda9311211607f2fcbb9c2e106bf32916590c8625ab0fecbc60bde327538d0`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Deterministically rewrites/reclassifies the named fields according to the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 10. `_run_ark_arkat_per_segment_validation`

- Code: `backend/app/services/ai_analyzer.py:4845-5063`
- Source SHA-256: `7505b6f2b751818a4497546f4100ebf3db3dbe26a3ad7e522e34be5fd59c1a00`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Runs the named validation and merges its governed findings under the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 11. `_ensure_semantic_tg3_cost_backstop`

- Code: `backend/app/services/ai_analyzer.py:5145-5231`
- Source SHA-256: `6ce82efc965c1312bdba91f77f8b1f27b8872a6d565bca4528d944ecd7d6b3dd`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Adds, hydrates, or restores the named finding/evidence/backstop only when the exact branch conditions below are satisfied.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 12. `_drop_arkat_false_positives`

- Code: `backend/app/services/ai_analyzer.py:8165-8233`
- Source SHA-256: `6ebe56f46abf8b61f4943becc26e91248bcd2b6a0f7843b6b3bb3d87184d67ae`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 13. `_drop_good_enough_content_false_positives`

- Code: `backend/app/services/ai_analyzer.py:8803-8968`
- Source SHA-256: `64e1e29826c407f0e623d24d8f63cd2227a67263f6393f17413478d1a4bcb21e`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 14. `_drop_segment_arkat_for_tg2_only_points`

- Code: `backend/app/services/ai_analyzer.py:5816-5857`
- Source SHA-256: `6f46f52a5d295b6de18526f943d0584a9d8d2bf0259871f406d70cfca67033f4`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 15. `_drop_tg2_tiltak_requirement_false_positives`

- Code: `backend/app/services/ai_analyzer.py:5935-6016`
- Source SHA-256: `a3cde1b78a51a42adc53d4a752af0467c6486bbc13e59ba91d8c5303de81ae0f`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 16. `_soften_no_tg_hms_findings`

- Code: `backend/app/services/ai_analyzer.py:6257-6316`
- Source SHA-256: `7bb444eb5a560ad75dfcc770e13f8b19356f2e113070fa868ae9b2c26c265378`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Performs the mutations shown in the cited complete function body.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 17. `_ensure_electrical_no_tg_hms_findings`

- Code: `backend/app/services/ai_analyzer.py:6441-6526`
- Source SHA-256: `469abc6e77eed1cafc9a1298d59fdb6840708d4a361429486897da3b18b75075`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Adds, hydrates, or restores the named finding/evidence/backstop only when the exact branch conditions below are satisfied.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 18. `_ensure_generic_backstop_findings`

- Code: `backend/app/services/ai_analyzer.py:6916-7165`
- Source SHA-256: `29ac02996936d63199ef688b55f4087782277e5f7cc8dc1e8653ae3efabb4878`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Adds, hydrates, or restores the named finding/evidence/backstop only when the exact branch conditions below are satisfied.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 19. `_drop_age_only_false_positives`

- Code: `backend/app/services/ai_analyzer.py:10430-10550`
- Source SHA-256: `77d433b2599d20d05e108339e4ef8d2a4544eddd79be6653c18d31bdf924c0b0`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 20. `_drop_unexpected_jargon_findings`

- Code: `backend/app/services/ai_analyzer.py:6881-6913`
- Source SHA-256: `c97e9b247fa06db56d262ea24e20e831c05eeb3982140638ec9ec6b7a191bf06`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 21. `_ensure_non_buyer_oriented_consequence_findings`

- Code: `backend/app/services/ai_analyzer.py:8630-8638`
- Source SHA-256: `78d4d59575ba295f488c028bc35e4257a172f9515a64d3ba0ac742eaffe2ba23`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Adds, hydrates, or restores the named finding/evidence/backstop only when the exact branch conditions below are satisfied.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 22. `_normalize_non_buyer_oriented_consequence_findings`

- Code: `backend/app/services/ai_analyzer.py:8624-8627`
- Source SHA-256: `5e8f84f5fa349253f29757a427460ff6124988b2d39cfe799e222858035394fa`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Deterministically rewrites/reclassifies the named fields according to the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 23. `_drop_buyer_only_consequence_public_claims`

- Code: `backend/app/services/ai_analyzer.py:7993-8024`
- Source SHA-256: `afd9bdd03156c0d4be8ea18aeacd3c27bf775d41d5074004b0d4285b640bb112`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 24. `_ensure_finding_suggestions_differentiated`

- Code: `backend/app/services/ai_analyzer.py:5234-5319`
- Source SHA-256: `24bb27e4b980e8c254f6654c122141af169cc08c2d10b665da1f53ff9d1fdf40`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Adds, hydrates, or restores the named finding/evidence/backstop only when the exact branch conditions below are satisfied.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 25. `_normalize_report_level_finding_targets`

- Code: `backend/app/services/ai_analyzer.py:3477-3488`
- Source SHA-256: `adaa8da9dddcca7deafeeaa47e61fbd2604324a5d23bfeef1d6adff355b91cbc`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Deterministically rewrites/reclassifies the named fields according to the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 26. `_ensure_writing_help_fields`

- Code: `backend/app/services/ai_analyzer.py:5790-5813`
- Source SHA-256: `01521d10c0ef2b5933f2ce75176f845f75aa25eaa78893a618c0e60170fb38bf`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Adds, hydrates, or restores the named finding/evidence/backstop only when the exact branch conditions below are satisfied.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 27. `_dedupe_all_findings_duplicate_safe`

- Code: `backend/app/services/ai_analyzer.py:8994-9032`
- Source SHA-256: `b981c06cac8e06cdd5c14884403d38125e904bf097be2355243b23fd35671e34`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Removes duplicate finding representations using the keys and precedence conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 28. `_force_required_public_findings`

- Code: `backend/app/services/ai_analyzer.py:10711-10811`
- Source SHA-256: `82326374a011ac14e42229193e3018a19bd8cf08576cfd39f9062dd97f6719bb`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Adds, hydrates, or restores the named finding/evidence/backstop only when the exact branch conditions below are satisfied.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 29. `_normalize_legal_finding_labels`

- Code: `backend/app/services/ai_analyzer.py:10814-10843`
- Source SHA-256: `5f295142b18aab9a52910257618029d1daac0958466803dbb7e62f676d66c628`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Deterministically rewrites/reclassifies the named fields according to the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 30. `_drop_report_level_false_positives`

- Code: `backend/app/services/ai_analyzer.py:10553-10600`
- Source SHA-256: `dbcd6c2389faef51e0f6a89e6a37c7c80be2438d6af93b8e22d7b68082cf5360`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 31. `_drop_known_client_false_positives`

- Code: `backend/app/services/ai_analyzer.py:10603-10708`
- Source SHA-256: `88bc15714c576c247a308ae6e861c0f9fbdd4391d5a8dfb645381c29f2e11566`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 32. `_drop_false_freestanding_garage_findings`

- Code: `backend/app/services/ai_analyzer.py:7213-7257`
- Source SHA-256: `8910df75b51bdc75ff49942822698b473ee8914e82901e6c3c19044a7276e776`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 33. `_drop_missing_tiltak_when_raw_action_present`

- Code: `backend/app/services/ai_analyzer.py:9291-9322`
- Source SHA-256: `82b5b46158b445bdf1c6961615ffc861f13a5af1cb2e6eb1c9681cb2c420ad72`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 34. `_drop_duplicate_missing_tiltak_findings`

- Code: `backend/app/services/ai_analyzer.py:9324-9488`
- Source SHA-256: `711a8295c901bc8744a71c6ef2f5d2a9370116b3c35a607d2284c53672f187b8`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic.", "references": ["A_ARKAT_SEMANTIC.ANBEFALT_TILTAK.MISSING_ANBEFALT_TILTAK"], "type": "governed_or_code_bridge"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 35. `_drop_tg3_missing_tiltak_false_positives_from_point_text`

- Code: `backend/app/services/ai_analyzer.py:9676-9837`
- Source SHA-256: `5b7dcc20b30d4dd4ddff04bfe46318a3d72c44b4485437a90b2f53b01d9e0c26`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 36. `_drop_overlapping_consequence_missing_findings`

- Code: `backend/app/services/ai_analyzer.py:9512-9573`
- Source SHA-256: `723c4143f7e898af53edc955bb1c2243ea86a58d2a3b087cef77ff61e94c617b`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 37. `_drop_missing_claims_when_semantic_field_correct`

- Code: `backend/app/services/ai_analyzer.py:9576-9673`
- Source SHA-256: `95950cb6b396c45291ae63e4a16517f570e2b793ff02505514c2921aaa312254`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 38. `_drop_legacy_consequence_unclear_when_semantic_missing`

- Code: `backend/app/services/ai_analyzer.py:7462-7488`
- Source SHA-256: `42b152bb87ae679aa10d7a3f20137e04bae0c5f5c897f0d0ba58975a1f51d7b3`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 39. `_finalize_dommer_b_canonical_output`

- Code: `backend/app/services/ai_analyzer.py:9056-9154`
- Source SHA-256: `a84e76750e4bf15a761690108e5d697682a4d9353e47627a6b7fb3473703921e`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Performs the mutations shown in the cited complete function body.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 40. `_drop_tg3_cost_top_issues_if_segments_have_cost`

- Code: `backend/app/services/ai_analyzer.py:5860-5932`
- Source SHA-256: `8ca477256ed2ab33aa68d2e5422e9a22c370ac5f2cbf00c8da3ceeff59684c4e`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 41. `_ensure_tg3_missing_cost_compliance_from_segments`

- Code: `backend/app/services/ai_analyzer.py:5116-5142`
- Source SHA-256: `ad51bdf13e7ca0c73a919635b6a6e9ca8617ac89f66bacdd772429da8e91a54b`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Adds, hydrates, or restores the named finding/evidence/backstop only when the exact branch conditions below are satisfied.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 42. `_sync_gate_from_all_findings`

- Code: `backend/app/services/ai_analyzer.py:7260-7302`
- Source SHA-256: `87d849de0a58c85083b9907ca9024efd373363ac36f0bd3a520bec4843a58a70`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Synchronizes derived scoring, gate, category, or public views from canonical findings under the conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 43. `_sync_category_breakdown_with_score_by_category`

- Code: `backend/app/services/ai_analyzer.py:7662-7737`
- Source SHA-256: `ff5b0b11a5c434c35fb3e5d43c7e9b4b37b296835fc8256806639ca237bba30e`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Synchronizes derived scoring, gate, category, or public views from canonical findings under the conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 44. `_scrub_age_only_category_summary_without_finding`

- Code: `backend/app/services/ai_analyzer.py:10414-10428`
- Source SHA-256: `221785365140bd16d5a26c23e6497332efbbfe980bbc97437a94a1fac4c2086b`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Performs the mutations shown in the cited complete function body.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 45. `_normalize_zero_score_language_findings`

- Code: `backend/app/services/ai_analyzer.py:9985-10015`
- Source SHA-256: `07fc445e6d41ef754f22b587d1741a17a19614f7906c26471b8666e071a5ee1c`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Deterministically rewrites/reclassifies the named fields according to the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 46. `_ensure_tgiu_deductions_visible_in_all_findings`

- Code: `backend/app/services/ai_analyzer.py:7305-7396`
- Source SHA-256: `5163938b75a5537510953a220ab61f04ae9d617ed176ea30ed3397c08685f859`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Adds, hydrates, or restores the named finding/evidence/backstop only when the exact branch conditions below are satisfied.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 47. `_sync_public_output_views`

- Code: `backend/app/services/ai_analyzer.py:10846-11004`
- Source SHA-256: `393fb1f162bb1872585fa51508a55ae95e3267764210c45b2278bc46eb89a488`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Synchronizes derived scoring, gate, category, or public views from canonical findings under the conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 48. `_normalize_user_facing_child_titles`

- Code: `backend/app/services/ai_analyzer.py:3445-3474`
- Source SHA-256: `b44ce7e60c3568dd4583abb2123a9ee2843a2ca97d94e0a69967f0509c387311`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Deterministically rewrites/reclassifies the named fields according to the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 49. `_polish_analysis_text_fields`

- Code: `backend/app/services/ai_analyzer.py:11143-11163`
- Source SHA-256: `6b36e70ff04cf38b5a445b5190d02228c5414b86234f42f1fb0c93ecf623d460`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Deterministically rewrites/reclassifies the named fields according to the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 50. `_sanitize_analysis_output_text`

- Code: `backend/app/services/ai_analyzer.py:15984-15996`
- Source SHA-256: `84479dd1295ca2c3dff0d9cf2c9135ed27e4ff31e8f6db0bd6fb28a19837f27d`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Deterministically rewrites/reclassifies the named fields according to the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 51. `_drop_tg3_missing_tiltak_for_semantic_tg2_not_applicable`

- Code: `backend/app/services/ai_analyzer.py:7892-7912`
- Source SHA-256: `1bb24bd3bcf8fdae5fbd1a08fb63ebc14c8659b396e6c156ca89e5ea8b061c0b`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Suppresses/removes only records satisfying the exact branch conditions below; retains all others.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 52. `_mark_duplicate_f001_informational`

- Code: `backend/app/services/ai_analyzer.py:8104-8162`
- Source SHA-256: `5630e149a7253272f884f1a3ef81b57ab9e8d3da0b6c2136acfc7522f4b7d15f`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Changes finding/output metadata status under the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 53. `_normalize_category_summary_consequence_wording`

- Code: `backend/app/services/ai_analyzer.py:7768-7773`
- Source SHA-256: `1a672d3022814661d89b6c79abc59cb28bbfc4c8df599c84ebc61541c59ca92b`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Deterministically rewrites/reclassifies the named fields according to the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 54. `_sanitize_user_facing_text_contracts`

- Code: `backend/app/services/ai_analyzer.py:8068-8092`
- Source SHA-256: `aee849c120eeb143dabd40f29bb532474d2248fd0b4ecab06aea9171cd80018d`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Deterministically rewrites/reclassifies the named fields according to the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 55. `_finalize_category_summary_public_contracts`

- Code: `backend/app/services/ai_analyzer.py:7797-7826`
- Source SHA-256: `c84e3883678efe0ee9e21708f20d2f71fc36641c57d131cb13ab06899c2de96d`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Performs the mutations shown in the cited complete function body.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 56. `_remove_dead_public_visibility_fields`

- Code: `backend/app/services/ai_analyzer.py:11166-11175`
- Source SHA-256: `48f62a6f5c3bfda8754652bf9954b4a8c2f316fa05386b967a457bc6abacea6b`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Performs the mutations shown in the cited complete function body.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 57. `_mark_incomplete_fallback_output`

- Code: `backend/app/services/ai_analyzer.py:16021-16099`
- Source SHA-256: `5c5858545fcc6f3da53c5e5e0179296666feca610b01722596f6f8dbc8efa1a6`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Changes finding/output metadata status under the exact branch conditions below.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic.", "references": ["get_runtime_manifest"], "type": "governed_or_code_bridge"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 58. `_finalize_verified_fallback_customer_payload`

- Code: `backend/app/services/ai_analyzer.py:16853-16923`
- Source SHA-256: `1305e7bc96603890507efc2446d5ca3e5656c035b1cfc05917cf03f590944530`
- Trigger: Exactly the boolean expressions in trigger_conditions_ast; no implicit trigger is declared.
- Action: Performs the mutations shown in the cited complete function body.
- Governance: `{"fallback": "Product-owner option-1 frozen code-resident runtime logic declared by this inventory.", "references": [], "type": "code_resident"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 1. `_normalize_arkat_eval_result`

- Code: `backend/app/services/arkat_semantic_pipeline.py:4199-4436`
- Source SHA-256: `c3470fbe555138902dfd192ad712c892c6a5ef6a21b1cdaae1d240709f7cb5c6`
- Trigger: Every parsed Dommer B point verdict; branches are the complete AST conditions recorded below.
- Action: Parses plaintext fallback; constructs heuristic defaults; validates and supplements TGIU; normalizes statuses; rejects unapproved WRONG error types; recovers false MISSING fields; applies deterministic regression guards. In particular lines 4314-4321 convert an out-of-catalog WRONG to the valid heuristic error type, or to CORRECT with cleared error data when none exists.
- Governance: `{"references": ["DOMMER_B_ALLOWED_ERROR_TYPES, ARKAT field applicability rules, and product-owner option-1 frozen code-resident runtime logic."], "type": "explicit"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 2. `_normalize_field_bound_error_type`

- Code: `backend/app/services/arkat_semantic_pipeline.py:4674-4678`
- Source SHA-256: `19c372f0571f0a1f72b1133a2cb4dddac9d8ebfc62822464f923163a33080839`
- Trigger: A field/error-type pair is converted to scoring metadata.
- Action: Reclassifies the two pairs in _FIELD_BOUND_ERROR_ALIASES; otherwise preserves the type.
- Governance: `{"references": ["Hard-coded alias table explicitly governed as frozen runtime logic."], "type": "explicit"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.

## 3. `_status_to_scoring_meta`

- Code: `backend/app/services/arkat_semantic_pipeline.py:4681-4719`
- Source SHA-256: `55b690de74c0ec2c1bdc529d8fbe33c1c62e1fb97eb3d6a68ab7ea53327cf5c2`
- Trigger: Normalized field status is WRONG or MISSING and has a bridge key.
- Action: Uses governed deduction mapping when present; otherwise applies code-resident default/low/high severity and point decisions.
- Governance: `{"references": ["arkat_error_to_deduction_mapping_v1_1_2.json, with declared hard-coded fallback at lines 4702-4719."], "type": "explicit"}`
- Exact conditions/mutations: see the JSON record; the cited complete body is authoritative.
