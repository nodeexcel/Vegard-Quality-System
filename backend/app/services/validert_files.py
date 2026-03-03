from pathlib import Path
from typing import Dict
import hashlib
import json
import re

FILES_DIR = Path(__file__).resolve().parents[3] / "files"

SYSTEM_PROMPT_PATH = FILES_DIR / "system_prompt_validert_v1.6.txt"
RAG_LEGAL_PATH = FILES_DIR / "rag_legal_framework_validert_v1.4.txt"
RAG_RULES_PATH = FILES_DIR / "rag_validert_rules_v1.4.txt"
RAG_LANGUAGE_PATH = FILES_DIR / "rag_language_rules_v1.6.txt"
SCORING_MODEL_PATH = FILES_DIR / "rag_scoring_model_validert_v1.6.json"
OUTPUT_SCHEMA_PATH = FILES_DIR / "output_schema_validert_v1.5.json"
OUTPUT_OVERLAY_PATH = FILES_DIR / "scoring_policy.validert_output_overlay.v1.1.json"
DETECTED_POINTS_SCHEMA_PATH = FILES_DIR / "validert_detected_points_v1.0.schema.json"
FEEDBACK_SCHEMA_PATH = FILES_DIR / "validert_feedback_v1.1.schema.json"
CATEGORY_CONFIG_PATH = FILES_DIR / "validert_category_config_v1_0.json"
LEGALITY_RULES_PATH = FILES_DIR / "validert_legal_compliance_rules_v1_1.json"
LEGALITY_ARKAT_TEMPLATES_PATH = FILES_DIR / "validert_arkat_templates_lovlighet_v1_1.json"
LEGALITY_ARKAT_MAP_PATH = FILES_DIR / "validert_lovlighet_to_arkat_map_v1_1.json"
LEGALITY_GUARDRAILS_PATH = FILES_DIR / "validert_no_prosjektering_guardrails_v1_1.json"
ORCHESTRATOR_PIPELINE_PATH = FILES_DIR / "validert_orchestrator_pipeline_v1.json"
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


def get_system_prompt() -> str:
    return _read_text(SYSTEM_PROMPT_PATH)


def get_rag_sections() -> Dict[str, str]:
    return {
        "legal_framework": _read_text(RAG_LEGAL_PATH),
        "validert_rules": _read_text(RAG_RULES_PATH),
        "language_rules": _read_text(RAG_LANGUAGE_PATH),
    }


def get_scoring_model_text() -> str:
    return _read_text(SCORING_MODEL_PATH)


def get_output_schema_text() -> str:
    return _read_text(OUTPUT_SCHEMA_PATH)


def get_output_overlay_text() -> str:
    return _read_text(OUTPUT_OVERLAY_PATH)


def get_detected_points_schema_text() -> str:
    return _read_text(DETECTED_POINTS_SCHEMA_PATH)


def get_feedback_schema_text() -> str:
    return _read_text(FEEDBACK_SCHEMA_PATH)

def get_category_config_text() -> str:
    return _read_text(CATEGORY_CONFIG_PATH)


def get_legality_rules_text() -> str:
    return _read_text(LEGALITY_RULES_PATH)


def get_legality_arkat_templates_text() -> str:
    return _read_text(LEGALITY_ARKAT_TEMPLATES_PATH)


def get_legality_arkat_map_text() -> str:
    return _read_text(LEGALITY_ARKAT_MAP_PATH)


def get_legality_guardrails_text() -> str:
    return _read_text(LEGALITY_GUARDRAILS_PATH)


def get_orchestrator_pipeline_text() -> str:
    return _read_text(ORCHESTRATOR_PIPELINE_PATH)


def get_age_service_life_validation_text() -> str:
    return _read_text(AGE_SERVICE_LIFE_VALIDATION_PATH)


def get_element_hierarchy_text() -> str:
    return _read_text(ELEMENT_HIERARCHY_PATH)


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
    return _read_text(PUNKT_STATUS_RULES_PATH)


def get_punkt_scoring_hooks_text() -> str:
    return _read_text(PUNKT_SCORING_HOOKS_PATH)


def get_room_instances_schema_text() -> str:
    return _read_text(ROOM_INSTANCES_SCHEMA_PATH)


def get_room_instance_extraction_rules_text() -> str:
    return _read_text(ROOM_INSTANCE_EXTRACTION_RULES_PATH)


def get_room_instance_arkat_coverage_text() -> str:
    return _read_text(ROOM_INSTANCE_ARKAT_COVERAGE_PATH)


def get_room_instance_rollup_format_text() -> str:
    return _read_text(ROOM_INSTANCE_ROLLUP_FORMAT_PATH)


def get_optional_tg_forbidden_meta_rule_text() -> str:
    return _read_text(META_RULE_OPTIONAL_TG_FORBIDDEN_PATH)


def get_non_mandatory_assessed_meta_rule_text() -> str:
    return _read_text(META_RULE_NON_MANDATORY_ASSESSED_PATH)


def get_lovlighet_patch_el_tg_text() -> str:
    return _read_text(LOVLIGHET_PATCH_EL_TG_PATH)


def get_lovlighet_patch_hms_tg_text() -> str:
    return _read_text(LOVLIGHET_PATCH_HMS_TG_PATH)


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
            "===== ORCHESTRATOR PIPELINE =====\n" + get_orchestrator_pipeline_text(),
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
            "===== LOVLIGHET PATCH EL TG =====\n" + get_lovlighet_patch_el_tg_text(),
            "===== LOVLIGHET PATCH HMS TG =====\n" + get_lovlighet_patch_hms_tg_text(),
        ]
    ).strip()


def get_prompt_context_sha() -> str:
    context = build_prompt_context()
    return hashlib.sha256(context.encode("utf-8")).hexdigest()
