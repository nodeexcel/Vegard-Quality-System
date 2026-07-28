from pathlib import Path
from typing import Dict, List
from datetime import datetime, timezone
import hashlib
import json
import re

FILES_DIR = Path(__file__).resolve().parents[3] / "files"
REPOSITORY_ROOT = FILES_DIR.parent
MANIFEST_PATH = FILES_DIR / "MANIFEST.json"

SYSTEM_PROMPT_PATH = FILES_DIR / "system_prompt_validert_v1_10.txt"
RAG_LEGAL_PATH = FILES_DIR / "rag_legal_framework_validert_v1.4.txt"
RAG_RULES_PATH = FILES_DIR / "rag_validert_rules_v1.4.txt"
RAG_LANGUAGE_PATH = FILES_DIR / "rag_language_rules_v1.7.txt"
SCORING_MODEL_PATH = FILES_DIR / "rag_scoring_model_validert_v1.6.15.json"
OUTPUT_SCHEMA_PATH = FILES_DIR / "output_schema_validert_v1.5.json"
OUTPUT_OVERLAY_PATH = FILES_DIR / "scoring_policy.validert_output_overlay.v1.1.json"
DETECTED_POINTS_SCHEMA_PATH = FILES_DIR / "validert_detected_points_v1.0.schema.json"
FEEDBACK_SCHEMA_PATH = FILES_DIR / "validert_feedback_v1.1.schema.json"
CATEGORY_CONFIG_PATH = FILES_DIR / "validert_category_config_v1_0.json"
LEGALITY_RULES_PATH = FILES_DIR / "validert_legal_compliance_rules_v1_1.json"
LEGALITY_ARKAT_TEMPLATES_PATH = FILES_DIR / "validert_arkat_templates_lovlighet_v1_1.json"
LEGALITY_ARKAT_MAP_PATH = FILES_DIR / "validert_lovlighet_to_arkat_map_v1_1.json"
LEGALITY_GUARDRAILS_PATH = FILES_DIR / "validert_no_prosjektering_guardrails_v1_1.json"
ORCHESTRATOR_PIPELINE_PATH = FILES_DIR / "validert_orchestrator_pipeline_v2.1.json"
ACTIVE_CONFIG_PATH = FILES_DIR / "validert_active_config_v1.2.json"
AGE_SERVICE_LIFE_VALIDATION_PATH = FILES_DIR / "tg2_tg3_age_service_life_validation_v2_3.json"

# Client bundle (2026-02-27): core hierarchy, punkt-for-punkt, room instances, meta rules, lovlighet patches
ELEMENT_HIERARCHY_PATH = FILES_DIR / "validert_element_hierarchy_v1_2.json"
ELEMENT_HIERARCHY_SYNONYMS_PATH = FILES_DIR / "validert_element_hierarchy_synonyms_v1_1.json"
PUNKT_STATUS_RULES_PATH = FILES_DIR / "validert_punkt_for_punkt_status_rules_v1_2.json"
PUNKT_SCORING_HOOKS_PATH = FILES_DIR / "validert_punkt_for_punkt_scoring_hooks_v1_1.json"
ROOM_INSTANCES_SCHEMA_PATH = FILES_DIR / "validert_room_instances_schema_v1_0.json"
ROOM_INSTANCE_EXTRACTION_RULES_PATH = FILES_DIR / "validert_room_instance_extraction_rules_v1_0.json"
ROOM_INSTANCE_ARKAT_COVERAGE_PATH = FILES_DIR / "validert_room_instance_arkat_coverage_rules_v1_1.json"
ROOM_INSTANCE_ROLLUP_FORMAT_PATH = FILES_DIR / "validert_room_instance_rollup_format_v1_1.json"
META_RULE_OPTIONAL_TG_FORBIDDEN_PATH = FILES_DIR / "validert_optional_tg_forbidden_meta_rule_v1_0.json"
META_RULE_NON_MANDATORY_ASSESSED_PATH = FILES_DIR / "validert_non_mandatory_assessed_meta_rule_v1_1.json"
LOVLIGHET_PATCH_EL_TG_PATH = FILES_DIR / "validert_lovlighet_patch_el_tg_v1_2.json"
LOVLIGHET_PATCH_HMS_TG_PATH = FILES_DIR / "validert_lovlighet_patch_hms_tg_v1_2.json"
BUILDING_PART_WHITELIST_V21_PATH = FILES_DIR / "validert_building_part_whitelist_v2_1.json"
BUILDING_PART_WHITELIST_V22_PATH = FILES_DIR / "validert_building_part_whitelist_v2_2.json"
CANONICAL_POINTS_V30_PATH = FILES_DIR / "validert_canonical_points_v3_0.json"
MIGRATION_MAP_V33_TO_V34_PATH = FILES_DIR / "validert_migration_map_v3.3_to_v3.4.json"
FORSKRIFT_MATRIX_PATH = FILES_DIR / "validert_forskrift_matrix_v1.0.json"
MANDATORY_EXPLANATION_ONLY_PATH = FILES_DIR / "validert_mandatory_explanation_only_rules_v1.0.json"
ARKAT_SEMANTIC_RULES_PATH = FILES_DIR / "arkat_semantic_rules_v1_2_3.json"
ARKAT_EVALUATION_PIPELINE_STEP_PATH = FILES_DIR / "arkat_evaluation_pipeline_step.json"
DOMMER_B_SYSTEM_PROMPT_PATH = FILES_DIR / "dommer_b_system_prompt_v13.md"
ARKAT_ERROR_DEDUCTION_MAPPING_PATH = FILES_DIR / "arkat_error_to_deduction_mapping_v1_1_2.json"
REPORT_FORMAT_DETECTION_PATH = FILES_DIR / "report_format_detection.json"
ARKAT_CANONICAL_EXAMPLES_PATH = FILES_DIR / "arkat_canonical_examples_v1_1_1.json"
INCOMPLETE_ANALYSIS_POLICY_PATH = FILES_DIR / "validert_incomplete_analysis_policy_v1_5.json"
VERIFIED_CUSTOMER_TEMPLATES_PATH = FILES_DIR / "verified_customer_templates_v1_0.json"

# Routing: if element.tg_policy == "FORBIDDEN" -> apply optional_tg_forbidden_meta_rule_v1_0
#          if element.element_type == "NON_MANDATORY_ASSESSED" -> apply non_mandatory_assessed_meta_rule_v1_1
# OPTIONAL elements only shown if present. Room instances: validate ARKAT per instance, enforce coverage assertion.
META_RULE_ROUTING = """META RULE ROUTING (apply in this order per element):
- If element.tg_policy == "FORBIDDEN" -> apply validert_optional_tg_forbidden_meta_rule_v1_0 (minimum Årsak + Konsekvens).
- Else if element.element_type == "NON_MANDATORY_ASSESSED" -> apply validert_non_mandatory_assessed_meta_rule_v1_1.
- OPTIONAL and NON_MANDATORY_ASSESSED elements: show only if present in the report; no deduction if missing.
- Room instances (BE-20, BE-21, BE-22, BE-23): validate ARKAT per instance; enforce coverage assertion (every instance must be validated)."""


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _load_json_file(path: Path) -> Dict:
    try:
        return json.loads(_read_text(path))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {}


def _manifest_entries() -> list[Dict]:
    manifest = _load_json_file(MANIFEST_PATH)
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        return []
    return [entry for entry in files if isinstance(entry, dict)]


def _resolve_file_from_name(filename: str) -> Path:
    """Resolve configured filename in files/ with safe fallbacks for dot/underscore variants."""
    clean = (filename or "").strip()
    if not clean:
        return Path("")
    candidate = FILES_DIR / clean
    if candidate.exists():
        return candidate
    stem = Path(clean).stem
    suffix = Path(clean).suffix or ".json"
    # Common client naming mismatch: v3.2 <-> v3_0 file names.
    normalized_stem = stem.replace(".", "_")
    underscore_candidate = FILES_DIR / f"{normalized_stem}{suffix}"
    if underscore_candidate.exists():
        return underscore_candidate
    return candidate


def _resolve_manifest_file(*name_parts: str) -> Path:
    required_parts = [part.strip().lower() for part in name_parts if part and part.strip()]
    if not required_parts:
        return Path("")

    for entry in _manifest_entries():
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        normalized_name = Path(raw_path.strip()).name.lower()
        if all(part in normalized_name for part in required_parts):
            resolved = _resolve_file_from_name(raw_path)
            if resolved.exists():
                return resolved
    return Path("")


def get_active_config() -> Dict:
    if ACTIVE_CONFIG_PATH.exists():
        return _load_json_file(ACTIVE_CONFIG_PATH)
    manifest_path = _resolve_manifest_file("active", "config")
    if manifest_path.exists():
        return _load_json_file(manifest_path)
    return {}


def get_verified_customer_templates() -> Dict:
    return _load_json_file(VERIFIED_CUSTOMER_TEMPLATES_PATH)


def get_active_pipeline_file_path() -> Path:
    cfg = get_active_config()
    configured = cfg.get("active_pipeline_file") if isinstance(cfg, dict) else None
    if isinstance(configured, str) and configured.strip():
        resolved = _resolve_file_from_name(configured)
        if resolved.exists():
            return resolved
    manifest_path = _resolve_manifest_file("orchestrator", "pipeline")
    if manifest_path.exists():
        return manifest_path
    return ORCHESTRATOR_PIPELINE_PATH


def get_manifest() -> Dict:
    return _load_json_file(MANIFEST_PATH)


def get_manifest_text() -> str:
    manifest = get_manifest()
    if manifest:
        return json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    return _read_text(MANIFEST_PATH)


def get_runtime_code_pin_results() -> List[Dict[str, object]]:
    manifest = get_manifest()
    declared = manifest.get("runtime_code_files") if isinstance(manifest, dict) else []
    results: List[Dict[str, object]] = []
    for entry in declared if isinstance(declared, list) else []:
        if not isinstance(entry, dict):
            continue
        relative = str(entry.get("path") or "").strip()
        expected = str(entry.get("sha256") or "").strip().lower()
        path = REPOSITORY_ROOT / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        results.append({
            "path": relative,
            "declared_sha256": expected,
            "actual_sha256": actual,
            "exists": path.is_file(),
            "hash_matches": bool(expected and actual and expected == actual),
        })
    return results


def assert_runtime_code_pins() -> None:
    results = get_runtime_code_pin_results()
    expected_paths = {
        "backend/app/api/v1/reports.py",
        "backend/app/services/ai_analyzer.py",
        "backend/app/services/arkat_semantic_pipeline.py",
        "backend/app/services/bedrock_ai.py",
    }
    actual_paths = {str(row.get("path") or "") for row in results}
    failures = [row for row in results if not bool(row.get("hash_matches"))]
    if actual_paths != expected_paths or failures:
        raise RuntimeError(
            "Runtime code pin validation failed: "
            + json.dumps({"expected_paths": sorted(expected_paths), "results": results}, sort_keys=True)
        )


def _bump_manifest_registry_version(version_str: str) -> str:
    """Increment the v-number in a MANIFEST version string.

    Examples:
        '3.11-v36-12parent-A'  →  '3.11-v37-12parent-A'
        'v5'                   →  'v6'
        'no-version'           →  'no-version-v1'
    """
    bumped, count = re.subn(r"v(\d+)", lambda m: f"v{int(m.group(1)) + 1}", version_str, count=1)
    if count == 0:
        return version_str + "-v1"
    return bumped


def write_manifest_with_version_bump() -> str:
    """Bump the registry version in MANIFEST.json and write it back atomically.

    Reads the current MANIFEST.json, increments the v-number in the ``version``
    field (e.g. v36 → v37), and writes the file back.  Must be called on every
    programmatic write so the version always reflects the current byte content.

    Returns the new version string.
    """
    manifest = get_manifest()
    if not isinstance(manifest, dict):
        raise ValueError("MANIFEST.json is missing or not a valid JSON object")
    current_version = str(manifest.get("version") or "")
    new_version = _bump_manifest_registry_version(current_version)
    manifest["version"] = new_version
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return new_version


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version_from_filename(path: Path) -> str:
    stem = path.stem
    match = re.search(r"(?:^|[_\-.])v?(\d+(?:[._]\d+)*)(?:$|[_\-.])", stem, flags=re.IGNORECASE)
    if match:
        return match.group(1).replace("_", ".")
    match = re.search(r"v(\d+)$", stem, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _runtime_file_entry(path: Path, file_id: str = "") -> Dict[str, str]:
    resolved = path if path.exists() else _resolve_file_from_name(str(path.name))
    payload = _load_json_file(resolved) if resolved.exists() and resolved.suffix.lower() == ".json" else {}
    inferred_file_id = file_id
    if not inferred_file_id and isinstance(payload, dict):
        inferred_file_id = str(
            payload.get("file_id")
            or payload.get("model")
            or payload.get("name")
            or payload.get("title")
            or ""
        ).strip()
    if not inferred_file_id:
        inferred_file_id = resolved.stem
    version = ""
    if isinstance(payload, dict):
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        version = str(payload.get("version") or meta.get("version") or metadata.get("version") or "").strip()
    if not version:
        version = _version_from_filename(resolved)
    return {
        "file_id": inferred_file_id,
        "version": version,
        "hash": f"sha256:{_file_sha256(resolved)}" if resolved.exists() else "",
    }


def _runtime_governance_paths() -> List[tuple[Path, str]]:
    overview_files = _get_active_points_overview_files()
    return [
        (SYSTEM_PROMPT_PATH, "system_prompt_validert"),
        (RAG_LEGAL_PATH, "rag_legal_framework_validert"),
        (RAG_RULES_PATH, "rag_validert_rules"),
        (RAG_LANGUAGE_PATH, "rag_language_rules"),
        (_get_dynamic_file("existing_runtime_modules_unchanged", "scoring_model_file", SCORING_MODEL_PATH), "rag_scoring_model_validert"),
        (_get_dynamic_file("existing_runtime_modules_unchanged", "output_schema_file", OUTPUT_SCHEMA_PATH), "output_schema_validert"),
        (_get_dynamic_file("existing_runtime_modules_unchanged", "scoring_policy_overlay_file", OUTPUT_OVERLAY_PATH), "scoring_policy_validert_output_overlay"),
        (DETECTED_POINTS_SCHEMA_PATH, "validert_detected_points_schema"),
        (FEEDBACK_SCHEMA_PATH, "validert_feedback_schema"),
        (_get_dynamic_file("existing_runtime_modules_unchanged", "category_config_file", CATEGORY_CONFIG_PATH), "validert_category_config"),
        (_get_dynamic_file("existing_runtime_modules_unchanged", "legal_compliance_rules_file", LEGALITY_RULES_PATH), "validert_legal_compliance_rules"),
        (LEGALITY_ARKAT_TEMPLATES_PATH, "validert_arkat_templates_lovlighet"),
        (LEGALITY_ARKAT_MAP_PATH, "validert_lovlighet_to_arkat_map"),
        (_get_dynamic_file("existing_runtime_modules_unchanged", "no_prosjektering_guardrails_file", LEGALITY_GUARDRAILS_PATH), "validert_no_prosjektering_guardrails"),
        (MANIFEST_PATH, "MANIFEST"),
        (get_active_pipeline_file_path(), "validert_orchestrator_pipeline"),
        (ARKAT_SEMANTIC_RULES_PATH, "arkat_semantic_rules"),
        (ARKAT_EVALUATION_PIPELINE_STEP_PATH, "arkat_evaluation_pipeline_step"),
        (DOMMER_B_SYSTEM_PROMPT_PATH, "dommer_b_system_prompt"),
        (ARKAT_ERROR_DEDUCTION_MAPPING_PATH, "arkat_error_to_deduction_mapping"),
        (REPORT_FORMAT_DETECTION_PATH, "report_format_detection"),
        (ARKAT_CANONICAL_EXAMPLES_PATH, "arkat_canonical_examples"),
        (ACTIVE_CONFIG_PATH, "validert_active_config"),
        (overview_files["canonical"], "validert_canonical_points"),
        (overview_files["mapping"], "validert_points_overview_mapping"),
        (overview_files["overlay"], "validert_ui_overlay"),
        (_get_dynamic_file("existing_runtime_modules_unchanged", "age_service_life_validation_file", AGE_SERVICE_LIFE_VALIDATION_PATH), "tg2_tg3_age_service_life_validation"),
        (_get_dynamic_file("active_points_overview", "element_hierarchy_file", ELEMENT_HIERARCHY_PATH), "validert_element_hierarchy"),
        (ELEMENT_HIERARCHY_SYNONYMS_PATH, "validert_element_hierarchy_synonyms"),
        (_get_dynamic_file("active_points_overview", "status_rules_file", PUNKT_STATUS_RULES_PATH), "validert_punkt_for_punkt_status_rules"),
        (_get_dynamic_file("active_points_overview", "scoring_hooks_file", PUNKT_SCORING_HOOKS_PATH), "validert_punkt_for_punkt_scoring_hooks"),
        (_get_dynamic_file("supporting_modules", "room_instances_schema_file", ROOM_INSTANCES_SCHEMA_PATH), "validert_room_instances_schema"),
        (_get_dynamic_file("supporting_modules", "room_instance_extraction_rules_file", ROOM_INSTANCE_EXTRACTION_RULES_PATH), "validert_room_instance_extraction_rules"),
        (_get_dynamic_file("supporting_modules", "room_instance_arkat_coverage_rules_file", ROOM_INSTANCE_ARKAT_COVERAGE_PATH), "validert_room_instance_arkat_coverage_rules"),
        (_get_dynamic_file("supporting_modules", "room_instance_rollup_format_file", ROOM_INSTANCE_ROLLUP_FORMAT_PATH), "validert_room_instance_rollup_format"),
        (_get_dynamic_file("existing_runtime_modules_unchanged", "optional_tg_forbidden_meta_rule_file", META_RULE_OPTIONAL_TG_FORBIDDEN_PATH), "validert_optional_tg_forbidden_meta_rule"),
        (_get_dynamic_file("existing_runtime_modules_unchanged", "non_mandatory_assessed_meta_rule_file", META_RULE_NON_MANDATORY_ASSESSED_PATH), "validert_non_mandatory_assessed_meta_rule"),
        (MANDATORY_EXPLANATION_ONLY_PATH, "validert_mandatory_explanation_only_rules"),
        (MIGRATION_MAP_V33_TO_V34_PATH, "validert_migration_map"),
        (_get_dynamic_file("active_points_overview", "forskrift_matrix_file", FORSKRIFT_MATRIX_PATH), "validert_forskrift_matrix"),
        (INCOMPLETE_ANALYSIS_POLICY_PATH, "validert_incomplete_analysis_policy"),
    ]


def get_runtime_manifest(analysis_mode: str) -> Dict[str, object]:
    manifest = get_manifest()
    pipeline_version = str(manifest.get("version") or get_prompt_context_sha()) if isinstance(manifest, dict) else get_prompt_context_sha()
    loaded = []
    seen = set()
    for path, file_id in _runtime_governance_paths():
        key = str(path)
        if key in seen or not path.exists():
            continue
        seen.add(key)
        loaded.append(_runtime_file_entry(path, file_id))
    return {
        "pipeline_version": pipeline_version,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "analysis_mode": analysis_mode or "full",
        "loaded": loaded,
        "runtime_code_files": get_runtime_code_pin_results(),
    }

def _get_dynamic_file(category: str, key: str, fallback_path: Path) -> Path:
    cfg = get_active_config()
    if isinstance(cfg, dict):
        section = cfg.get(category)
        if isinstance(section, dict):
            filename = section.get(key)
            if isinstance(filename, str) and filename.strip():
                resolved = _resolve_file_from_name(filename)
                if resolved.exists():
                    return resolved
    return fallback_path


def _get_active_points_overview_files() -> Dict[str, Path]:
    cfg = get_active_config()
    overview_cfg = cfg.get("active_points_overview") if isinstance(cfg, dict) else {}
    if not isinstance(overview_cfg, dict):
        overview_cfg = {}

    canonical_name = overview_cfg.get("canonical_points_file")
    mapping_name = overview_cfg.get("mapping_file")
    overlay_name = overview_cfg.get("ui_overlay_file")

    canonical = (
        _resolve_file_from_name(canonical_name)
        if isinstance(canonical_name, str) and canonical_name.strip()
        else CANONICAL_POINTS_V30_PATH
    )
    if not canonical.exists():
        canonical = CANONICAL_POINTS_V30_PATH

    mapping = (
        _resolve_file_from_name(mapping_name)
        if isinstance(mapping_name, str) and mapping_name.strip()
        else FILES_DIR / "validert_points_overview_mapping_v1.1.json"
    )
    overlay = (
        _resolve_file_from_name(overlay_name)
        if isinstance(overlay_name, str) and overlay_name.strip()
        else FILES_DIR / "validert_ui_overlay_v1.1.json"
    )
    return {
        "canonical": canonical,
        "mapping": mapping,
        "overlay": overlay,
    }


def get_system_prompt() -> str:
    prompt = _read_text(SYSTEM_PROMPT_PATH)
    semantic_rules = get_arkat_semantic_rules()
    instruction = (
        semantic_rules.get("evaluation_instruction", {}).get("prompt")
        if isinstance(semantic_rules, dict)
        else ""
    )
    if not isinstance(instruction, str) or not instruction.strip():
        return prompt
    block = (
        "\n\n===== AKTIV ARKAT SEMANTISK EVALUERINGSINSTRUKS =====\n"
        f"{instruction.strip()}"
    )
    if instruction.strip() in prompt:
        return prompt
    return f"{prompt.rstrip()}{block}"


def get_rag_sections() -> Dict[str, str]:
    return {
        "legal_framework": _read_text(RAG_LEGAL_PATH),
        "validert_rules": _read_text(RAG_RULES_PATH),
        "language_rules": _read_text(RAG_LANGUAGE_PATH),
    }


def get_scoring_model_text() -> str:
    path = _get_dynamic_file("existing_runtime_modules_unchanged", "scoring_model_file", SCORING_MODEL_PATH)
    return _read_text(path)

def get_output_schema_text() -> str:
    path = _get_dynamic_file("existing_runtime_modules_unchanged", "output_schema_file", OUTPUT_SCHEMA_PATH)
    return _read_text(path)

def get_output_overlay_text() -> str:
    path = _get_dynamic_file("existing_runtime_modules_unchanged", "scoring_policy_overlay_file", OUTPUT_OVERLAY_PATH)
    return _read_text(path)


def get_detected_points_schema_text() -> str:
    # Not purely dynamic via config usually, but we keep the fallback structure anyway
    return _read_text(DETECTED_POINTS_SCHEMA_PATH)

def get_feedback_schema_text() -> str:
    return _read_text(FEEDBACK_SCHEMA_PATH)

def get_forskrift_matrix_text() -> str:
    path = _get_dynamic_file("active_points_overview", "forskrift_matrix_file", FORSKRIFT_MATRIX_PATH)
    return _read_text(path)

def get_mandatory_explanation_only_rules_text() -> str:
    return _read_text(MANDATORY_EXPLANATION_ONLY_PATH)

def get_category_config_text() -> str:
    path = _get_dynamic_file("existing_runtime_modules_unchanged", "category_config_file", CATEGORY_CONFIG_PATH)
    return _read_text(path)

def get_legality_rules_text() -> str:
    path = _get_dynamic_file("existing_runtime_modules_unchanged", "legal_compliance_rules_file", LEGALITY_RULES_PATH)
    return _read_text(path)

def get_legality_arkat_templates_text() -> str:
    return _read_text(LEGALITY_ARKAT_TEMPLATES_PATH)

def get_legality_arkat_map_text() -> str:
    return _read_text(LEGALITY_ARKAT_MAP_PATH)

def get_legality_guardrails_text() -> str:
    path = _get_dynamic_file("existing_runtime_modules_unchanged", "no_prosjektering_guardrails_file", LEGALITY_GUARDRAILS_PATH)
    return _read_text(path)

def get_orchestrator_pipeline_text() -> str:
    return _read_text(get_active_pipeline_file_path())


def get_arkat_semantic_rules() -> Dict:
    return _load_json_file(ARKAT_SEMANTIC_RULES_PATH)


def get_arkat_semantic_rules_text() -> str:
    return _read_text(ARKAT_SEMANTIC_RULES_PATH)


def get_arkat_evaluation_pipeline_step() -> Dict:
    return _load_json_file(ARKAT_EVALUATION_PIPELINE_STEP_PATH)


def get_arkat_evaluation_pipeline_step_text() -> str:
    return _read_text(ARKAT_EVALUATION_PIPELINE_STEP_PATH)


def get_dommer_b_system_prompt_text() -> str:
    return _read_text(DOMMER_B_SYSTEM_PROMPT_PATH)


def get_arkat_error_deduction_mapping() -> Dict:
    return _load_json_file(ARKAT_ERROR_DEDUCTION_MAPPING_PATH)


def get_arkat_error_deduction_mapping_text() -> str:
    return _read_text(ARKAT_ERROR_DEDUCTION_MAPPING_PATH)


def get_report_format_detection() -> Dict:
    return _load_json_file(REPORT_FORMAT_DETECTION_PATH)


def get_report_format_detection_text() -> str:
    return _read_text(REPORT_FORMAT_DETECTION_PATH)


def get_arkat_canonical_examples() -> Dict:
    return _load_json_file(ARKAT_CANONICAL_EXAMPLES_PATH)


def get_arkat_canonical_examples_text() -> str:
    return _read_text(ARKAT_CANONICAL_EXAMPLES_PATH)


def get_arkat_canonical_examples_report_context_text() -> str:
    examples = get_arkat_canonical_examples()
    if not isinstance(examples, dict):
        return ""
    compact = {
        "meta": examples.get("meta") or {},
        "retrieval_guidance": examples.get("retrieval_guidance") or {},
        "examples_count": len(examples.get("examples") or []) if isinstance(examples.get("examples"), list) else 0,
        "runtime_note": "Full examples are injected only into Dommer B point-level calls via injection_template/example_per_item_template.",
    }
    return json.dumps(compact, ensure_ascii=False, sort_keys=True)


def get_age_service_life_validation_text() -> str:
    path = _get_dynamic_file("existing_runtime_modules_unchanged", "age_service_life_validation_file", AGE_SERVICE_LIFE_VALIDATION_PATH)
    return _read_text(path)

def get_element_hierarchy_text() -> str:
    path = _get_dynamic_file("active_points_overview", "element_hierarchy_file", ELEMENT_HIERARCHY_PATH)
    return _read_text(path)


def get_building_part_whitelist_v21() -> Dict:
    """Load whitelist v2.1: strict canonical + alias, legal tagging, hard reject regex."""
    try:
        return json.loads(_read_text(BUILDING_PART_WHITELIST_V21_PATH))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {}


def get_building_part_whitelist_v22() -> Dict:
    """Load whitelist v2.2: normalization, reject_if_regex, canonical building parts, instance extraction."""
    try:
        return json.loads(_read_text(BUILDING_PART_WHITELIST_V22_PATH))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {}


def get_canonical_points_v30() -> Dict:
    """Load canonical points v3.0: fixed list for punkt-for-punkt oversikt."""
    files = _get_active_points_overview_files()
    canonical_path = files["canonical"]
    if canonical_path.exists():
        raw = canonical_path.read_bytes()
        # Fail loudly when runtime points file is empty (INV-10 extension).
        if not raw.strip():
            raise ValueError(f"Canonical points file is empty at runtime: {canonical_path}")
    return _load_json_file(canonical_path)


def get_points_overview_mapping_config() -> Dict:
    files = _get_active_points_overview_files()
    return _load_json_file(files["mapping"])


def get_ui_overlay_config() -> Dict:
    files = _get_active_points_overview_files()
    return _load_json_file(files["overlay"])


def get_building_part_whitelist() -> Dict[str, set]:
    """Load building-part names and synonyms for segment validation. Returns dict with 'names' and 'blocklist' sets."""
    names: set = set()
    blocklist: set = set()
    try:
        hierarchy = json.loads(_read_text(ELEMENT_HIERARCHY_PATH))
        for el in hierarchy.get("elements", []) or []:
            if not isinstance(el, dict):
                continue
            name = (el.get("name") or "").strip()
            if name:
                name_lower = name.lower()
                names.add(name_lower)
                for word in re.sub(r"[-/()]", " ", name_lower).split():
                    if len(word) >= 3:
                        names.add(word)
    except (json.JSONDecodeError, OSError):
        pass
    try:
        syn = json.loads(_read_text(ELEMENT_HIERARCHY_SYNONYMS_PATH))
        for key, vals in (syn.get("elements") or {}).items():
            if isinstance(vals, list):
                for v in vals:
                    if isinstance(v, str) and v.strip():
                        names.add(v.strip().lower())
            elif isinstance(vals, str):
                names.add(vals.strip().lower())
    except (json.JSONDecodeError, OSError):
        pass
    blocklist = {"etg", "kostnadspekulasjon", "vurderinger", "yttligere"}
    return {"names": names, "blocklist": blocklist}


def get_element_hierarchy_synonyms_text() -> str:
    return _read_text(ELEMENT_HIERARCHY_SYNONYMS_PATH)


def get_punkt_status_rules_text() -> str:
    path = _get_dynamic_file("active_points_overview", "status_rules_file", PUNKT_STATUS_RULES_PATH)
    return _read_text(path)

def get_punkt_scoring_hooks_text() -> str:
    path = _get_dynamic_file("active_points_overview", "scoring_hooks_file", PUNKT_SCORING_HOOKS_PATH)
    return _read_text(path)

def get_room_instances_schema_text() -> str:
    path = _get_dynamic_file("supporting_modules", "room_instances_schema_file", ROOM_INSTANCES_SCHEMA_PATH)
    return _read_text(path)

def get_room_instance_extraction_rules_text() -> str:
    path = _get_dynamic_file("supporting_modules", "room_instance_extraction_rules_file", ROOM_INSTANCE_EXTRACTION_RULES_PATH)
    return _read_text(path)

def get_room_instance_arkat_coverage_text() -> str:
    path = _get_dynamic_file("supporting_modules", "room_instance_arkat_coverage_rules_file", ROOM_INSTANCE_ARKAT_COVERAGE_PATH)
    return _read_text(path)

def get_room_instance_rollup_format_text() -> str:
    path = _get_dynamic_file("supporting_modules", "room_instance_rollup_format_file", ROOM_INSTANCE_ROLLUP_FORMAT_PATH)
    return _read_text(path)


def get_optional_tg_forbidden_meta_rule_text() -> str:
    path = _get_dynamic_file("existing_runtime_modules_unchanged", "optional_tg_forbidden_meta_rule_file", META_RULE_OPTIONAL_TG_FORBIDDEN_PATH)
    return _read_text(path)

def get_non_mandatory_assessed_meta_rule_text() -> str:
    path = _get_dynamic_file("existing_runtime_modules_unchanged", "non_mandatory_assessed_meta_rule_file", META_RULE_NON_MANDATORY_ASSESSED_PATH)
    return _read_text(path)


def get_lovlighet_patch_el_tg_text() -> str:
    return _read_text(LOVLIGHET_PATCH_EL_TG_PATH)


def get_lovlighet_patch_hms_tg_text() -> str:
    return _read_text(LOVLIGHET_PATCH_HMS_TG_PATH)


def get_migration_map() -> Dict:
    return _load_json_file(MIGRATION_MAP_V33_TO_V34_PATH)


def get_scoring_model_info() -> Dict[str, str]:
    text = get_scoring_model_text()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {}
    return {
        "model_id": str(payload.get("model", "")),
        "version": str(payload.get("version", "")),
        "updated_at": str(payload.get("updated_at", "")),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def build_prompt_context() -> str:
    rag_sections = get_rag_sections()
    return "\n\n".join(
        [
            "===== RAG – JURIDISK RAMMEVERK =====\n" + rag_sections["legal_framework"],
            "===== RAG – VALIDERT SYSTEMREGLER =====\n" + rag_sections["validert_rules"],
            "===== RAG – SPRÅK- OG STRUKTURREGLER =====\n" + rag_sections["language_rules"],
            "===== SCORING MODEL =====\n" + get_scoring_model_text(),
            "===== OUTPUT SCHEMA =====\n" + get_output_schema_text(),
            "===== OUTPUT OVERLAY POLICY =====\n" + get_output_overlay_text(),
            "===== DETECTED POINTS SCHEMA =====\n" + get_detected_points_schema_text(),
            "===== FEEDBACK SCHEMA =====\n" + get_feedback_schema_text(),
            "===== CATEGORY CONFIG =====\n" + get_category_config_text(),
            "===== LEGALITY RULES =====\n" + get_legality_rules_text(),
            "===== LEGALITY ARKAT TEMPLATES =====\n" + get_legality_arkat_templates_text(),
            "===== LEGALITY ARKAT MAP =====\n" + get_legality_arkat_map_text(),
            "===== LEGALITY GUARDRAILS =====\n" + get_legality_guardrails_text(),
            "===== MANIFEST =====\n" + get_manifest_text(),
            "===== ORCHESTRATOR PIPELINE =====\n" + get_orchestrator_pipeline_text(),
            "===== ARKAT SEMANTIC RULES =====\n" + get_arkat_semantic_rules_text(),
            "===== REPORT FORMAT DETECTION =====\n" + get_report_format_detection_text(),
            "===== ARKAT CANONICAL EXAMPLES METADATA =====\n" + get_arkat_canonical_examples_report_context_text(),
            "===== ACTIVE CONFIG =====\n" + json.dumps(get_active_config(), ensure_ascii=False, sort_keys=True),
            "===== CANONICAL POINTS (ACTIVE) =====\n" + json.dumps(get_canonical_points_v30(), ensure_ascii=False, sort_keys=True),
            "===== POINTS OVERVIEW MAPPING =====\n" + json.dumps(get_points_overview_mapping_config(), ensure_ascii=False, sort_keys=True),
            "===== POINTS OVERVIEW UI OVERLAY =====\n" + json.dumps(get_ui_overlay_config(), ensure_ascii=False, sort_keys=True),
            "===== AGE/SERVICE LIFE VALIDATION =====\n" + get_age_service_life_validation_text(),
            "===== META RULE ROUTING =====\n" + META_RULE_ROUTING,
            "===== ELEMENT HIERARCHY =====\n" + get_element_hierarchy_text(),
            "===== ELEMENT HIERARCHY SYNONYMS =====\n" + get_element_hierarchy_synonyms_text(),
            "===== PUNKT-FOR-PUNKT STATUS RULES =====\n" + get_punkt_status_rules_text(),
            "===== PUNKT-FOR-PUNKT SCORING HOOKS =====\n" + get_punkt_scoring_hooks_text(),
            "===== ROOM INSTANCES SCHEMA =====\n" + get_room_instances_schema_text(),
            "===== ROOM INSTANCE EXTRACTION RULES =====\n" + get_room_instance_extraction_rules_text(),
            "===== ROOM INSTANCE ARKAT COVERAGE RULES =====\n" + get_room_instance_arkat_coverage_text(),
            "===== ROOM INSTANCE ROLLUP FORMAT =====\n" + get_room_instance_rollup_format_text(),
            "===== META RULE: OPTIONAL TG FORBIDDEN =====\n" + get_optional_tg_forbidden_meta_rule_text(),
            "===== META RULE: NON-MANDATORY ASSESSED =====\n" + get_non_mandatory_assessed_meta_rule_text(),
            "===== META RULE: MANDATORY EXPLANATION ONLY =====\n" + get_mandatory_explanation_only_rules_text(),
            "===== MIGRATION MAP =====\n" + json.dumps(get_migration_map(), ensure_ascii=False, sort_keys=True),
            "===== FORSKRIFT MATRIX =====\n" + get_forskrift_matrix_text(),
        ]
    ).strip()


def get_prompt_context_sha() -> str:
    # Include get_system_prompt(): it is not part of build_prompt_context() (that block is user-side RAG),
    # but it must bust analysis cache when system_prompt_validert_*.txt changes.
    context = build_prompt_context()
    system = get_system_prompt()
    # Full canonical examples are injected only in Dommer B point calls, but still
    # participate in the cache key so example-file changes invalidate analyses.
    dommer_b = get_dommer_b_system_prompt_text()
    canonical_examples = get_arkat_canonical_examples_text()
    return hashlib.sha256(f"{context}\n{system}\n{dommer_b}\n{canonical_examples}".encode("utf-8")).hexdigest()
