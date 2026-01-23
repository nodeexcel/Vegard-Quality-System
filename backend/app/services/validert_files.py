from pathlib import Path
from typing import Dict
import hashlib
import json

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
        ]
    ).strip()


def get_prompt_context_sha() -> str:
    context = build_prompt_context()
    return hashlib.sha256(context.encode("utf-8")).hexdigest()
