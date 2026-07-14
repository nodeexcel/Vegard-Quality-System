from openai import OpenAI
from typing import Dict, List, Optional, Tuple
import re
from datetime import datetime, timezone
from functools import lru_cache
import json
import logging
import hashlib
import uuid
import time
import unicodedata
from pathlib import Path
from app.config import settings
from app.schemas import AnalysisResult, ComponentBase, FindingBase
from app.services.arkat_semantic_pipeline import (
    _is_semantically_missing_text,
    _looks_like_structured_point_id,
    _trim_text_to_point_window,
    finalize_client_arkat_semantic_pipeline_output,
    run_client_arkat_semantic_pipeline as _run_client_arkat_semantic_pipeline_service,
)
from app.services.system_prompt import SYSTEM_PROMPT
from app.services.validert_files import (
    build_prompt_context,
    get_building_part_whitelist,
    get_building_part_whitelist_v21,
    get_building_part_whitelist_v22,
    get_canonical_points_v30,
    get_category_config_text,
    get_arkat_error_deduction_mapping_text,
    get_legality_arkat_map_text,
    get_legality_arkat_templates_text,
    get_legality_rules_text,
    get_prompt_context_sha,
    get_scoring_model_info,
    get_scoring_model_text,
    get_ui_overlay_config,
    get_points_overview_mapping_config,
    get_migration_map,
    get_runtime_manifest,
)

logger = logging.getLogger(__name__)
_analysis_debug_used_run_id = None


class IncompleteAnalysisError(Exception):
    def __init__(
        self,
        message: str,
        reasons: List[str],
        run_meta: Dict[str, object],
        detected_points_payload: Optional[Dict[str, object]] = None,
        document_hash: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.reasons = reasons
        self.run_meta = run_meta
        self.detected_points_payload = detected_points_payload
        self.document_hash = document_hash


def _should_log_debug(run_id: str) -> bool:
    global _analysis_debug_used_run_id
    if settings.ANALYSIS_DEBUG_RUN_ID and settings.ANALYSIS_DEBUG_RUN_ID != run_id:
        return False
    if settings.ANALYSIS_DEBUG_ONCE and _analysis_debug_used_run_id not in (None, run_id):
        return False
    enabled = settings.ANALYSIS_DEBUG or settings.ANALYSIS_DEBUG_ONCE or bool(settings.ANALYSIS_DEBUG_RUN_ID)
    if not enabled:
        return False
    if settings.ANALYSIS_DEBUG_ONCE:
        if _analysis_debug_used_run_id is None:
            _analysis_debug_used_run_id = run_id
    return True


def _log_debug(run_id: str, event: str, payload: Dict[str, object]) -> None:
    if _should_log_debug(run_id):
        logger.info("analysis_debug run_id=%s event=%s payload=%s", run_id, event, json.dumps(payload, ensure_ascii=True, sort_keys=True))

PAGE_MARKER_RE = re.compile(r"\[SIDE (\d+)\]\n", re.IGNORECASE)
# Pages with these phrases are scanned for point-numbered blocks and merged into segment text (TG2/TG3 assessments).
SUMMARY_MARKERS = [
    "oppsummering",
    "takstmannens vurdering",
    "takstmannens vurdering ved tg2",
    "takstmannens vurdering ved tg3",
    "summary",
    "vurdering av tg 2",
    "vurdering av tg 3",
    "vurdering av tg",
]
# Allow Pkt, Punkt, PUNKT, optional TG prefix; capture number (with optional trailing dot normalized).
POINT_HEADER_RE = re.compile(
    r"^\s*(?:TG\s*(?:IU|0|1|2|3)\s+)?(?:PUNKT|Punkt|Pkt)\s*(\d+(?:\.\d+){0,4})\.?\s*(?:[-–—:]?\s*(.*\S)?)?$",
    re.IGNORECASE,
)
# Fallback: line that begins with a dotted numeric point id (e.g. "6.2" or "6.2.1").
# Important: require at least one dot so plain leading numbers in prose (e.g. "2-lags ...")
# do not become false point headers.
POINT_HEADER_FALLBACK_RE = re.compile(r"^\s*(\d+(?:\.\d+){1,4})\.?\s*(?:[-–—:]?\s*(.*\S)?)?$")
SUMMARY_INLINE_POINT_RE = re.compile(r"\b(\d+(?:\.\d+){1,4})\.?\b")
# Accept common variants: "TG2", "TG 2", "TG-2", "TGIU", "TG IU".
TG_RE = re.compile(r"(?i)\bTG(?:\s*[-]?\s*(?:0|1|2|3|IU))\b")
DATE_POINT_ID_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
# TG3 cost: normalize before matching (NFKC, dash harmonization, zero-width cleanup).
ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200D\u2060\uFEFF]")
NBSP_RE = re.compile(r"[\u00A0\u202F]")
WHITESPACE_RE = re.compile(r"\s+")
_DASH_TRANSLATION_TABLE = str.maketrans({
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2012": "-",  # figure dash
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2015": "-",  # horizontal bar
    "\u2212": "-",  # minus sign
})


def _normalize_tg3_cost_text(text: str) -> str:
    if not text or not isinstance(text, str):
        return ""
    # Also strip stray CJK/private-use glyphs that leak in from broken PDF encodings
    # so patterns like "Kostnadsestimat: 50 000 - 100 000" still match even when the
    # underlying text layer contains garbage characters between letters.
    s = _strip_suspicious_cjk(text)
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_DASH_TRANSLATION_TABLE)
    s = NBSP_RE.sub(" ", s)
    s = ZERO_WIDTH_RE.sub("", s)
    replacements = (
        (r"\blstand\b", "tilstand"),
        (r"\bLstand\b", "Tilstand"),
        (r"\blstrekkelig\b", "tilstrekkelig"),
        (r"\bLstrekkelig\b", "Tilstrekkelig"),
    )
    for pattern, replacement in replacements:
        s = re.sub(pattern, replacement, s)
    return WHITESPACE_RE.sub(" ", s).strip()

# E1/E2 policy: PASS = interval OR cost class; E2 MEDIUM = single amount only; E1 HIGH = none.
# Numeric interval must be accepted even without the word "Kostnadsestimat".
_AMOUNT_RE = r"(?:\d{1,3}(?:[ .]\d{3})+|\d+)"
TG3_INTERVAL_RE = re.compile(
    rf"(?ix)\b(?:kr\.?\s*|nok\s*)?{_AMOUNT_RE}\s*(?:-|til)\s*(?:kr\.?\s*|nok\s*)?{_AMOUNT_RE}\b"
)
# COST CLASS (PASS): lav / middels / høy
TG3_COST_CLASS_RE = re.compile(
    r"(?ix)"
    r"(?:\b(?:kostnadsklasse(?:r)?|kostnadskategori(?:er)?|kostnadsnivå|kostnadsramme|utbedringskostnad(?:en)?|kostnad|prisnivå)\b[^.\n]{0,50}\b(?:lav|middels|høy)\b)"
    r"|(?:\b(?:lav|middels|høy)\b[^.\n]{0,24}\b(?:kostnad|kostnadsnivå|kostnadsklasse|prisnivå)\b)"
    r"|(?:\b(?:moderat|betydelig)\b[^.\n]{0,24}\b(?:kostnad|kostnadsnivå)\b)"
    r"|(?:\b(?:lav|middels|høy)\s*/\s*(?:lav|middels|høy)\b)"
)
# SINGLE AMOUNT (E2): one amount only, optional ca./kr.
TG3_SINGLE_AMOUNT_RE = re.compile(
    rf"(?ix)\b(?:kostnad(?:sestimat)?|kostnadsanslag|estimert\s+kostnad|utbedringskostnad(?:er)?)\b"
    rf"[^0-9]{{0,50}}(?:ca\.?\s*)?(?:kr\.?\s*)?{_AMOUNT_RE}(?:\s*(?:,-|kr\.?))?\b(?!\s*(?:-|til)\s*\d)"
)
TILTAK_HEADING_RE = re.compile(r"(?ix)\btiltak\b\s*:")
ANDRE_TILTAK_RE = re.compile(r"(?ix)\bandre\s+tiltak\b")
KONSEKVENS_TILTAK_RE = re.compile(r"(?ix)\bkonsekvens\s*(?:/|-|og)\s*tiltak\b")
VAGUE_MEASURE_GUIDANCE_RE = re.compile(
    r"(?ix)\b(?:kan|bør|anbefales)\s+(?:vurderes|ses\s+på|følges\s+opp)\b|"
    r"\bvurder(?:es)?\s+(?:nærmere|videre)\b|"
    r"\bom\s+ønskelig\b"
)
SCHEMATIC_COST_TEXT_RE = re.compile(
    r"(?ix)\b(?:kostnadsestimat|kostnadsanslag|sjablong|kostnadsramme|utbedringskostnad(?:er)?)\b"
)
REPORT_COST_CLASS_MODEL_RE = re.compile(
    r"(?ix)\b(?:kostnadsklasse(?:r)?|kostnadskategori(?:er)?)\b.{0,80}\b(?:lav|middels|høy)\b"
)
NO_TG_HMS_TOPIC_RE = re.compile(
    r"(?ix)\b(?:elektrisk|el-anlegg|el anlegg|elinstallasjon|hms|brannsikkerhet|radon|asbest|lovlighet|sikkerhet)\b"
)
NO_TG_HMS_POLICY_RE = re.compile(
    r"(?ix)\b(?:skal\s+ikke\s+tilstandsgradsettes|ikke\s+tilstandsgradsettes|uten\s+tg|ikke\s+tg|tg\s+ikke\s+satt)\b"
)
REPORT_DATE_LABEL_RE = re.compile(
    r"(?ix)\b(?:rapportdato|rapport\s*dato|dato\s+for\s+rapport|rapporteringsdato|signeringsdato|dato)\b"
    r"[^0-9\n]{0,20}(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}|\d{4}[.\-/]\d{2}[.\-/]\d{2})"
)
GENERIC_DATE_RE = re.compile(r"\b(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}|\d{4}[.\-/]\d{2}[.\-/]\d{2})\b")
RAILINGS_TOPIC_RE = re.compile(r"(?ix)\b(?:rekkverk|håndrekker|handrekker|håndløper|handloper|håndløpere|handlopere)\b")
FREESTANDING_BUILDING_RE = re.compile(r"(?ix)\b(?:garasje|garage|uthus|bod|naust)\b")
DEVIATION_KEYWORD_RE = re.compile(
    r"(?ix)\b(?:avvik|skade|svikt|lekk|fukt|råte|sprek|mangler|manglende|ikke\s+i\s+henhold|utbedr|"
    r"anbefales|anbefalt|bor\b|bør\b|kontroll|tiltak|vedlikehold|slitasje|fare|risiko)\b"
)
BRA_BREAKDOWN_RE = re.compile(r"(?ix)\bBRA[\s\\/_-]*(?:i|e|b)\b")
OLD_AREAL_METHOD_RE = re.compile(
    r"(?ix)\b(?:p[\-\s]?rom|s[\-\s]?rom)\b|"
    r"slik\s+m[aå]lereglene\s+var\s+praktisert\s+i\s+bransjen\s+p[aå]\s+m[aå]le?t?idspunktet"
)
HABITABLE_ANNEX_RE = re.compile(
    r"(?ix)\banneks\b.*?\b(?:varig\s+opphold|godkjent\s+for\s+varig\s+opphold|"
    r"godkjent\s+som\s+bolig|selvstendig\s+boenhet|boenhet)\b|"
    r"\b(?:varig\s+opphold|godkjent\s+for\s+varig\s+opphold|godkjent\s+som\s+bolig|selvstendig\s+boenhet|boenhet)\b.*?\banneks\b"
)


def _page_has_summary_marker(page_text: str) -> bool:
    lower = (page_text or "").lower()
    return bool(lower and any(marker in lower for marker in SUMMARY_MARKERS))


def _line_looks_like_summary_signal(line: str) -> bool:
    if not line:
        return False
    return bool(
        TG_RE.search(line)
        or ARK_ÅRSAK_RE.search(line)
        or ARK_RISIKO_RE.search(line)
        or ARK_KONSEKVENS_RE.search(line)
        or ARK_TILTAK_RE.search(line)
        or TG3_INTERVAL_RE.search(line)
        or TG3_COST_CLASS_RE.search(line)
        or TG3_SINGLE_AMOUNT_RE.search(line)
        or KONSEKVENS_TILTAK_RE.search(line)
    )


def _page_looks_like_summary_continuation(page_text: str) -> bool:
    text = (page_text or "").strip()
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    point_headers = 0
    inline_refs = 0
    signal_lines = 0
    for line in lines:
        match = POINT_HEADER_RE.match(line) or POINT_HEADER_FALLBACK_RE.match(line)
        if match:
            raw_pid = (match.group(1) or "").strip()
            section_title = (match.group(2) or "").strip() if match.lastindex and match.lastindex >= 2 else ""
            if not (
                _looks_like_date_point_id(raw_pid)
                or _is_noise_point_id(raw_pid)
                or _looks_like_date_line(line)
                or _is_false_point_header(line, raw_pid, section_title)
            ):
                point_headers += 1
        refs = [m.group(1) for m in SUMMARY_INLINE_POINT_RE.finditer(line)]
        if refs and _line_looks_like_summary_signal(line):
            inline_refs += len(refs)
        if _line_looks_like_summary_signal(line):
            signal_lines += 1
    if point_headers >= 1 and signal_lines >= 1:
        return True
    if point_headers >= 2:
        return True
    if signal_lines >= 2:
        return True
    return inline_refs >= 1 and signal_lines >= 1


def _collect_summary_section_pages(report_text: str) -> List[str]:
    pages = _split_pages(report_text)
    summary_pages: List[str] = []
    in_summary_section = False
    for page in pages:
        page_text = (page.get("text") or "").strip()
        if not page_text:
            continue
        if _page_has_summary_marker(page_text):
            in_summary_section = True
            summary_pages.append(page_text)
            continue
        if in_summary_section and _page_looks_like_summary_continuation(page_text):
            summary_pages.append(page_text)
            continue
        in_summary_section = False
    return summary_pages


def _get_effective_point_text(
    point: Dict[str, object],
    linked_summary: Optional[Dict[str, str]] = None,
    available_point_ids: Optional[List[str]] = None,
) -> str:
    if not isinstance(point, dict):
        return ""
    main_text = str(point.get("span_text") or point.get("excerpt") or "").strip()
    current_title = _normalize_tg3_cost_text(str(point.get("title") or point.get("excerpt") or "")).lower()

    def _strip_embedded_report_markers(text: str) -> str:
        if not text:
            return ""
        cleaned = str(text)
        for marker in ("[TABELLDATA]", "[BILDE DETEKTERT", "[PDF METADATA]", "[START RAPPORTTEKST]"):
            idx = cleaned.find(marker)
            if idx >= 0:
                cleaned = cleaned[:idx]
        return cleaned.strip()

    def _is_inline_field_label(line: str) -> bool:
        normalized = _normalize_tg3_cost_text(line).lower().rstrip(":")
        return normalized in {
            "beskrivelse",
            "vurdering av avvik",
            "konsekvens/tiltak",
            "konsekvens tiltak",
            "konsekvens-tiltak",
            "tiltak",
            "andre tiltak",
            "kommentar",
            "årstall",
            "kilde",
        }

    def _is_sibling_subsection_heading(line: str, next_line: str) -> bool:
        heading = _normalize_tg3_cost_text(line).strip()
        if not heading or len(heading) > 80:
            return False
        heading_low = heading.lower()
        if current_title and heading_low == current_title:
            return False
        if _is_inline_field_label(heading):
            return False
        if POINT_HEADER_RE.match(heading) or POINT_HEADER_FALLBACK_RE.match(heading):
            return False
        if any(ch in heading for ch in ".:;!?"):
            return False
        if len(heading.split()) > 8:
            return False
        next_norm = _normalize_tg3_cost_text(next_line).lower()
        if next_norm in {
            "beskrivelse",
            "vurdering av avvik",
            "konsekvens/tiltak",
            "konsekvens tiltak",
            "konsekvens-tiltak",
            "tiltak",
            "andre tiltak",
            "kommentar",
            "årstall",
            "kilde",
        }:
            return True
        return False

    main_text = _strip_embedded_report_markers(main_text)

    if main_text:
        lines = main_text.splitlines()
        isolated_lines: List[str] = []
        for idx, raw_line in enumerate(lines):
            line = raw_line.strip()
            next_line = ""
            for candidate in lines[idx + 1:]:
                if candidate.strip():
                    next_line = candidate.strip()
                    break
            if isolated_lines and _is_sibling_subsection_heading(line, next_line):
                break
            isolated_lines.append(raw_line)
        isolated_text = "\n".join(isolated_lines).strip()
        if isolated_text:
            main_text = isolated_text

    linked_text = _strip_embedded_report_markers(str(point.get("linked_summary_text") or "").strip())
    if not linked_text and linked_summary:
        point_id = str(point.get("point_id") or point.get("numeric_id") or point.get("native_label") or "")
        linked_text = _get_linked_summary_for_point(linked_summary, point_id, available_point_ids=available_point_ids).strip()
    if linked_text and linked_text not in main_text:
        return (main_text + "\n" + linked_text).strip() if main_text else linked_text
    return main_text or linked_text


def _get_exact_point_text(point: Dict[str, object]) -> str:
    if not isinstance(point, dict):
        return ""
    raw_point = dict(point)
    raw_point["linked_summary_text"] = ""
    raw_point["effective_span_text"] = ""
    return _get_effective_point_text(raw_point).strip()


def _tg3_cost_status(text: str) -> str:
    """
    E1/E2 precedence: range or cost class -> PASS; else single amount -> MEDIUM (E2); else -> HIGH (E1).
    Returns "pass" | "medium" | "high".
    """
    norm = _normalize_tg3_cost_text(text or "")
    if TG3_INTERVAL_RE.search(norm) or TG3_COST_CLASS_RE.search(norm):
        return "pass"
    if TG3_SINGLE_AMOUNT_RE.search(norm):
        return "medium"
    return "high"


def _is_no_tg_hms_point(title: str, text: str) -> bool:
    combined = _normalize_tg3_cost_text(f"{title or ''}\n{text or ''}")
    if not combined:
        return False
    low = combined.lower()
    if NO_TG_HMS_POLICY_RE.search(low):
        return True
    if "lovlighet og sikkerhet" in low:
        return True
    if NO_TG_HMS_TOPIC_RE.search(low) and ("lovlighet" in low or "hms" in low or "elektr" in low):
        return True
    if TG_RE.search(low):
        return False
    return bool(
        re.search(r"(?ix)\b(?:radon|fdv|plantegning|planløsning|dokumentasjon|samsvarserklæring|lovlighet|sikkerhet)\b", low)
    )


def _extract_runtime_scoring_signals(
    title: str,
    text: str,
    report_uses_cost_class_as_schematic_model: bool,
) -> Dict[str, bool]:
    combined = _normalize_tg3_cost_text(f"{title or ''}\n{text or ''}")
    low = combined.lower()
    tiltak_present = bool(TILTAK_HEADING_RE.search(low) or "anbefalt tiltak" in low or "anbefalte tiltak" in low)
    andre_tiltak_present = bool(ANDRE_TILTAK_RE.search(low))
    konsekvens_tiltak_present = bool(KONSEKVENS_TILTAK_RE.search(low))
    vague_measure_guidance_present = bool(VAGUE_MEASURE_GUIDANCE_RE.search(low))
    concrete_recommended_measure_in_free_text = bool(
        ARK_TILTAK_RE.search(combined)
        and not (tiltak_present or andre_tiltak_present or konsekvens_tiltak_present)
        and not vague_measure_guidance_present
    )
    cost_interval_present = bool(TG3_INTERVAL_RE.search(combined))
    cost_class_present = bool(TG3_COST_CLASS_RE.search(combined))
    single_amount_only_present = _tg3_cost_status(combined) == "medium"
    other_schematic_cost_estimate_present = bool(
        SCHEMATIC_COST_TEXT_RE.search(low)
        and not cost_interval_present
        and not cost_class_present
        and not single_amount_only_present
    )
    return {
        "tiltak_present": tiltak_present,
        "andre_tiltak_present": andre_tiltak_present,
        "konsekvens_tiltak_present": konsekvens_tiltak_present,
        "concrete_recommended_measure_in_free_text": concrete_recommended_measure_in_free_text,
        "vague_measure_guidance_present": vague_measure_guidance_present,
        "cost_class_present": cost_class_present,
        "cost_interval_present": cost_interval_present,
        "other_schematic_cost_estimate_present": other_schematic_cost_estimate_present,
        "single_amount_only_present": single_amount_only_present,
        "report_uses_cost_class_as_schematic_model": bool(report_uses_cost_class_as_schematic_model),
    }


_COMPRESSED_MIXED_STRONG_MARKERS = (
    "konsekvens/tiltak",
    "vurdering av avvik",
    "avvik og konsekvens",
    "årsak/konsekvens",
    "aarsak/konsekvens",
)


def _report_text_suggests_compressed_mixed_format(report_text: str) -> bool:
    low = _normalize_report_text_for_analysis(report_text or "")[:120000].lower()
    return any(marker in low for marker in _COMPRESSED_MIXED_STRONG_MARKERS)


def _report_text_suggests_fremtind_template(report_text: str) -> bool:
    normalized = _normalize_report_text_for_analysis(report_text or "")[:160000]
    low = normalized.lower()
    if "fremtind" in low:
        return True
    return bool(re.search(r"(?im)^\s*P\d{2}[A-Z]_[A-Z0-9_]+\b", normalized))


def _report_text_suggests_befar_template(report_text: str) -> bool:
    normalized = _normalize_report_text_for_analysis(report_text or "")[:160000].lower()
    return "befar.io" in normalized or "rapporten er bygget med befar" in normalized


def _p_style_heading_token(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return ""
    head = t.split()[0].strip()
    return head if re.match(r"(?i)^P\d{2}[A-Z]_", head) else ""


def _extract_text_block_for_p_style_heading(norm_report: str, heading_token: str) -> str:
    if not norm_report or not heading_token:
        return ""
    lines = norm_report.splitlines()
    heading_u = heading_token.strip().upper()
    start_i = None
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        su = s.upper()
        if su == heading_u or su.startswith(heading_u + " ") or su.startswith(heading_u + ":"):
            start_i = i
            break
    if start_i is None:
        return ""
    block: List[str] = [lines[start_i]]
    for j in range(start_i + 1, len(lines)):
        l2 = lines[j]
        stripped = l2.strip()
        if not stripped:
            block.append(l2)
            continue
        if re.match(r"(?i)^P\d{2}[A-Z]_[A-Z0-9_]+\s*$", stripped):
            break
        if POINT_HEADER_RE.match(stripped):
            break
        if POINT_HEADER_FALLBACK_RE.match(stripped):
            if _looks_like_date_line(stripped):
                block.append(l2)
                continue
            break
        block.append(l2)
    return "\n".join(block).strip()


def _compressed_mixed_heading_terms_for_point(point: Dict[str, object]) -> List[str]:
    terms: List[str] = []
    point_id = _normalize_point_id(
        str(point.get("point_id") or point.get("canonical_point_id") or point.get("native_label") or "")
    )
    title = str(point.get("title") or point.get("excerpt") or "").strip()
    if title:
        cleaned_title = re.sub(r"(?i)\s+g[åa]\s+t?i?l?\s+side\s*$", "", title).strip()
        terms.append(cleaned_title)
        if ">" in cleaned_title:
            segments = [part.strip() for part in cleaned_title.split(">") if part.strip()]
            terms.append(cleaned_title.rsplit(">", 1)[-1].strip())
            if len(segments) >= 2:
                terms.append(" > ".join(segments[:-1]))
                terms.append(" > ".join(segments[-2:]))
            if len(segments) >= 3:
                terms.append(" > ".join(segments[1:-1]))
            if segments and segments[-1].lower().startswith("tilliggende"):
                terms.append("Tilliggende konstruksjoner våtrom")
    if point_id:
        for mapping in _get_child_mapping_points_for_inference():
            if str(mapping.get("canonical_id") or mapping.get("child_id") or "") != point_id:
                continue
            aliases = mapping.get("aliases")
            if isinstance(aliases, list):
                terms.extend(str(alias or "").strip() for alias in aliases)
            break
    out: List[str] = []
    seen = set()
    for term in terms:
        cleaned = _normalize_report_text_for_analysis(term or "").strip(" :-|")
        if len(cleaned) < 4:
            continue
        key = cleaned.lower()
        if key in {
            "utvendig",
            "innvendig",
            "våtrom",
            "vatrom",
            "tekniske installasjoner",
            "tomteforhold",
            "kjøkken",
            "kjokken",
            "spesialrom",
        }:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    if point_id == "P01D_DRAINAGE":
        # A bare "drenering" alias is too broad in Fremtind reports because
        # Rom Under Terreng says "Punktet må sees i sammenheng med Drenering".
        out = [term for term in out if "fuktsikring" in term.lower()]
    return out


def _extract_compressed_mixed_wetroom_block_by_title(norm_report: str, title: str) -> str:
    if not norm_report or not title or ">" not in title:
        return ""
    normalized_title = _normalize_report_text_for_analysis(title)
    title_low = normalized_title.lower()
    if not any(marker in title_low for marker in ("våtrom", "vatrom", "spesialrom")):
        return ""
    segments = [part.strip() for part in normalized_title.split(">") if part.strip()]
    if len(segments) < 3:
        return ""
    room_segments = segments[1:-1] if segments[0].lower() in {"våtrom", "vatrom", "spesialrom"} else segments[:-1]
    if not room_segments:
        return ""
    room_line = _normalize_report_text_for_analysis(" > ".join(room_segments)).lower()
    component_label = segments[-1].lower()
    if component_label.startswith("tilliggende"):
        component_candidates = {"tilliggende konstruksjoner våtrom", "tilliggende konstruksjoner vatrom"}
    elif component_label.startswith("overflater"):
        component_candidates = {component_label, "overflater og konstruksjon"}
    elif component_label.startswith("teknisk"):
        component_candidates = {component_label, "teknisk anlegg"}
    else:
        component_candidates = {component_label}
    lines = norm_report.splitlines()

    def _norm_line(idx: int) -> str:
        if idx < 0 or idx >= len(lines):
            return ""
        return _normalize_report_text_for_analysis(lines[idx] or "").strip().lower()

    def _is_room_line(value: str) -> bool:
        return bool(value and (value == room_line or value.endswith(" > " + room_line)))

    def _is_component_line(value: str) -> bool:
        return any(candidate and candidate == value for candidate in component_candidates)

    starts: List[Tuple[int, int]] = []
    for idx in range(len(lines)):
        if not _is_room_line(_norm_line(idx)):
            continue
        for j in range(idx + 1, min(len(lines), idx + 8)):
            if _is_component_line(_norm_line(j)):
                starts.append((idx, j))
                break
    if not starts:
        return ""

    start_idx, component_idx = starts[0]
    for candidate_start, candidate_component in starts:
        # Prefer exact room/component pairs in body text over table-of-content rows.
        local = _normalize_report_text_for_analysis(
            "\n".join(lines[candidate_start:min(len(lines), candidate_component + 12)])
        ).lower()
        if "gå til side" not in local and _is_component_line(_norm_line(candidate_component)):
            start_idx, component_idx = candidate_start, candidate_component
            break

    end_idx = min(len(lines), component_idx + 95)
    room_heading_re = re.compile(r"(?i)^(?:\d+\.\s*)?[A-ZÆØÅ0-9 .]+\s+>\s+[A-ZÆØÅ0-9 .]+\s*$")
    category_re = re.compile(r"(?i)^(?:UTVENDIG|INNVENDIG|V[ÅA]TROM|KJ[ØO]KKEN|SPESIALROM|TEKNISKE INSTALLASJONER|TOMTEFORHOLD)\s*$")
    seen_body = False
    for j in range(component_idx + 1, end_idx):
        stripped = lines[j].strip()
        if not stripped:
            continue
        norm = _norm_line(j)
        if norm in {"beskrivelse", "vurdering av avvik:", "konsekvens/tiltak", "kostnadsestimat"}:
            seen_body = True
        if j > component_idx + 2 and stripped.startswith("[TABELLDATA]"):
            end_idx = j
            break
        if j > component_idx + 2 and re.match(r"(?i)^\[SIDE\s+\d+\]", stripped):
            end_idx = j
            break
        if j > component_idx + 2 and category_re.match(stripped):
            end_idx = j
            break
        if j > component_idx + 2 and room_heading_re.match(stripped):
            next_norm = _norm_line(j + 1)
            if next_norm in {"generell", "tilliggende konstruksjoner våtrom", "tilliggende konstruksjoner vatrom", "overflater og konstruksjon", "teknisk anlegg"}:
                end_idx = j
                break
        if seen_body and j > component_idx + 3:
            next_norm = _norm_line(j + 1)
            if next_norm == "beskrivelse" and not _is_component_line(norm):
                end_idx = j
                break
    block = "\n".join(lines[start_idx:end_idx]).strip()
    if "gå til side" in block.lower() or "gå til side" in block.lower():
        return ""
    return block[:5000].strip()


def _extract_compressed_mixed_block_by_terms(norm_report: str, terms: List[str], require_tg: bool = True) -> str:
    if not norm_report or not terms:
        return ""
    lines = norm_report.splitlines()
    normalized_terms = [term.lower() for term in terms if term]
    label_re = re.compile(
        r"(?i)\b(?:vurdering\s+av\s+avvik|konsekvens\s*(?:/|-|og)\s*(?:tiltak|tltak|ltak)|"
        r"tiltak|kostnadsestimat)\b"
    )
    strong_label_re = re.compile(
        r"(?i)\b(?:vurdering\s+av\s+avvik|konsekvens\s*(?:/|-|og)\s*(?:tiltak|tltak|ltak))\b"
    )
    boundary_re = re.compile(
        r"(?i)^(?:[A-ZÆØÅ0-9][^.\n]{0,95}\s+>\s+[^.\n]{2,95}\s+g[åa]\s+t?i?l?\s+side|"
        r"[A-ZÆØÅ][A-ZÆØÅ0-9 /,&()_-]{4,}\s+g[åa]\s+t?i?l?\s+side)\s*$"
    )
    table_or_page_re = re.compile(r"(?i)^\[(?:TABELLDATA|BILDE DETEKTERT|SIDE\s+\d+)\]")
    heading_boundary_re = re.compile(
        r"(?i)^(?:UTVENDIG|INNVENDIG|V[ÅA]TROM|KJ[ØO]KKEN|SPESIALROM|TEKNISKE INSTALLASJONER|"
        r"TOMTEFORHOLD|HELSE,\s*MILJØ OG SIKKERHET|FORHOLD SOM ÅPENBART KAN MEDFØRE FARE)$"
    )

    def _line_has_term(idx: int) -> bool:
        if idx < 0 or idx >= len(lines):
            return False
        normalized_line = _normalize_report_text_for_analysis(lines[idx] or "").strip().lower()
        if not normalized_line or len(normalized_line) > 220:
            return False
        return any(term in normalized_line for term in normalized_terms)

    def _line_is_exact_anchor_term(idx: int) -> bool:
        if idx < 0 or idx >= len(lines):
            return False
        normalized_line = _normalize_report_text_for_analysis(lines[idx] or "").strip(" :-|").lower()
        return bool(normalized_line and normalized_line in set(normalized_terms))

    def _looks_like_toc_or_summary(block: str) -> bool:
        low = _normalize_report_text_for_analysis(block or "").lower()
        nav_hits = low.count("gå til side") + low.count("gÅ til side")
        summary_hits = sum(
            low.count(marker)
            for marker in (
                "sammendrag av boligens",
                "oppsummering av avvik",
                "fordeling av tilstandsgrader",
                "anslag på utbedringskostnad",
                "vil du vite mer",
            )
        )
        return nav_hits >= 2 or summary_hits >= 1

    def _window_end(start_idx: int, min_idx: int) -> int:
        end_idx = min(len(lines), start_idx + 90)
        seen_label = False
        for j in range(start_idx + 1, end_idx):
            stripped = lines[j].strip()
            if not stripped:
                continue
            if j > start_idx + 2 and table_or_page_re.match(stripped):
                return j
            if j > start_idx + 2 and boundary_re.match(stripped):
                return j
            if j > start_idx + 2 and heading_boundary_re.match(stripped) and not _line_has_term(j):
                return j
            if label_re.search(stripped):
                seen_label = True
            if seen_label and j > min_idx + 3:
                next_stripped = lines[j + 1].strip() if j + 1 < len(lines) else ""
                if next_stripped.lower() == "kommentar" and not _line_has_term(j):
                    return j
                if (
                    re.match(r"^[A-ZÆØÅ][A-Za-zÆØÅæøå /,&()_-]{3,80}$", stripped)
                    and next_stripped.lower() in {"beskrivelse", "kommentar", "punktet må sees i sammenheng med ‘drenering’", "punktet må sees i sammenheng med 'drenering'"}
                    and not _line_is_exact_anchor_term(j)
                ):
                    return j
                if re.match(r"^[A-ZÆØÅ0-9][A-ZÆØÅ0-9 .>/_-]{3,60}$", stripped) and not _line_is_exact_anchor_term(j):
                    return j
                if (
                    re.match(r"^[A-ZÆØÅ][A-Za-zÆØÅæøå /,&()_-]{3,80}$", stripped)
                    and not _line_is_exact_anchor_term(j)
                    and (next_stripped.startswith("[") or not next_stripped)
                ):
                    return j
            if j > min_idx + 3 and re.match(r"(?i)^P\d{2}[A-Z]_[A-Z0-9_]+\s*$", stripped):
                return j
            if j > min_idx + 3 and POINT_HEADER_RE.match(stripped):
                return j
            if j > min_idx + 3 and POINT_HEADER_FALLBACK_RE.match(stripped) and not _looks_like_date_line(stripped):
                return j
        return end_idx

    def _append_candidate(start_idx: int, min_idx: int) -> None:
        if start_idx < 0 or start_idx >= len(lines):
            return
        end_idx = _window_end(start_idx, min_idx)
        block = "\n".join(lines[start_idx:end_idx]).strip()
        if not block or _looks_like_toc_or_summary(block):
            return
        candidates.append(block)

    candidates: List[str] = []
    for idx, line in enumerate(lines):
        normalized_line = _normalize_report_text_for_analysis(line or "").strip().lower()
        if not normalized_line or len(normalized_line) > 220:
            continue
        if "gå til side" in normalized_line or "gÅ til side" in normalized_line:
            continue
        if not any(term in normalized_line for term in normalized_terms):
            continue
        if normalized_line in {"utvendig", "innvendig", "våtrom", "vatrom", "tekniske installasjoner", "tomteforhold"}:
            continue
        _append_candidate(idx, idx)

    # Fremtind blocks often have a short component description followed by labeled
    # fields; use label anchors too, but only when the same local window names the point.
    for idx, line in enumerate(lines):
        if not label_re.search(line or ""):
            continue
        lookback_start = max(0, idx - 35)
        term_idx = -1
        for j in range(idx, lookback_start - 1, -1):
            if _line_has_term(j) and "gå til side" not in _normalize_report_text_for_analysis(lines[j]).lower():
                term_idx = j
                break
        if term_idx >= 0:
            _append_candidate(term_idx, idx)

    def _score(block: str) -> tuple:
        normalized_block = _normalize_report_text_for_analysis(block or "")
        low = normalized_block.lower()
        if _looks_like_toc_or_summary(normalized_block):
            return (0, 0, 0, 0)
        if "forhold som åpenbart kan medføre fare" in low and not any("fare" in term for term in normalized_terms):
            return (0, 0, 0, 0)
        block_lines = [line.strip() for line in normalized_block.splitlines() if line.strip()]
        tg = _extract_tg_label_from_text(block)
        label_hits = len(label_re.findall(low))
        strong_label_hits = len(strong_label_re.findall(low))
        if tg not in {"TG2", "TG3", "TGIU"} and strong_label_hits:
            inferred_tg = _fallback_infer_tg_from_point_text(block, _normalize_report_text_for_analysis)
            if inferred_tg in {"TG2", "TG3", "TGIU"}:
                tg = inferred_tg
        if label_hits == 0:
            return (0, 0, 0, 0)
        if require_tg and tg not in {"TG2", "TG3", "TGIU"}:
            return (0, 0, 0, 0)
        signal_score = 0
        for marker in (
            "vurdering av avvik",
            "konsekvens",
            "tiltak",
            "tilstandsgrad 2",
            "tilstandsgrad 3",
            "kostnadsestimat",
        ):
            if marker in low:
                signal_score += 1
        term_hits = sum(1 for term in normalized_terms if term and term in low)
        clean_anchor = 0
        if block_lines:
            first_line = block_lines[0].lower()
            second_line = block_lines[1].lower() if len(block_lines) > 1 else ""
            if any(term == first_line for term in normalized_terms):
                clean_anchor = 3
            elif any(term in first_line for term in normalized_terms) and second_line in {
                "beskrivelse",
                "kommentar",
                "punktet må sees i sammenheng med ‘drenering’",
                "punktet må sees i sammenheng med 'drenering'",
            }:
                clean_anchor = 2
            elif any(term in first_line for term in normalized_terms):
                clean_anchor = 1
        section_heading_re = re.compile(
            r"(?i)^(?:fuktsikring og drenering|grunnmur og fundamenter|terrengforhold|byggegrunn|"
            r"utvendige vann- og avløpsledninger|vinduer|dører|balkonger, terrasser og rom under balkonger|"
            r"utvendige trapper|andre utvendige forhold|overflater|etasjeskille/gulv mot grunn|"
            r"innvendige trapper|innvendige dører|andre innvendige forhold|elektrisk anlegg|"
            r"forhold som åpenbart kan medføre fare)$"
        )
        unrelated_heading_hits = sum(
            1
            for line in block_lines[1:]
            if section_heading_re.match(line)
            and not any(term and term in line.lower() for term in normalized_terms)
        )
        same_line_merge_penalty = int(
            bool(re.search(r"(?i)\bdreneringen er fra \d{4}\.\s+utvendige vann- og avløpsledninger\b", normalized_block))
        )
        return (1, clean_anchor, -unrelated_heading_hits, -same_line_merge_penalty, signal_score, term_hits, -min(len(low), 4500))

    best = max(candidates, key=_score, default="")
    if _score(best)[0] <= 0:
        return ""
    return best[:5000].strip()


_FREMTIND_SUMMARY_CATEGORY_RE = re.compile(
    r"(?i)\b(?:Utvendig|Innvendig|V[åa]trom|Tekniske installasjoner|Tomteforhold|Kj[øo]kken|Spesialrom)\b.*"
)


def _clean_fremtind_summary_title(line: str) -> str:
    raw = _normalize_report_text_for_analysis(line or "").strip(" |")
    if not raw:
        return ""
    match = _FREMTIND_SUMMARY_CATEGORY_RE.search(raw)
    if match:
        raw = match.group(0)
    raw = re.sub(r"(?i)\bg[åa]\s+t?i?l?\s+side\b", "", raw)
    raw = re.sub(r"(?i)\bllatna\b", "", raw)
    raw = re.sub(r"^\s*\d+(?:[.,]\d+)?\s+", "", raw)
    raw = re.sub(r"(?i)\b\d+(?:[.,]\d+)?\s+(balkonger)\b", r"\1", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" :-|")
    replacements = {
        "Overater": "Overflater",
        "overater": "overflater",
        "teesjikt": "tettesjikt",
        "lstand": "tilstand",
        "ltak": "tiltak",
        "Lo ": "Loft ",
        "Lo>": "Loft>",
    }
    for old, new in replacements.items():
        raw = raw.replace(old, new)
    return raw.strip()


def _summary_title_to_canonical_child_id(title: str, mapping_points: List[Dict[str, object]]) -> str:
    normalized = _normalize_segment_title_for_canonical_match(title or "")
    if not normalized:
        return ""
    manual: Tuple[Tuple[str, str], ...] = (
        ("nedlop og beslag", "P04D_GUTTERS"),
        ("nedløp og beslag", "P04D_GUTTERS"),
        ("pipe og ildsted", "P10F_FIREPLACE"),
        ("rom under terreng", "P06A_BELOW_GRADE_WALLS"),
        ("bad generell", "P07A_WETROOM_INSTANCE"),
        ("taktekking", "P04C_ROOF_COVERING"),
        ("veggkonstruksjon", "P02A_EXTERIOR_WALLS"),
        ("takkonstruksjon", "P04A_ROOF_STRUCTURE"),
        ("vinduer", "P03A_WINDOWS"),
        ("innvendige dorer", "P10E_INTERIOR_DOORS"),
        ("innvendige dører", "P10E_INTERIOR_DOORS"),
        ("dorer", "P03C_EXTERIOR_DOORS"),
        ("dører", "P03C_EXTERIOR_DOORS"),
        ("balkonger terrasser", "P05B_TERRACE"),
        ("balkonger, terrasser", "P05B_TERRACE"),
        ("utvendige trapper", "P05D_EXTERNAL_STAIRS"),
        ("andre utvendige forhold", "P05H_OTHER_EXTERIOR"),
        ("innvendig overflater", "P10G_INTERIOR_SURFACES"),
        ("etasjeskille", "P10A_FLOOR_SEPARATIONS"),
        ("innvendige trapper", "P10D_INTERIOR_STAIRS"),
        ("andre innvendige forhold", "P10H_OTHER_INTERIOR"),
        ("utvendige vann og avlopsledninger", "P09H_EXTERNAL_WATER_DRAIN_PIPES"),
        ("utvendige vann og avløpsledninger", "P09H_EXTERNAL_WATER_DRAIN_PIPES"),
        ("utvendige vann", "P09H_EXTERNAL_WATER_DRAIN_PIPES"),
        ("vannledninger", "P09A_WATER_PIPES"),
        ("avlopsror", "P09B_DRAIN_PIPES"),
        ("avløpsrør", "P09B_DRAIN_PIPES"),
        ("varmtvannstank", "P09C_HOT_WATER_TANK"),
        ("andre installasjoner", "P09G_OTHER_INSTALLATIONS"),
        ("fuktsikring og drenering", "P01D_DRAINAGE"),
        ("grunnmur og fundamenter", "P01C_FOUNDATION_WALLS"),
        ("terrengforhold", "P01E_TERRAIN"),
        ("kjolerom", "P10I_SPECIAL_ROOM_SURFACES"),
        ("kjølerom", "P10I_SPECIAL_ROOM_SURFACES"),
        ("overflater og innredning", "P08B_KITCHEN_FURNITURE"),
        ("kjokken avtrekk", "P08D_EXTRACTION"),
        ("kjøkken avtrekk", "P08D_EXTRACTION"),
        ("avtrekk", "P08D_EXTRACTION"),
        ("overflater vegger", "P07D_SURFACES"),
        ("overflater gulv", "P07D_SURFACES"),
        ("sluk, membran", "P07B_MEMBRANE"),
        ("sluk membran", "P07B_MEMBRANE"),
        ("sanitærutstyr og innredning", "P07G_SANITARY_EQUIPMENT"),
    )
    for needle, canonical_id in manual:
        if needle in normalized:
            return canonical_id
    inferred = _infer_canonical_child_from_text(title, mapping_points)
    return inferred if inferred and _is_canonical_child_point_id(inferred) else ""


def _summary_child_id_with_instance(base_id: str, title: str, used_ids: set) -> str:
    if not base_id:
        return ""
    if base_id not in used_ids:
        used_ids.add(base_id)
        return base_id
    suffix_source = _normalize_segment_title_for_canonical_match(title or "").upper()
    suffix = re.sub(r"[^A-Z0-9]+", "_", suffix_source).strip("_")[:48]
    candidate = f"{base_id}_{suffix or 'INSTANCE'}"
    counter = 2
    while candidate in used_ids:
        candidate = f"{base_id}_{suffix or 'INSTANCE'}_{counter}"
        counter += 1
    used_ids.add(candidate)
    return candidate


def _extract_compressed_mixed_summary_tg_points(report_text: str) -> List[Dict[str, object]]:
    if not _report_text_suggests_fremtind_template(report_text or ""):
        return []
    pages = _split_pages(_normalize_report_text_for_analysis(report_text or ""))
    mapping_cfg = get_points_overview_mapping_config() or {}
    mapping_points: List[Dict[str, object]] = []
    child_mappings = mapping_cfg.get("child_mappings") if isinstance(mapping_cfg, dict) else []
    if isinstance(child_mappings, list):
        for item in child_mappings:
            if isinstance(item, dict):
                copy = dict(item)
                if "child_id" in copy:
                    copy["canonical_id"] = copy["child_id"]
                mapping_points.append(copy)

    summary_lines: List[Tuple[int, str]] = []
    in_summary = False
    for page in pages:
        lines = [str(line or "").strip() for line in str(page.get("text") or "").splitlines()]
        page_has_summary = any(re.search(r"(?i)sammendrag\s+av\s+boligens\s+(?:tilstand|lstand)", line) for line in lines)
        if page_has_summary:
            in_summary = True
        if in_summary:
            for line in lines:
                if line.strip().startswith("[TABELLDATA]"):
                    break
                if line:
                    summary_lines.append((int(page.get("page") or 1), line))
        if in_summary and any(re.search(r"(?i)HELSE,\s*MILJ", line) for line in lines):
            break

    if not summary_lines:
        return []

    rows: List[Tuple[str, str, int]] = []
    current_tg = ""
    pending_idx: Optional[int] = None

    def _append_title(tg: str, raw_title: str, page_no: int) -> None:
        cleaned = _clean_fremtind_summary_title(raw_title)
        if not cleaned or ">" not in cleaned:
            return
        if any(_normalize_report_text_for_analysis(cleaned).lower() == _normalize_report_text_for_analysis(existing).lower() and tg == existing_tg for existing_tg, existing, _ in rows):
            return
        rows.append((tg, cleaned, page_no))

    for idx, (page_no, raw_line) in enumerate(summary_lines):
        line = raw_line.strip()
        line_low = _normalize_report_text_for_analysis(line).lower()
        if re.search(r"(?i)^\s*(?:TG\s*IU|TG\s*[0-3])\s*:", line):
            if "avvik som kan kreve" in line_low:
                current_tg = "TG2"
            pending_idx = None
            continue
        if "store eller alvorlige avvik" in line_low:
            current_tg = "TG3"
            pending_idx = None
            if "avvik som kan kreve" in line_low:
                current_tg = "TG2"
            continue
        if "avvik som kan kreve" in line_low:
            current_tg = "TG2"
            pending_idx = None
            continue
        if "konstruksjoner som ikke er unders" in line_low:
            current_tg = "TGIU"
            pending_idx = None
            continue
        if re.search(r"(?i)^HELSE,\s*MILJ", line):
            break
        if not current_tg:
            continue
        if not _FREMTIND_SUMMARY_CATEGORY_RE.search(line):
            if pending_idx is not None and not re.search(r"(?i)^(?:TG\d|TG\s*IU|Tiltak|Oppdragsnr|Side:|Vil du vite|Hva er|Anslag|Fordeling|Oppsummering|rapporten|Ingeniør|Lundestad|Berjmannsveien|Witek\s+AS|Roald\s+Amundsens|Fjellfoten|Gnr\b|(?:\d{4}\\s+)?(?:FREDRIKSTAD|SARPSBORG)|.*HAFSLUNDSØY.*|Sammendrag|llatnA|\[|\||\d+(?:[.,]\d+)?\s*$)", line):
                tg, title, title_page = rows[pending_idx]
                rows[pending_idx] = (tg, _clean_fremtind_summary_title(f"{title} {line}"), title_page)
            continue
        cleaned = _clean_fremtind_summary_title(line)
        if not cleaned:
            continue
        _append_title(current_tg, cleaned, page_no)
        pending_idx = len(rows) - 1 if rows else None

    used_ids: set = set()
    points: List[Dict[str, object]] = []
    for idx, (tg, title, page_no) in enumerate(rows, start=1):
        base_id = _summary_title_to_canonical_child_id(title, mapping_points)
        if not base_id:
            continue
        point_id = _summary_child_id_with_instance(base_id, title, used_ids)
        points.append(
            {
                "point_key": f"FREM_SUMMARY_{idx:03d}",
                "native_label": point_id,
                "numeric_id": "",
                "native_path": [],
                "kind": "point",
                "point_id": point_id,
                "canonical_point_id": point_id,
                "title": title,
                "page_start": page_no,
                "page_end": page_no,
                "order_in_doc": idx,
                "anchor_text": title,
                "span_hash": hashlib.sha256(f"{tg}:{title}".encode("utf-8")).hexdigest(),
                "excerpt": title,
                "tg": tg,
                "span_text": title,
                "effective_span_text": title,
                "exact_span_text": title,
                "tg_source": "fremtind_summary",
            }
        )
    return points


def _hydrate_compressed_mixed_p_style_spans(report_text: str, points: List[Dict[str, object]]) -> None:
    """
    Fremtind / NTF-style segments use P02A_* headings and labeled ARKAT blocks instead of
    inline 'TG X | N.N.N' lines. When detection only captured the heading line, expand each
    point's span from the report up to the next P-heading or numeric punkt-header.
    """
    if not report_text or not points:
        return
    if not _report_text_suggests_fremtind_template(report_text):
        return
    norm_report = _normalize_report_text_for_analysis(report_text)
    for point in points:
        if not isinstance(point, dict):
            continue
        token = _p_style_heading_token(str(point.get("title") or ""))
        if not token:
            token = _p_style_heading_token(str(point.get("canonical_point_id") or ""))
        if not token:
            token = _p_style_heading_token(str(point.get("point_id") or ""))
        cur = str(
            point.get("effective_span_text")
            or point.get("span_text")
            or ""
        ).strip()
        summary_tg_source = (
            str(point.get("tg_source") or "") == "fremtind_summary"
            and _normalize_tg_label(point.get("tg")) in {"TG2", "TG3", "TGIU"}
        )
        if len(cur) >= 120 and not summary_tg_source:
            continue
        block = _extract_compressed_mixed_wetroom_block_by_title(
            norm_report,
            str(point.get("title") or point.get("excerpt") or ""),
        )
        path_anchored_block = bool(block)
        if not block:
            block = _extract_text_block_for_p_style_heading(norm_report, token) if token else ""
        summary_tg = _normalize_tg_label(point.get("tg")) if summary_tg_source else ""
        if not path_anchored_block and not _extract_tg_label_from_text(block):
            recovered_block = _extract_compressed_mixed_block_by_terms(
                norm_report,
                _compressed_mixed_heading_terms_for_point(point),
                require_tg=not summary_tg_source,
            )
            if recovered_block:
                block = recovered_block
        if not block or len(block) < 40:
            continue
        point["span_text"] = block
        point["effective_span_text"] = block
        point["exact_span_text"] = block
        if summary_tg:
            point["tg"] = summary_tg
            continue
        tg = _extract_tg_label_from_text(block)
        if tg not in {"TG2", "TG3", "TGIU"}:
            tg = _fallback_infer_tg_from_point_text(block, _normalize_report_text_for_analysis)
        if tg in {"TG2", "TG3", "TGIU"}:
            point["tg"] = tg


def _normalize_runtime_scoring_signals(points: List[Dict[str, object]]) -> List[Dict[str, object]]:
    if not isinstance(points, list) or not points:
        return points
    report_text = _normalize_tg3_cost_text(
        "\n".join(
            str(p.get("effective_span_text") or _get_effective_point_text(p)).strip()
            for p in points
            if isinstance(p, dict)
        )
    )
    report_uses_cost_class_model = bool(REPORT_COST_CLASS_MODEL_RE.search(report_text))
    normalized_points: List[Dict[str, object]] = []
    for point in points:
        if not isinstance(point, dict):
            normalized_points.append(point)
            continue
        out = dict(point)
        title = str(out.get("title") or out.get("excerpt") or "")
        exact_span_text = str(out.get("exact_span_text") or _get_exact_point_text(out)).strip()
        out["exact_span_text"] = exact_span_text
        span_text = str(out.get("effective_span_text") or _get_effective_point_text(out)).strip()
        out["effective_span_text"] = span_text
        signals = _extract_runtime_scoring_signals(title, span_text, report_uses_cost_class_model)
        out["normalized_signals"] = signals
        out["exact_point_signals"] = _extract_runtime_scoring_signals(title, exact_span_text, report_uses_cost_class_model)
        out.update(signals)
        out["no_tg_hms_point"] = _is_no_tg_hms_point(title, span_text)
        normalized_points.append(out)
    return normalized_points


def _build_exact_point_source_lookup(
    detected_points: List[Dict[str, object]],
) -> Dict[str, Dict[str, object]]:
    lookup: Dict[str, Dict[str, object]] = {}
    report_text = _normalize_tg3_cost_text(
        "\n".join(
            str(p.get("effective_span_text") or _get_effective_point_text(p)).strip()
            for p in detected_points
            if isinstance(p, dict)
        )
    )
    report_uses_cost_class_model = bool(REPORT_COST_CLASS_MODEL_RE.search(report_text))
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(
            str(point.get("point_id") or point.get("numeric_id") or point.get("native_label") or "")
        )
        if not _is_segment_validation_point_id(point_id):
            continue
        title = str(point.get("title") or point.get("excerpt") or point_id).strip()
        exact_text = str(point.get("exact_span_text") or _get_exact_point_text(point)).strip()
        if not exact_text:
            continue
        lookup[point_id] = {
            "point_id": point_id,
            "title": title,
            "text": exact_text,
            "signals": dict(point.get("exact_point_signals") or _extract_runtime_scoring_signals(title, exact_text, report_uses_cost_class_model)),
        }
    return lookup


def _point_has_accepted_tg3_cost_signal(point: Dict[str, object]) -> bool:
    if not isinstance(point, dict):
        return False
    if bool(point.get("cost_interval_present")):
        return True
    if bool(point.get("cost_class_present")):
        return True
    exact_signals = point.get("exact_point_signals")
    if isinstance(exact_signals, dict):
        if bool(exact_signals.get("cost_interval_present")):
            return True
        if bool(exact_signals.get("cost_class_present")):
            return True
        if bool(exact_signals.get("other_schematic_cost_estimate_present")):
            return True
    # Accept explicit schematic estimates for the exact point even when the
    # wording is not a strict interval / cost-class phrase.
    if bool(point.get("other_schematic_cost_estimate_present")):
        return True
    return False


_VALID_TG_LABELS = {"TG0", "TG1", "TG2", "TG3", "TGIU"}
_TILSTANDSGRAD_VALUE_RE = re.compile(r"(?i)\btilstandsgrad\s*[:\-]?\s*(0|1|2|3|IU)\b")
_TILSTANDSGRAD_NOT_INSPECTED_RE = re.compile(
    r"(?i)\btilstandsgrad\s*(?:ikke\s*unders[øo]kt|iu)\b|\bikke\s+unders[øo]kt\b"
)
_TG3_FALLBACK_SIGNAL_RE = re.compile(
    r"(?i)\b(?:store\s+avvik|alvorlige?\s+avvik|strakstiltak|må\s+utbedres?|kritisk|akutt|"
    r"funksjonssvikt|sammenbrudd|fare\s+for\s+personskade|brannfare)\b"
)
_TG2_FALLBACK_SIGNAL_RE = re.compile(
    r"(?i)\b(?:avvik|mangler?|slitasje|anbefales?|bør\s+utbedres?|risiko|fuktskade|"
    r"sprekker?|utetthet|nedsatt\s+funksjon)\b"
)


def _normalize_tg_label(value: object) -> str:
    tg = str(value or "").strip().upper()
    tg = re.sub(r"[\s\-]+", "", tg)
    return tg if tg in _VALID_TG_LABELS else ""


def _extract_tg_label_from_text(text: str) -> str:
    normalized = _normalize_tg3_cost_text(text or "")
    if not normalized:
        return ""
    tg_match = TG_RE.search(normalized)
    if tg_match:
        return _normalize_tg_label(tg_match.group(0))
    value_match = _TILSTANDSGRAD_VALUE_RE.search(normalized)
    if value_match:
        return _normalize_tg_label(f"TG{value_match.group(1)}")
    if _TILSTANDSGRAD_NOT_INSPECTED_RE.search(normalized):
        return "TGIU"
    # Compressed / NTF-style prose: "tilstandsgrad settes til 2", "NS 3600 TG-vurdering ... TG 2"
    m = re.search(r"(?i)\btilstandsgrad(?:en)?\b[^.\n]{0,55}\b(0|1|2|3|iu)\b", normalized)
    if m:
        return _normalize_tg_label(f"TG{m.group(1)}")
    m = re.search(
        r"(?i)\b(?:vurder(?:es|t)|satt|settes)\s+(?:til|som)\s+TG\s*[-]?\s*(0|1|2|3|iu)\b",
        normalized,
    )
    if m:
        return _normalize_tg_label(f"TG{m.group(1)}")
    return ""


def _bmtf_subpoint_marked_ingen_in_condensed_table(normalized_blob: str, point_id: str) -> bool:
    """
    BMTF / eierskifte condensed rows place explicit 'Ingen' (no TG) per sub-point, e.g.:
    '7.1 Vaskerom | TG 3 | 7.1.1 Overflate ... Ingen | 7.1.2 ... Ingen |'.
    When this pattern exists for a numeric sub-point, that point must not use TG from
    holistic prose elsewhere.
    """
    if not normalized_blob or not point_id:
        return False
    if not (_looks_like_structured_point_id(point_id) and str(point_id).strip().count(".") >= 2):
        return False
    esc = re.escape(str(point_id).strip())
    # Cell ends with Ingen before next pipe (table column).
    if re.search(rf"(?i)(?<![\d.]){esc}(?![\d.])[^\n|]{{0,120}}\bIngen\b\s*\|", normalized_blob):
        return True
    if re.search(rf"(?i)\|\s*{esc}\s+[^\n|]{{0,120}}\bIngen\b\s*\|", normalized_blob):
        return True
    # Common compact-table ordering: "Ingen | 7.1.3 ...".
    if re.search(rf"(?i)\bIngen\b\s*\|\s*(?<![\d.]){esc}(?![\d.])", normalized_blob):
        return True
    # OCR/compacted variants can lose trailing pipe delimiters. Use a bounded local
    # window and only accept Ingen when it appears before the next structured point id.
    around_re = re.compile(rf"(?i)(?<![\d.]){esc}(?![\d.])")
    next_structured_id_re = re.compile(r"\b\d{1,2}(?:\.\d{1,2}){2,4}\b")
    for m in around_re.finditer(normalized_blob):
        window = normalized_blob[m.start(): m.start() + 260]
        ingen_m = re.search(r"(?i)\bingen\b", window)
        if not ingen_m:
            continue
        next_id_m = next_structured_id_re.search(window, 1)
        if next_id_m and next_id_m.start() < ingen_m.start():
            continue
        return True
    return False


def _fallback_infer_tg_from_point_text(text: str, normalize_text_fn) -> str:
    normalized = _normalize_tg3_cost_text(normalize_text_fn(text or ""))
    if not normalized:
        return ""
    explicit = _extract_tg_label_from_text(normalized)
    if explicit:
        return explicit
    if _TILSTANDSGRAD_NOT_INSPECTED_RE.search(normalized):
        return "TGIU"
    if _TG3_FALLBACK_SIGNAL_RE.search(normalized):
        return "TG3"
    if _TG2_FALLBACK_SIGNAL_RE.search(normalized):
        return "TG2"
    return ""


def _effective_point_tg(point: Dict[str, object], report_text: Optional[str] = None) -> str:
    if not isinstance(point, dict):
        return ""
    if bool(point.get("no_tg_hms_point")):
        return ""
    explicit_tg = _normalize_tg_label(point.get("tg"))
    pid = _normalize_point_id(str(point.get("point_id") or point.get("numeric_id") or point.get("native_label") or ""))
    if str(point.get("tg_source") or "") == "fremtind_summary" and explicit_tg in {"TG2", "TG3", "TGIU"}:
        target_re = re.compile(rf"(?i)(?<![\d.]){re.escape(pid)}(?![\d.])") if pid else None
        for source_key in ("exact_span_text", "span_text", "effective_span_text", "excerpt"):
            source_text = str(point.get(source_key) or "")
            if not source_text:
                continue
            if target_re:
                for match in target_re.finditer(source_text):
                    window = source_text[max(0, match.start() - 32): match.end() + 520]
                    local_tg = _extract_tg_label_from_text(window)
                    if local_tg in {"TG2", "TG3", "TGIU"} and local_tg != explicit_tg:
                        return local_tg
            local_tg = _extract_tg_label_from_text(source_text)
            if local_tg in {"TG2", "TG3", "TGIU"} and local_tg != explicit_tg:
                return local_tg
        return explicit_tg
    raw_main = str(
        point.get("exact_span_text")
        or point.get("span_text")
        or point.get("effective_span_text")
        or point.get("excerpt")
        or ""
    )
    for marker in ("[TABELLDATA]", "[BILDE DETEKTERT", "[PDF METADATA]", "[START RAPPORTTEKST]"):
        marker_idx = raw_main.find(marker)
        if marker_idx >= 0:
            raw_main = raw_main[:marker_idx]
    main_text = _normalize_tg3_cost_text(raw_main)
    pid = _normalize_point_id(str(point.get("point_id") or point.get("numeric_id") or point.get("native_label") or ""))
    # Numeric sub-points (e.g. 7.1.1): TG must be explicit in this point's own window.
    # Do not inherit the parent's TG from metadata or from merged cross-point text.
    if _looks_like_structured_point_id(pid) and pid.count(".") >= 2:
        target_re = re.compile(rf"(?i)(?<![\d.]){re.escape(pid)}(?![\d.])")
        linked_summary = str(point.get("linked_summary_text") or "")
        linked_match = target_re.search(linked_summary)
        if linked_match:
            linked_window = linked_summary[linked_match.start():]
            linked_tg = _extract_tg_label_from_text(linked_window)
            if linked_tg and not _bmtf_subpoint_marked_ingen_in_condensed_table(
                _normalize_tg3_cost_text(linked_window),
                pid,
            ):
                return linked_tg
        scoped = _trim_text_to_point_window(raw_main.strip(), pid, _normalize_tg3_cost_text)
        scoped_for_tg = scoped if scoped else raw_main
        target_match = target_re.search(scoped_for_tg)
        if target_match and target_match.start() > 0:
            scoped_for_tg = scoped_for_tg[target_match.start():]
        blob = _normalize_tg3_cost_text("\n".join(part for part in (report_text or "", raw_main) if part))
        if len(blob) > 200000:
            blob = blob[:200000]
        marked_ingen = _bmtf_subpoint_marked_ingen_in_condensed_table(blob, pid)
        local_marked_ingen = _bmtf_subpoint_marked_ingen_in_condensed_table(
            _normalize_tg3_cost_text(scoped_for_tg),
            pid,
        )
        extracted_tg = _extract_tg_label_from_text(scoped_for_tg)
        if extracted_tg and not local_marked_ingen and not marked_ingen:
            return extracted_tg
        if marked_ingen or local_marked_ingen:
            return ""
        return ""
    extracted_tg = _extract_tg_label_from_text(main_text)
    if extracted_tg in {"TG2", "TG3", "TGIU"}:
        return extracted_tg
    if (
        extracted_tg in {"TG0", "TG1"}
        and explicit_tg in {"TG2", "TG3", "TGIU"}
        and bool(point.get("tg_inferred_fallback"))
        and str(point.get("linked_summary_text") or "").strip()
    ):
        return explicit_tg
    if _looks_like_structured_point_id(pid):
        return ""
    return explicit_tg


def _extract_local_tg_for_point_id(point_id: str, text: str) -> str:
    pid = _normalize_point_id(point_id)
    source = str(text or "")
    if not pid or not source:
        return ""
    target_re = re.compile(rf"(?i)(?<![\d.]){re.escape(pid)}(?![\d.])")
    before_tg_re = re.compile(r"(?i)(TG\s*IU|TG\s*[0-3]|tilstandsgrad\s*[:\-]?\s*(?:IU|[0-3]))\s*$")
    after_tg_re = re.compile(r"(?i)^.{0,140}?\b(TG\s*IU|TG\s*[0-3]|tilstandsgrad\s*[:\-]?\s*(?:IU|[0-3]))\b")
    matches = list(target_re.finditer(source))
    for match in matches:
        before = source[max(0, match.start() - 32):match.start()]
        before_match = before_tg_re.search(before)
        if before_match:
            local_tg = _extract_tg_label_from_text(before_match.group(1))
            if local_tg in {"TG2", "TG3", "TGIU"}:
                return local_tg
    for match in matches:
        after = source[match.end():match.end() + 180].split("\n", 1)[0]
        after_match = after_tg_re.search(after)
        if after_match:
            local_tg = _extract_tg_label_from_text(after_match.group(1))
            if local_tg in {"TG2", "TG3", "TGIU"}:
                return local_tg
    return ""


def _merge_point_tg(existing_tg: object, candidate_tg: object) -> str:
    existing = _normalize_tg_label(existing_tg)
    if existing:
        return existing
    return _normalize_tg_label(candidate_tg)


POINT_ID_IN_TEXT_RE = re.compile(r"(?:Punkt|punkt)\s+(\d+(?:\.\d+)*)", re.IGNORECASE)
NAKED_POINT_ID_RE = re.compile(r"\b([1-9]|1[0-2])(?:\.\d{1,2}){1,3}\b")
POINT_ID_SUFFIX_RE = re.compile(r"[_\-](\d+(?:\.\d+)*)$")
# Stray glyphs from broken PDF encodings – includes CJK blocks and private-use area.
SUSPICIOUS_CJK_RE = re.compile(r"[\u3400-\u9FFF\uF900-\uFAFF\uE000-\uF8FF]")
_CONTROL_TEXT_RE = re.compile(r"[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]")
_SOFT_HYPHEN_LINEBREAK_RE = re.compile(r"([A-Za-zÆØÅæøå])[\-\u00AD]\s*\n\s*([A-Za-zÆØÅæøå])")
_NBSP_RE = re.compile(r"[\u00A0\u202F]")
_ZERO_WIDTH_TEXT_RE = re.compile(r"[\u200B-\u200D\u2060\uFEFF]")
_TRAILING_WS_BEFORE_NL_RE = re.compile(r"[ \t]+\n")
_EXCESS_BLANKLINES_RE = re.compile(r"\n{3,}")


def _strip_suspicious_cjk(text: str) -> str:
    """
    Remove stray CJK / private-use glyphs that leak in from broken PDF encodings.
    These often appear in the middle of otherwise Latin/Norwegian text (e.g. 'Ser玲栠的栠sert').
    For our use-case (Norwegian reports), it's better UX to drop them than to surface garbage.
    """
    if not text or not isinstance(text, str):
        return text or ""
    return SUSPICIOUS_CJK_RE.sub("", text)


def _normalize_report_text_for_analysis(text: str) -> str:
    """
    Normalize extracted PDF text before detection/scoring:
    - remove broken-encoding glyph noise and control chars
    - normalize Unicode/dashes/spacing
    - merge line-break hyphenation in Latin/Norwegian words
    """
    if not text or not isinstance(text, str):
        return ""
    s = unicodedata.normalize("NFKC", text)
    s = _strip_suspicious_cjk(s)
    s = _ZERO_WIDTH_TEXT_RE.sub("", s)
    s = _NBSP_RE.sub(" ", s)
    s = s.translate(_DASH_TRANSLATION_TABLE)
    s = _CONTROL_TEXT_RE.sub("", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _SOFT_HYPHEN_LINEBREAK_RE.sub(r"\1\2", s)
    s = _TRAILING_WS_BEFORE_NL_RE.sub("\n", s)
    s = _EXCESS_BLANKLINES_RE.sub("\n\n", s)
    replacements = (
        (r"\blstand\b", "tilstand"),
        (r"\bLstand\b", "Tilstand"),
        (r"\blstrekkelig\b", "tilstrekkelig"),
        (r"\bLstrekkelig\b", "Tilstrekkelig"),
    )
    for pattern, replacement in replacements:
        s = re.sub(pattern, replacement, s)
    return s.strip()

# Content-based ARK/ARKAT detection (semantic, no strict labels) – per-segment validation
ARK_ÅRSAK_RE = re.compile(
    r"årsak|begrunnelse|fordi|på grunn|forårsaket|vurderes å være|vurderes da|grunnet|pga\.|forklaring|derfor er|"
    r"satt med bakgrunn i|gis med bakgrunn i|vurderes med bakgrunn i",
    re.IGNORECASE,
)
ARK_RISIKO_RE = re.compile(
    r"risiko|kan føre til|kan utvikle|sannsynlighet|mulig videre|utvikling|kan medføre|usikkerhet",
    re.IGNORECASE,
)
ARK_KONSEKVENS_RE = re.compile(
    r"konsekvens|kjøper må|må påregne|utbedring|vedlikehold|behov for|praktisk betydning|innebærer at|for kjøper",
    re.IGNORECASE,
)
# Semantic detection: actionable verbs/phrases for recommended action (not strict "Anbefalt tiltak:" heading).
ARK_TILTAK_RE = re.compile(
    r"anbefalt\s+tiltak|anbefalte\s+tiltak|(?:andre\s+)?tiltak\s*:|anbefales|anbefaler\s+(?:at|[aå])|"
    r"må\s+utbedres|krever\s+utbedring|bør\s+skiftes|utskiftning\s+anbefales|det\s+anbefales|anbefalt\s+å|anbefales\s+å|"
    r"bør\s+undersøkes|undersøkelse\s+(?:av|ved)|utført\s+av\s+fagperson|videre\s+undersøkelser|"
    r"etableres|utbedres|skiftes|undersøkes\s+av|må\s+undersøkes",
    re.IGNORECASE,
)
OBSERVATION_PRESENT_RE = re.compile(
    r"(?ix)\b(?:registrert|observert|påvist|målt|opplyst|det\s+er|det\s+ble|avvik|mangl(?:er|ende)|"
    r"dokumentasjon(?:en)?|ikke\s+dokumentert|ikke\s+framlagt|ikke\s+opplyst|ukjent|alder|byggeår|"
    r"ventilasjon|terreng|fall\s+mot|rør|vannrør|radon|fdv|planløsning|plantegning|elektrisk)\b"
)
PRACTICAL_CONSEQUENCE_RE = re.compile(
    r"(?ix)\b(?:kan\s+føre\s+til|kan\s+medføre|kan\s+gi|fare\s+for|risiko\s+for|fuktskade|fuktproblem|"
    r"råte|mugg|sopp|lekkasje|følgeskade|videre\s+skade|helserisiko|sikkerhetsrisiko|brannfare|"
    r"økte?\s+kostnader|utbedringsbehov|vedlikeholdsbehov|inntrengning|belastning)\b"
)
RECOMMENDED_ACTION_FREE_TEXT_RE = re.compile(
    r"(?ix)\b(?:følges?\s+opp|kontrolleres?|undersøkes?|utbedres?|skiftes?|dokumenteres?|"
    r"fremskaffes?|innhentes?|avklares?|måles?|overvåkes?|rettes?|justeres?|utføres?\s+av\s+fagperson)\b"
)
DOCUMENTATION_GOOD_ENOUGH_RE = re.compile(
    r"(?ix)"
    r"(?:\bdokumentasjon(?:en)?\s+(?:foreligger|er\s+framlagt|er\s+innhentet|er\s+dokumentert)\b)"
    r"|(?:\b(?:samsvarserklæring|fdv|plantegning|planløsning|radonmåling|radon)\b[^.\n]{0,80}\b(?:foreligger|framlagt|utført|dokumentert|målt|under)\b)"
    r"|(?:\b(?:ingen|uten)\s+avvik\b)"
    r"|(?:\bi\s+orden\b)"
)
PDF_NOISE_PATTERNS = [
    re.compile(r"^\s*\d+\s*/\s*\d+\s+.*"),
    re.compile(r"^\s*(BMTF|Byggmestrenes Takseringsforbund|EIERSKIFTERAPPORT|Tilstandsrapport|Norsk Takst).*", re.IGNORECASE),
    re.compile(r"^\s*Side\s+\d+\s+av\s+\d+\s*$", re.IGNORECASE),
]

_client = None


def get_openai_client():
    """Get or create OpenAI client instance"""
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for text.
    Rough approximation: 1 token ≈ 4 characters for Norwegian text.
    """
    return len(text) // 4


def _preflight_token_overflow_allowance(available_tokens: int) -> int:
    """
    Allow modest slack in the preflight token check because the estimator is intentionally rough.
    This prevents borderline reports from being rejected before the model is even tried.
    """
    return max(1500, int(available_tokens * 0.05))


def _run_client_arkat_semantic_pipeline(
    report_text: str,
    detected_points: List[Dict[str, object]],
    analysis_output: Dict[str, object],
    report_date_override: str = "",
) -> None:
    if _report_text_suggests_fremtind_template(report_text or "") and isinstance(detected_points, list):
        summary_tg_by_point = {
            _normalize_point_id(str(point.get("point_id") or "")): _normalize_tg_label(point.get("tg"))
            for point in _extract_compressed_mixed_summary_tg_points(report_text or "")
            if isinstance(point, dict)
        }
        for point in detected_points:
            if not isinstance(point, dict):
                continue
            point_id = _normalize_point_id(
                str(point.get("point_id") or point.get("canonical_point_id") or point.get("native_label") or "")
            )
            summary_tg = summary_tg_by_point.get(point_id)
            if summary_tg in {"TG2", "TG3", "TGIU"}:
                point["tg"] = summary_tg
                point["tg_source"] = "fremtind_summary"
    def _semantic_regime_context(text: str) -> Dict[str, object]:
        context = _extract_report_regime_context(text)
        meta = analysis_output.get("meta") if isinstance(analysis_output.get("meta"), dict) else {}
        meta_report_date = str(meta.get("report_date") or analysis_output.get("report_date") or "").strip()
        meta_ns_version = str(meta.get("ns_version") or meta.get("ns_standard_version") or analysis_output.get("ns_version") or "").strip()
        if meta_report_date and not context.get("report_date"):
            context["report_date"] = meta_report_date
        if meta_ns_version and not context.get("ns_version"):
            context["ns_version"] = meta_ns_version
        override = str(report_date_override or "").strip()
        if override:
            context["report_date"] = override
        if context.get("report_date") or context.get("ns_version"):
            context["report_regime"] = _detect_report_regime(
                str(context.get("report_date") or ""),
                str(context.get("ns_version") or ""),
            )
        return context

    semantic_report_text = report_text or ""
    meta = analysis_output.get("meta") if isinstance(analysis_output.get("meta"), dict) else {}
    meta_report_date = str(meta.get("report_date") or analysis_output.get("report_date") or report_date_override or "").strip()
    meta_ns_version = str(meta.get("ns_version") or meta.get("ns_standard_version") or analysis_output.get("ns_version") or "").strip()
    regime_prefix_lines = []
    if meta_report_date and not _detect_report_date(semantic_report_text):
        regime_prefix_lines.append(f"Rapportdato: {meta_report_date}")
    if meta_ns_version and not _detect_ns_standard_version(semantic_report_text):
        regime_prefix_lines.append(meta_ns_version.replace("NS3600", "NS 3600"))
    if regime_prefix_lines:
        semantic_report_text = "\n".join(regime_prefix_lines) + "\n\n" + semantic_report_text

    _run_client_arkat_semantic_pipeline_service(
        report_text=semantic_report_text,
        detected_points=detected_points,
        analysis_output=analysis_output,
        deps={
            "normalize_text": _normalize_tg3_cost_text,
            "split_pages": _split_pages,
            "extract_arkat_section_text": _extract_arkat_section_text,
            "extract_linked_summary_text_per_point": _extract_linked_summary_text_per_point,
            "get_linked_summary_for_point": _get_linked_summary_for_point,
            "extract_report_regime_context": _semantic_regime_context,
            "effective_point_tg": lambda p: _effective_point_tg(p, report_text),
            "normalize_point_id": _normalize_point_id,
            "is_synthetic_supplement_point_id": _is_synthetic_supplement_point_id,
            "is_parent_of": _is_parent_of,
            "append_unique_all_finding": _append_unique_all_finding,
            "iso_date_at_or_after": _iso_date_at_or_after,
            "railings_topic_re": RAILINGS_TOPIC_RE,
        },
    )
    finalize_client_arkat_semantic_pipeline_output(analysis_output, _normalize_tg3_cost_text)


def _semantic_arkat_points_by_id(analysis_output: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    pipeline = analysis_output.get("arkat_semantic_pipeline") if isinstance(analysis_output, dict) else None
    points = pipeline.get("points") if isinstance(pipeline, dict) else None
    if not isinstance(points, list):
        return {}
    out: Dict[str, Dict[str, object]] = {}
    for point in points:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or ""))
        if point_id:
            out[point_id] = point
    return out


def _semantic_arkat_present_and_missing_keys(point_payload: Dict[str, object], tg: str) -> Tuple[set, List[str]]:
    field_to_segment_key = {
        "aarsak": "årsak",
        "risiko": "risiko",
        "konsekvens": "konsekvens",
        "anbefalt_tiltak": "anbefalt_tiltak",
    }
    segment_to_field_key = {segment_key: field_key for field_key, segment_key in field_to_segment_key.items()}
    evaluation = point_payload.get("evaluation") if isinstance(point_payload, dict) else None
    field_results = evaluation.get("field_results") if isinstance(evaluation, dict) else None
    if not isinstance(field_results, dict):
        return set(), list(_required_segment_keys_for_tg(tg))

    present = set()
    missing: List[str] = []
    for segment_key in _required_segment_keys_for_tg(tg):
        field_key = segment_to_field_key.get(segment_key)
        result = field_results.get(field_key) if field_key else None
        status = str(result.get("status") or "").strip() if isinstance(result, dict) else ""
        if status.startswith("CORRECT"):
            present.add(segment_key)
        else:
            missing.append(segment_key)
    return present, missing


def _split_pages(report_text: str) -> List[Dict[str, str]]:
    if not report_text:
        return []
    parts = PAGE_MARKER_RE.split(report_text)
    pages: List[Dict[str, str]] = []
    for i in range(1, len(parts), 2):
        try:
            page_num = int(parts[i])
        except ValueError:
            continue
        page_text = _strip_pdf_noise(parts[i + 1].strip())
        pages.append({"page": page_num, "text": page_text})
    return pages


def _strip_pdf_noise(text: str) -> str:
    if not text:
        return ""
    cleaned_lines = []
    for line in text.splitlines():
        # Drop boilerplate/footer noise and stray CJK glyphs from broken PDF encodings
        line = _strip_suspicious_cjk(line)
        if any(pattern.match(line) for pattern in PDF_NOISE_PATTERNS):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _extract_snippet(text: str, index: int, window: int = 220) -> str:
    if not text:
        return ""
    start = max(index - window // 2, 0)
    end = min(index + window // 2, len(text))
    return text[start:end].strip()


def _normalize_title_for_trace(title: str) -> str:
    """Normalize title for trace output (strip, lower, collapse whitespace, truncate)."""
    if not title or not isinstance(title, str):
        return ""
    return " ".join(str(title).strip().lower().split())[:80]


_BEFAR_PRIMARY_TG_HEADER_RE = re.compile(
    r"(?i)^\s*TG\s*(?P<tg>2|3|IU)\s+"
    r"(?P<main>\d{1,2})(?:[.\s]+(?P<sub>\d{1,2}))?\.?\s+"
    r"(?P<title>[A-ZÆØÅ0-9][^\n]{2,})\s*$"
)


def _clean_befar_primary_title(value: str) -> str:
    text = re.sub(r"\(cid:\d+\)", "", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip(" .:-")
    return text.title() if text.isupper() else text


def _befar_primary_point_id(main: str, sub: str = "") -> str:
    main_norm = str(main or "").strip().lstrip("0") or str(main or "").strip()
    sub_norm = str(sub or "").strip().lstrip("0")
    if sub_norm:
        return f"{main_norm}.{sub_norm}"
    return main_norm


def _befar_page_primary_text(page_text: str) -> str:
    text = str(page_text or "")
    if "[TABELLDATA]" in text:
        text = text.split("[TABELLDATA]", 1)[0]
    return text.strip()


def _extract_befar_primary_tg_sections(report_text: str) -> List[Dict[str, object]]:
    if not _report_text_suggests_befar_template(report_text or ""):
        return []
    sections: List[Dict[str, object]] = []
    seen_keys: set = set()
    for page in _split_pages(report_text or ""):
        page_text = _befar_page_primary_text(page.get("text") or "")
        if not page_text:
            continue
        lines = page_text.splitlines()
        header_idx = -1
        header_match = None
        for idx, line in enumerate(lines[:8]):
            match = _BEFAR_PRIMARY_TG_HEADER_RE.match(line.strip())
            if match:
                header_idx = idx
                header_match = match
                break
        if not header_match or header_idx < 0:
            continue
        span_lines = lines[header_idx:]
        span_text = "\n".join(span_lines).strip()
        if "konklusjon bygningsdel" not in span_text.lower():
            continue
        tg = f"TG{str(header_match.group('tg')).upper()}".replace("TGIU", "TGIU")
        point_id = _befar_primary_point_id(header_match.group("main"), header_match.group("sub") or "")
        title = _clean_befar_primary_title(header_match.group("title") or "")
        title_norm = _normalize_tg3_cost_text(title).lower()
        key = (point_id, title_norm, int(page.get("page") or 1))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        sections.append(
            {
                "point_key": f"B{len(sections) + 1:04d}",
                "native_label": point_id,
                "numeric_id": point_id,
                "native_path": [],
                "kind": "point",
                "point_id": point_id,
                "title": title or f"Punkt {point_id}",
                "page_start": int(page.get("page") or 1),
                "page_end": int(page.get("page") or 1),
                "order_in_doc": len(sections) + 1,
                "anchor_text": lines[header_idx].strip(),
                "span_hash": hashlib.sha256(span_text.encode("utf-8")).hexdigest() if span_text else "",
                "excerpt": title or span_text[:200].strip(),
                "tg": tg,
                "span_text": span_text,
                "exact_span_text": span_text,
                "effective_span_text": span_text,
                "source_primary_tg_conclusion": True,
                "source_tg_marker": lines[header_idx].strip(),
            }
        )
    return sections


def _merge_befar_primary_sections(
    detected: List[Dict[str, object]],
    primary_sections: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    if not primary_sections:
        return detected
    primary_by_id: Dict[str, Dict[str, object]] = {}
    for section in primary_sections:
        if not isinstance(section, dict):
            continue
        point_id = _normalize_point_id(str(section.get("point_id") or ""))
        if not point_id:
            continue
        existing = primary_by_id.get(point_id)
        if not existing or len(str(section.get("span_text") or "")) > len(str(existing.get("span_text") or "")):
            primary_by_id[point_id] = section

    merged: List[Dict[str, object]] = []
    replaced_ids: set = set()
    for point in detected:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or point.get("numeric_id") or point.get("native_label") or ""))
        replacement = primary_by_id.get(point_id)
        if replacement:
            if point_id in replaced_ids:
                continue
            merged.append(dict(replacement))
            replaced_ids.add(point_id)
            continue
        if bool(point.get("synthetic_supplement")):
            continue
        merged.append(point)
    for point_id, section in primary_by_id.items():
        if point_id not in replaced_ids:
            merged.append(dict(section))
    for idx, point in enumerate(merged, start=1):
        if isinstance(point, dict):
            point["order_in_doc"] = idx
            point.setdefault("point_key", f"P{idx:04d}")
    return merged


def _extract_detected_points(report_text: str, trace: Optional[Dict[str, object]] = None) -> List[Dict[str, object]]:
    pages = _split_pages(report_text)
    line_index: List[Dict[str, object]] = []
    for page in pages:
        for line in page["text"].splitlines():
            line_index.append({"page": page["page"], "text": line})

    headings: List[Dict[str, object]] = []
    stray_rejected: List[Dict[str, object]] = []
    for idx, line in enumerate(line_index):
        text = line["text"]
        match = POINT_HEADER_RE.match(text) or POINT_HEADER_FALLBACK_RE.match(text)
        if match:
            raw_id = (match.group(1) or "").strip()
            section_title = (match.group(2) or "").strip() if match.lastindex >= 2 else ""
            if not raw_id or _looks_like_date_point_id(raw_id) or _is_noise_point_id(raw_id):
                continue
            if _looks_like_date_line(text):
                continue
            if _is_stray_point_header(text, raw_id, section_title):
                if trace is not None:
                    stray_rejected.append({
                        "point_id": _normalize_point_id(raw_id),
                        "normalized_title": _normalize_title_for_trace(section_title or text),
                        "reason": "stray_title",
                    })
                continue
            if _is_false_point_header(text, raw_id, section_title):
                continue
            point_id = _normalize_point_id(raw_id)
            headings.append({"idx": idx, "point_id": point_id, "section_title": section_title})
    if trace is not None:
        trace["stray_rejected"] = stray_rejected
        trace["stray_rejected_count"] = len(stray_rejected)

    detected: List[Dict[str, object]] = []
    if headings:
        for i, heading in enumerate(headings):
            start_idx = heading["idx"]
            end_idx = headings[i + 1]["idx"] if i + 1 < len(headings) else len(line_index)
            span_lines = line_index[start_idx:end_idx]
            span_text = "\n".join(item["text"] for item in span_lines).strip()
            page_start = span_lines[0]["page"] if span_lines else line_index[start_idx]["page"]
            page_end = span_lines[-1]["page"] if span_lines else page_start
            section_title = heading["section_title"] or ""
            excerpt = section_title or (span_text[:200].strip() if span_text else "")
            if not excerpt:
                excerpt = f"Punkt {heading['point_id']}"
            native_label = heading["point_id"]
            numeric_id = native_label if _is_numeric_point_id(native_label) else ""
            order_in_doc = i + 1
            anchor_text = span_lines[0]["text"] if span_lines else ""
            detected.append(
                {
                    "point_key": f"P{order_in_doc:04d}",
                    "native_label": native_label,
                    "numeric_id": numeric_id or None,
                    "native_path": [],
                    "kind": "point",
                    "point_id": heading["point_id"],
                    "title": section_title or "Ukjent",
                    "page_start": page_start,
                    "page_end": page_end,
                    "order_in_doc": order_in_doc,
                    "anchor_text": anchor_text,
                    "span_hash": hashlib.sha256(span_text.encode("utf-8")).hexdigest() if span_text else "",
                    "excerpt": excerpt,
                    "tg": _extract_tg_label_from_text(span_text),
                    "span_text": span_text,
                }
            )

    befar_primary_sections = _extract_befar_primary_tg_sections(report_text or "")
    detected = _merge_befar_primary_sections(detected, befar_primary_sections)
    if trace is not None:
        trace["befar_primary_tg_sections_count"] = len(befar_primary_sections)
        trace["befar_primary_tg_sections"] = [
            {
                "point_id": section.get("point_id"),
                "title": section.get("title"),
                "tg": section.get("tg"),
                "page": section.get("page_start"),
                "span_hash": section.get("span_hash"),
            }
            for section in befar_primary_sections
            if isinstance(section, dict)
        ]

    # Supplement extraction with heading-like lines classified by whitelist v2.2.
    # Some templates have partial/unstable numeric structure; keep this available
    # even when numeric headers exist so canonical mapping can still recover.
    wl = _get_whitelist_v22_lookup()
    if wl and line_index and not befar_primary_sections:
        norm_cfg = wl.get("norm_cfg") or {}
        reject_patterns = wl.get("reject_patterns") or []
        building_parts = wl.get("building_parts") or []
        existing_norm_titles = {
            _normalize_title_v22(str(p.get("title") or ""), norm_cfg)
            for p in detected
            if isinstance(p, dict) and p.get("title")
        }
        heading_supplement_count = 0
        max_heading_supplements = 40
        for line in line_index:
            if heading_supplement_count >= max_heading_supplements:
                break
            raw = str(line.get("text") or "").strip()
            if not _looks_like_section_heading_candidate(raw):
                continue
            if not _contains_mapping_alias_hint(raw):
                continue
            norm_title = _normalize_title_v22(raw, norm_cfg)
            if not norm_title or norm_title in existing_norm_titles:
                continue
            if _matches_reject_if_regex(norm_title, reject_patterns):
                continue
            if not _classify_to_building_part_v22(norm_title, building_parts):
                continue
            heading_supplement_count += 1
            synthetic_id = str(90000 + heading_supplement_count)
            detected.append(
                {
                    "point_key": f"H{heading_supplement_count:04d}",
                    "native_label": synthetic_id,
                    "numeric_id": synthetic_id,
                    "native_path": [],
                    "kind": "point",
                    "point_id": synthetic_id,
                    "title": raw,
                    "page_start": int(line.get("page") or 1),
                    "page_end": int(line.get("page") or 1),
                    "order_in_doc": len(detected) + 1,
                    "anchor_text": raw,
                    "span_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                    "excerpt": raw,
                    "tg": "",
                    "span_text": raw,
                    "synthetic_supplement": True,
                }
            )
            existing_norm_titles.add(norm_title)
        if trace is not None:
            trace["heading_supplement_count"] = heading_supplement_count

    if not detected:
        # Fallback: no point headers found – treat whole report as one segment so we don't silently give 100%.
        full_text = "\n".join(item["text"] for item in line_index).strip()
        page_start = line_index[0]["page"] if line_index else 1
        page_end = line_index[-1]["page"] if line_index else page_start
        detected.append(
            {
                "point_key": "P0001",
                "native_label": "1",
                "numeric_id": "1",
                "native_path": [],
                "kind": "point",
                "point_id": "1",
                "title": "Hele rapporten (ingen punktoverskrifter funnet)",
                "page_start": page_start,
                "page_end": page_end,
                "order_in_doc": 1,
                "anchor_text": line_index[0]["text"] if line_index else "",
                "span_hash": hashlib.sha256(full_text.encode("utf-8")).hexdigest() if full_text else "",
                "excerpt": full_text[:200].strip() if full_text else "",
                "tg": _extract_tg_label_from_text(full_text) or "TG2",
                "span_text": full_text,
                "segmentation_fallback": True,
            }
        )
    return detected


def _build_detected_points_payload(
    detected_points: List[Dict[str, object]],
    document_hash: str,
    document_title: Optional[str],
    document_id: Optional[str],
    pdf_metadata: Optional[Dict[str, object]],
    points_before_whitelist: Optional[List[Dict[str, object]]] = None,
) -> Dict[str, object]:
    page_count = 1
    if isinstance(pdf_metadata, dict):
        page_count = int(pdf_metadata.get("total_pages") or pdf_metadata.get("pages_with_text") or 1)
    source_filename = document_title or (f"report_{document_id}.pdf" if document_id else "report.pdf")
    points_out = [{k: v for k, v in (p if isinstance(p, dict) else {}).items() if k != "span_text"} for p in detected_points]
    return {
        "version": "v1.2",
        "document": {
            "document_hash": document_hash,
            "source_filename": source_filename,
            "page_count": max(page_count, 1),
            "extraction": {
                "engine": "validert-point-detector",
                "engine_version": "1.0.0",
                "notes": "Point headers detected via regex; whitelist-validated building parts only.",
            },
        },
        "points": points_out,
        # Optional debug/fallback source for canonical mapping if whitelist becomes too aggressive.
        "points_before_whitelist": [
            {k: v for k, v in (p if isinstance(p, dict) else {}).items() if k != "span_text"}
            for p in (points_before_whitelist or [])
        ],
    }


def _dedupe_detected_physical_points(points: List[Dict[str, object]]) -> List[Dict[str, object]]:
    if not isinstance(points, list):
        return []
    deduped: List[Dict[str, object]] = []
    seen: set = set()
    seen_physical_sections: set = set()
    for point in points:
        if not isinstance(point, dict):
            continue
        canonical = _normalize_point_id(
            str(point.get("canonical_point_id") or point.get("point_id") or "")
        )
        numeric = _normalize_point_id(str(point.get("numeric_id") or point.get("native_label") or ""))
        title = _normalize_tg3_cost_text(str(point.get("title") or point.get("excerpt") or "")).lower()
        page = str(point.get("page_start") or "")
        tg = _normalize_tg_label(point.get("tg") or point.get("tg_grade") or "")
        physical_key = (title, page, tg)
        if title and page and physical_key in seen_physical_sections:
            continue
        if title and page:
            seen_physical_sections.add(physical_key)
        key = (canonical or numeric, numeric, title, page)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(point)
    return deduped


def _preserve_report_point_ids_for_non_fremtind(points: List[Dict[str, object]], report_text: str) -> List[Dict[str, object]]:
    if _report_text_suggests_fremtind_template(report_text or ""):
        return points if isinstance(points, list) else []
    preserved: List[Dict[str, object]] = []
    for point in points if isinstance(points, list) else []:
        if not isinstance(point, dict):
            continue
        item = dict(point)
        report_id = _normalize_point_id(str(item.get("numeric_id") or item.get("native_label") or ""))
        if report_id and _is_numeric_point_id(report_id):
            item["point_id"] = report_id
            item["native_label"] = item.get("native_label") or report_id
            item["numeric_id"] = item.get("numeric_id") or report_id
        preserved.append(item)
    return preserved


def get_validated_detected_points_payload(
    extracted_text: str,
    document_hash: str = "",
    document_title: Optional[str] = None,
    document_id: Optional[str] = None,
    pdf_metadata: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """
    Single source of truth: extract → stray filter → whitelist (hard gate) → build payload.
    Only validated building-part segments are included. Use this for points_overview, hierarchy,
    and ARKAT/cost rules. Never use raw/stored detected_points for UI rendering.
    """
    normalized_text = _normalize_report_text_for_analysis(extracted_text or "")
    if not normalized_text or not normalized_text.strip():
        return _build_detected_points_payload(
            [], document_hash or "", document_title, document_id, pdf_metadata
        )
    trace: Dict[str, object] = {
        "total_detected_before_stray": 0,
        "total_after_stray": 0,
        "total_after_whitelist": 0,
    }
    detected = _extract_detected_points(normalized_text, trace=trace)
    trace["total_after_stray"] = len(detected)
    trace["total_detected_before_stray"] = len(detected) + int(trace.get("stray_rejected_count", 0) or 0)
    detected = _dedupe_detected_physical_points(detected)
    validated = _validate_detected_points_against_whitelist(detected, trace=trace)
    validated = _dedupe_detected_physical_points(_normalize_runtime_scoring_signals(validated))
    validated = _preserve_report_point_ids_for_non_fremtind(validated, normalized_text)
    trace["total_after_whitelist"] = len(validated)
    trace["validated_point_ids"] = [p.get("point_id") for p in validated if isinstance(p, dict) and p.get("point_id")]
    payload = _build_detected_points_payload(
        validated,
        document_hash=document_hash or hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(),
        document_title=document_title,
        document_id=document_id,
        pdf_metadata=pdf_metadata,
        points_before_whitelist=detected,
    )
    payload["segmentation_trace"] = trace
    return payload


def _is_numeric_point_id(value: str) -> bool:
    if not value:
        return False
    return bool(re.match(r"^\d+(?:\.\d+)*$", value))


def _is_synthetic_supplement_point_id(value: str) -> bool:
    if not value or not isinstance(value, str):
        return False
    s = value.strip().rstrip(".")
    return bool(re.match(r"^\d+$", s) and int(s) >= 90000)


def _is_scoring_eligible_point_id(value: str) -> bool:
    if not _is_numeric_point_id(value):
        return False
    if _is_noise_point_id(value):
        return False
    if _is_synthetic_supplement_point_id(value):
        return False
    return True


def _is_segment_validation_point_id(value: str) -> bool:
    """Point IDs eligible for per-segment ARKAT/cost validation."""
    return _is_scoring_eligible_point_id(value) or _is_canonical_child_point_id(value)


def _looks_like_date_point_id(value: str) -> bool:
    if not value:
        return False
    return bool(DATE_POINT_ID_RE.match(value))


def _looks_like_date_line(line_text: str) -> bool:
    """True if line looks like a date (e.g. 05/01/2026 or 05.01.2026), to avoid detecting as point."""
    if not line_text or not isinstance(line_text, str):
        return False
    stripped = line_text.strip()
    return bool(re.search(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}", stripped))


def _is_noise_point_id(point_id: str) -> bool:
    """True if point_id is likely noise: 0 (table count), or 4-digit postcode (e.g. 2072)."""
    if not point_id:
        return True
    s = str(point_id).strip().rstrip(".")
    if s == "0":
        return True
    if re.match(r"^\d{4}$", s):
        return True
    return False


# Table/category labels that are not real bygningsdel points (BMTF/eierskifte).
_FALSE_POINT_TITLES = (
    "ingen vesentlige avvik",
    "vesentlige avvik",
    "store eller alvorlige avvik",
    "ikke undersøkt",
)

# Stray text fragments that must NOT be treated as valid points (segmentation integrity).
_STRAY_POINT_PATTERNS = (
    "etg:",                      # Floor/cost table row
    "for ytterligere vurderinger",  # Continuation text, not a building part
    "kostnadspekulasjon",         # TG1 cost section
    "vurdering:",                 # Standalone assessment label
)


def _is_stray_point_header(line_text: str, raw_id: str, section_title: str) -> bool:
    """True if this line is stray text (etg:, for ytterligere vurderinger, etc.) - not a valid building-part point."""
    if not line_text or not isinstance(line_text, str):
        return True
    text_lower = line_text.strip().lower()
    title_lower = (section_title or "").strip().lower()
    combined = f"{text_lower} {title_lower}"
    raw_id_clean = str(raw_id or "").strip().rstrip(".")
    # Reject prose such as "Punkt 2-lags glass ..." which is not a real point heading.
    if re.match(r"(?i)^\s*(?:punkt|pkt)\s*\d+\s*[-–—]?\s*lags?\b", line_text.strip()):
        return True
    # Reject floor-height rows that start like "2. etasje: ...".
    if re.match(r"(?i)^\s*\d+\.?\s*etasje\s*[:|]", line_text.strip()):
        return True
    if raw_id_clean.isdigit() and title_lower.startswith(("lags ", "lag ", "etasje:")):
        return True
    for p in _STRAY_POINT_PATTERNS:
        if p in combined:
            return True
    if "etasje >" in combined or "etasje |" in combined:
        return True
    if _is_question_style_heading(section_title or line_text):
        return True
    if title_lower and len(title_lower) < 4 and title_lower.rstrip(".:").isdigit():
        return True
    if text_lower.endswith(":") and len(text_lower) < 15 and not any(c.isalpha() for c in text_lower.replace(":", "")):
        return True
    return False


def _is_false_point_header(line_text: str, raw_id: str, section_title: str) -> bool:
    """True if this line is a table row, page indicator, or category label, not a real report point."""
    if not line_text or not isinstance(line_text, str):
        return True
    line_lower = line_text.strip().lower()
    title_lower = (section_title or "").strip().lower()
    # Table-style fractions or counts like "/37" or "25/37" are not real points
    if re.match(r"^/?\d{1,3}\s*/\s*\d{1,3}\s*$", line_lower):
        return True
    if title_lower in _FALSE_POINT_TITLES:
        return True
    if re.match(r"(?i)^\s*\d+\.?\s*etasje\s*[:|]", line_lower):
        return True
    if "/20" in line_lower and re.match(r"^\d{1,2}\s*/", line_lower):
        return True
    if any(t in title_lower for t in ("ingen vesentlige", "vesentlige avvik", "store eller alvorlige", "ikke undersøkt")):
        return True
    if "etasje >" in title_lower or "etasje |" in title_lower:
        return True
    pid = str(raw_id).strip().rstrip(".")
    if re.match(r"^\d{2}$", pid):
        n = int(pid)
        if 12 <= n <= 24 and "tg" not in line_lower:
            return True
    return False


def _looks_like_section_heading_candidate(line_text: str) -> bool:
    """
    Conservative heading detector used to supplement numeric point extraction.
    """
    if not line_text or not isinstance(line_text, str):
        return False
    s = " ".join(line_text.strip().split())
    if len(s) < 3 or len(s) > 90:
        return False
    low = s.lower()
    low_no_enum = _strip_leading_enumerator(low)
    if "?" in low:
        return False
    if _is_question_style_heading(low_no_enum):
        return False
    if "etasje >" in low or "etasje |" in low:
        return False
    if " m2" in low or "m2," in low or "m2." in low:
        return False
    if low.startswith(("rapportansvarlig", "dette trenger du å vite", "tilstandsrapporten")):
        return False
    if low.startswith(("forutsetninger", "premisser", "ordforklaring", "innholdsfortegnelse")):
        return False
    if re.match(r"^[\d\s.,:/-]+$", low):
        return False
    return True


@lru_cache(maxsize=1)
def _get_mapping_alias_hints() -> Tuple[str, ...]:
    """Collect normalized child aliases to keep supplemental heading extraction strict."""
    cfg = get_points_overview_mapping_config() or {}
    child_mappings = cfg.get("child_mappings") or []
    hints: set = set()
    for item in child_mappings:
        if not isinstance(item, dict):
            continue
        aliases = item.get("aliases") or []
        if not isinstance(aliases, list):
            continue
        for alias in aliases:
            if not isinstance(alias, str):
                continue
            a = _normalize_segment_title_for_canonical_match(alias)
            if len(a) >= 5:
                hints.add(a)
    return tuple(sorted(hints))


@lru_cache(maxsize=1)
def _get_child_mapping_points_for_inference() -> Tuple[Dict[str, object], ...]:
    cfg = get_points_overview_mapping_config() or {}
    child_mappings = cfg.get("child_mappings") if isinstance(cfg, dict) else []
    out: List[Dict[str, object]] = []
    if isinstance(child_mappings, list):
        for item in child_mappings:
            if not isinstance(item, dict):
                continue
            mapping = dict(item)
            if "child_id" in mapping and "canonical_id" not in mapping:
                mapping["canonical_id"] = mapping["child_id"]
            out.append(mapping)
    return tuple(out)


def _contains_mapping_alias_hint(line_text: str) -> bool:
    norm_line = _normalize_segment_title_for_canonical_match(line_text or "")
    if not norm_line:
        return False
    # OCR-friendly fallback for E3 headings (e.g. "VER OPPMERKSOM PA", "VAER OPPMERKSOM PAA").
    norm_ascii = (
        norm_line.replace("æ", "ae")
        .replace("ø", "o")
        .replace("å", "aa")
        .replace("ä", "a")
        .replace("ö", "o")
        .replace("ü", "u")
    )
    if (
        "tilleggsopplysninger" in norm_line
        or "anbefalte ytterligere unders" in norm_line
        or "vaer oppmerksom" in norm_ascii
        or "ver oppmerksom" in norm_ascii
    ):
        return True
    for hint in _get_mapping_alias_hints():
        if hint in norm_line:
            return True
    return False


def _strip_leading_enumerator(text: str) -> str:
    if not text or not isinstance(text, str):
        return ""
    return re.sub(r"^\s*\d+(?:[.)]|:)\s*", "", text).strip()


def _is_question_style_heading(text: str) -> bool:
    s = _strip_leading_enumerator((text or "").lower())
    if not s:
        return False
    if s.startswith(("er det ", "har det ", "foreligger det ", "finnes det ")):
        return True
    if " i boligen iht. forskriftskrav" in s:
        return True
    return False


def _normalize_point_id(raw: str) -> str:
    """
    Canonical form for point IDs so "6.2", "6.2.", "Pkt 6.2" all map to the same key.
    Strip whitespace and trailing dots; no prefix stripping (caller extracts number from regex).
    """
    if not raw or not isinstance(raw, str):
        return (raw or "").strip()
    s = raw.strip().rstrip(".")
    return s.strip() if s else (raw or "").strip()


def _extract_numeric_point_ids_from_text(text: str) -> set:
    normalized = _normalize_tg3_cost_text(text or "")
    if not normalized:
        return set()
    out: set = set()
    for m in POINT_ID_IN_TEXT_RE.finditer(normalized):
        pid = _normalize_point_id(str(m.group(1) or ""))
        if pid:
            out.add(pid)
    for m in NAKED_POINT_ID_RE.finditer(normalized):
        pid = _normalize_point_id(str(m.group(0) or ""))
        if pid:
            out.add(pid)
    return out


def _parse_numeric_id(value: str) -> List[int]:
    return [int(part) for part in value.split(".") if part.isdigit()]


def _numeric_id_for_point(point: Dict[str, object]) -> str:
    numeric_id = point.get("numeric_id") or point.get("point_id") or ""
    return numeric_id if isinstance(numeric_id, str) and _is_numeric_point_id(numeric_id) else ""


def _point_key_for_point(point: Dict[str, object]) -> str:
    return (
        point.get("point_key")
        or point.get("point_id")
        or point.get("native_label")
        or ""
    )


@lru_cache(maxsize=1)
def _get_building_part_whitelist() -> Dict[str, set]:
    """Cached building-part names for segment validation."""
    return get_building_part_whitelist()


def _segment_matches_building_part(title: str, whitelist: Dict[str, set]) -> bool:
    """True if title matches a known building-part (strict segment validation)."""
    if not title or not isinstance(title, str):
        return False
    names = whitelist.get("names") or set()
    norm = " ".join(title.strip().lower().split())
    if not norm or len(norm) < 3:
        return False
    for bp in names:
        if len(bp) < 4:
            continue
        if bp in norm or norm in bp:
            return True
    words = [w for w in norm.split() if len(w) >= 4]
    for w in words:
        if w in names:
            return True
        for bp in names:
            if len(bp) >= 4 and (w in bp or bp in w):
                return True
    return False


def run_segmentation_trace(report_text: str) -> Dict[str, object]:
    """
    Run extraction + validation with tracing. Returns trace for admin debugging.
    """
    trace: Dict[str, object] = {
        "total_detected_before_stray": 0,
        "total_after_stray": 0,
        "total_after_whitelist": 0,
        "stray_rejected_count": 0,
        "stray_rejected": [],
        "whitelist_rejected_count": 0,
        "whitelist_rejected": [],
    }
    detected = _extract_detected_points(report_text or "", trace=trace)
    total_after_stray = len(detected)
    trace["total_after_stray"] = total_after_stray
    trace["total_detected_before_stray"] = total_after_stray + trace.get("stray_rejected_count", 0)
    validated = _validate_detected_points_against_whitelist(detected, trace=trace)
    trace["total_after_whitelist"] = len(validated)
    trace["validated_point_ids"] = [p.get("point_id") for p in validated if isinstance(p, dict) and p.get("point_id")]
    return trace


@lru_cache(maxsize=1)
def _get_whitelist_v22_lookup() -> Optional[Dict]:
    """Build compiled structures for whitelist v2.2: normalization, reject_if_regex, building_parts, instance_extract."""
    wl = get_building_part_whitelist_v22()
    if not wl or not wl.get("building_parts"):
        return None
    norm_cfg = wl.get("normalization") or {}
    reject_patterns = [re.compile(p) for p in (norm_cfg.get("reject_if_regex") or []) if isinstance(p, str)]
    building_parts = [bp for bp in (wl.get("building_parts") or []) if isinstance(bp, dict) and bp.get("id")]
    instance_cfg = (wl.get("classification") or {}).get("instance_extract") or {}
    instance_regex_order = []
    for p in (instance_cfg.get("regex_order") or []):
        if isinstance(p, str):
            try:
                instance_regex_order.append(re.compile(p))
            except re.error:
                pass
    return {
        "norm_cfg": norm_cfg,
        "reject_patterns": reject_patterns,
        "building_parts": building_parts,
        "instance_regex_order": instance_regex_order,
    }


def _normalize_title_v22(title: str, norm_cfg: Dict) -> str:
    """Apply whitelist v2.2 normalization: NFKC, replace_chars, remove_prefixes/suffixes, lowercase, collapse whitespace."""
    if not title or not isinstance(title, str):
        return ""
    s = str(title).strip()
    if norm_cfg.get("remove_soft_hyphen", True):
        s = s.replace("\u00ad", "")
    if norm_cfg.get("unicode_nfkc", True):
        s = unicodedata.normalize("NFKC", s)
    replace_chars = norm_cfg.get("replace_chars") or {}
    for old, new in replace_chars.items():
        if isinstance(old, str) and isinstance(new, str):
            s = s.replace(old, new)
    for pat in (norm_cfg.get("remove_prefixes_regex") or []):
        if isinstance(pat, str):
            try:
                s = re.sub(pat, "", s, flags=re.IGNORECASE)
            except re.error:
                pass
    for pat in (norm_cfg.get("remove_suffixes_regex") or []):
        if isinstance(pat, str):
            try:
                s = re.sub(pat, "", s, flags=re.IGNORECASE)
            except re.error:
                pass
    if norm_cfg.get("lowercase", True):
        s = s.lower()
    if norm_cfg.get("collapse_whitespace", True):
        s = " ".join(s.split())
    return s.strip()


def _matches_reject_if_regex(norm_title: str, reject_patterns: List[re.Pattern]) -> bool:
    """True if normalized title matches any reject_if_regex pattern."""
    if not norm_title:
        return True
    for pat in reject_patterns:
        if pat.search(norm_title):
            return True
    return False


def _classify_to_building_part_v22(norm_title: str, building_parts: List[Dict]) -> Optional[Dict]:
    """Match normalized title against building_parts. Returns first matching part or None."""
    if not norm_title or not building_parts:
        return None
    for bp in building_parts:
        match_cfg = bp.get("match") or {}
        exact_list = match_cfg.get("exact") or []
        if any(norm_title == (e or "").strip().lower() for e in exact_list if isinstance(e, str)):
            return bp
        contains_list = match_cfg.get("contains_any") or []
        if any((c or "").lower() in norm_title for c in contains_list if isinstance(c, str)):
            return bp
        regex_list = match_cfg.get("regex_any") or []
        for rx in regex_list:
            if isinstance(rx, str):
                try:
                    if re.search(rx, norm_title):
                        return bp
                except re.error:
                    pass
    return None


def _extract_instance_label_v22(title: str, norm_title: str, instance_regex_order: List[re.Pattern]) -> Optional[str]:
    """Extract instance label (e.g. '1. etg > bad') from title using instance_extract regex_order."""
    if not instance_regex_order:
        return None
    text = norm_title or (title or "").strip().lower()
    if not text:
        return None
    for pat in instance_regex_order:
        m = pat.search(text)
        if m:
            groups = m.groupdict()
            parts = []
            if "etasje" in groups and groups["etasje"]:
                parts.append(groups["etasje"].strip())
            if "rom" in groups and groups["rom"]:
                parts.append(groups["rom"].strip())
            if "base" in groups and groups["base"]:
                parts.append(groups["base"].strip())
            if parts:
                return " / ".join(parts)
    return None


def _validate_detected_points_against_whitelist_v22(
    detected: List[Dict[str, object]],
    trace: Optional[Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    """
    Whitelist v2.2: normalize -> reject_if_regex -> classify_to_building_part -> extract instance.
    Pipeline: noise rejected first, then unclassified dropped, then segments created with canonical_id + instance.
    """
    if not detected:
        return detected
    wl = _get_whitelist_v22_lookup()
    if not wl:
        return None  # Signal fallback to v2.1
    norm_cfg = wl.get("norm_cfg") or {}
    reject_patterns = wl.get("reject_patterns") or []
    building_parts = wl.get("building_parts") or []
    instance_regex_order = wl.get("instance_regex_order") or []
    mapping_points_for_inference = list(_get_child_mapping_points_for_inference())
    validated: List[Dict[str, object]] = []
    rejected_noise: List[Dict[str, object]] = []
    unclassified: List[Dict[str, object]] = []
    classified_heading = 0
    rejected_noise_count = 0
    unclassified_count = 0
    for p in detected:
        if not isinstance(p, dict):
            continue
        point_id = p.get("point_id") or p.get("numeric_id") or p.get("native_label") or ""
        if not point_id or not _is_numeric_point_id(point_id):
            if trace is not None:
                unclassified.append({"point_id": point_id or "(empty)", "normalized_title": "", "reason": "invalid_point_id"})
                unclassified_count += 1
            continue
        if p.get("segmentation_fallback"):
            validated.append({
                **dict(p),
                "canonical_building_part_id": "BP_LOVLIGHET",
                "required_by_forskrift": True,
                "ui_badge": None,
                "legal_status": "lovpålagt",
            })
            classified_heading += 1
            continue
        title = (p.get("title") or p.get("native_label") or p.get("excerpt") or "").strip()
        norm_title = _normalize_title_v22(title, norm_cfg)
        if _matches_reject_if_regex(norm_title, reject_patterns):
            rejected_noise_count += 1
            if trace is not None:
                rejected_noise.append({"point_id": point_id, "normalized_title": norm_title, "reason": "rejected_noise"})
            continue
        bp = _classify_to_building_part_v22(norm_title, building_parts)
        if not bp:
            if p.get("source_primary_tg_conclusion"):
                classified_heading += 1
                canonical_point_id = (
                    _infer_canonical_child_from_text(
                        f"{title}\n{p.get('span_text') or p.get('exact_span_text') or p.get('excerpt') or ''}",
                        mapping_points_for_inference,
                    )
                    if mapping_points_for_inference
                    else ""
                )
                validated.append({
                    **dict(p),
                    "canonical_building_part_id": "BP_SOURCE_PRIMARY_TG",
                    "canonical_display_name": title,
                    "required_by_forskrift": True,
                    "ui_badge": None,
                    "legal_status": "lovpålagt",
                    "instance_label": None,
                    "canonical_point_id": canonical_point_id or "",
                })
                continue
            e3_parent = _match_e3_p11_p12_heading(norm_title)
            if e3_parent:
                classified_heading += 1
                validated.append({
                    **dict(p),
                    "canonical_building_part_id": f"BP_E3_{e3_parent}",
                    "canonical_display_name": "E3 P11/P12 heading",
                    "required_by_forskrift": True,
                    "ui_badge": None,
                    "legal_status": "lovpålagt",
                    "e3_parent_hint": e3_parent,
                })
                continue
            unclassified_count += 1
            if trace is not None:
                unclassified.append({"point_id": point_id, "normalized_title": norm_title, "reason": "unclassified_heading"})
            continue
        classified_heading += 1
        instance_label = _extract_instance_label_v22(title, norm_title, instance_regex_order)
        legal = "lovpålagt" if bp.get("required_by_forskrift", True) else "ikke lovpålagt"
        canonical_point_id = (
            _infer_canonical_child_from_text(f"{title}\n{norm_title}", mapping_points_for_inference)
            if mapping_points_for_inference
            else ""
        )
        validated.append({
            **dict(p),
            "canonical_building_part_id": bp.get("id", ""),
            "canonical_display_name": bp.get("display_name", ""),
            "required_by_forskrift": bp.get("required_by_forskrift", True),
            "ui_badge": bp.get("ui_badge"),
            "legal_status": legal,
            "instance_label": instance_label,
            "canonical_point_id": canonical_point_id or "",
        })
    if trace is not None:
        trace["rejected_noise"] = rejected_noise
        trace["rejected_noise_count"] = rejected_noise_count
        trace["unclassified_heading"] = unclassified
        trace["unclassified_heading_count"] = unclassified_count
        trace["classified_heading_count"] = classified_heading
        trace["rejected_noise_sample"] = rejected_noise[:20]
        trace["unclassified_heading_sample"] = unclassified[:20]
        trace["whitelist_rejected"] = rejected_noise + unclassified
        trace["whitelist_rejected_count"] = rejected_noise_count + unclassified_count
        trace["detected_points_total"] = len(detected)
    return validated


@lru_cache(maxsize=1)
def _get_whitelist_v21_lookup() -> Optional[Dict]:
    """Build canonical + alias lookup and compiled regexes for whitelist v2.1."""
    wl = get_building_part_whitelist_v21()
    if not wl:
        return None
    canonical_index = wl.get("canonical_index") or {}
    alias_mapping = wl.get("alias_mapping") or {}
    canonical_parts = {c.get("id"): c for c in (wl.get("canonical_building_parts") or []) if isinstance(c, dict)}
    # Build: normalized_name -> (canonical_id, legal_status)
    lookup: Dict[str, tuple] = {}
    for name, cid in canonical_index.items():
        if isinstance(name, str) and isinstance(cid, str):
            norm = name.strip().lower()
            part = canonical_parts.get(cid)
            legal = part.get("legal", "uklart/avhenger av rapporttype") if isinstance(part, dict) else "uklart/avhenger av rapporttype"
            lookup[norm] = (cid, legal)
    for alias, canonical_ref in alias_mapping.items():
        if isinstance(alias, str) and isinstance(canonical_ref, str):
            norm_alias = alias.strip().lower()
            cid = canonical_index.get(canonical_ref) or (canonical_ref if canonical_ref in canonical_parts else None)
            if cid:
                part = canonical_parts.get(cid)
                legal = part.get("legal", "uklart/avhenger av rapporttype") if isinstance(part, dict) else "uklart/avhenger av rapporttype"
                lookup[norm_alias] = (cid, legal)
    patterns = [re.compile(p) for p in (wl.get("hard_reject_regex") or []) if isinstance(p, str)]
    norm_cfg = wl.get("normalization") or {}
    canonical_names = {name.strip().lower() for name in canonical_index.keys() if isinstance(name, str)}
    return {"lookup": lookup, "patterns": patterns, "norm_cfg": norm_cfg, "canonical_names": canonical_names}


def _normalize_title_v21(title: str, norm_cfg: Dict) -> str:
    """Apply whitelist v2.1 normalization: lowercase, strip, remove trailing punctuation, collapse spaces."""
    if not title or not isinstance(title, str):
        return ""
    s = title.strip()
    if norm_cfg.get("lowercase", True):
        s = s.lower()
    if norm_cfg.get("remove_trailing_colon", True):
        s = s.rstrip(":")
    if norm_cfg.get("remove_trailing_comma", True):
        s = s.rstrip(",")
    if norm_cfg.get("collapse_multiple_spaces", True):
        s = " ".join(s.split())
    return s.strip()


def _validate_detected_points_against_whitelist(
    detected: List[Dict[str, object]],
    trace: Optional[Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    """
    Filter to only segments with valid point ID + whitelist match.
    Prefers v2.2 (normalize -> reject_if_regex -> classify -> instance extraction), falls back to v2.1 then v1.
    Hard gate: invalid segments never enter hierarchy.
    """
    if not detected:
        return detected
    validated = _validate_detected_points_against_whitelist_v22(detected, trace=trace)
    if validated is not None:
        return validated
    wl_lookup = _get_whitelist_v21_lookup()
    if not wl_lookup:
        # Fallback to v1 if v2.1 not available
        whitelist = _get_building_part_whitelist()
        validated: List[Dict[str, object]] = []
        whitelist_rejected: List[Dict[str, object]] = []
        for p in detected:
            if not isinstance(p, dict):
                continue
            point_id = p.get("point_id") or p.get("numeric_id") or p.get("native_label") or ""
            if not point_id or not _is_numeric_point_id(point_id):
                if trace is not None:
                    whitelist_rejected.append({"point_id": point_id or "(empty)", "normalized_title": _normalize_title_for_trace(p.get("title") or p.get("excerpt") or ""), "reason": "invalid_point_id"})
                continue
            title = (p.get("title") or p.get("native_label") or p.get("excerpt") or "").strip()
            if _segment_matches_building_part(title, whitelist):
                validated.append(p)
            elif p.get("segmentation_fallback"):
                validated.append(p)
            else:
                if trace is not None:
                    whitelist_rejected.append({"point_id": point_id, "normalized_title": _normalize_title_for_trace(title), "reason": "whitelist_no_match"})
        if trace is not None:
            trace["whitelist_rejected"] = whitelist_rejected
            trace["whitelist_rejected_count"] = len(whitelist_rejected)
        return validated

    lookup = wl_lookup.get("lookup") or {}
    patterns = wl_lookup.get("patterns") or []
    norm_cfg = wl_lookup.get("norm_cfg") or {}
    canonical_names = wl_lookup.get("canonical_names") or set()
    validated: List[Dict[str, object]] = []
    whitelist_rejected: List[Dict[str, object]] = []
    accepted_canonical = 0
    accepted_alias = 0
    rejected_not_in_whitelist = 0
    rejected_hard_regex = 0
    for p in detected:
        if not isinstance(p, dict):
            continue
        point_id = p.get("point_id") or p.get("numeric_id") or p.get("native_label") or ""
        if not point_id or not _is_numeric_point_id(point_id):
            if trace is not None:
                whitelist_rejected.append({"point_id": point_id or "(empty)", "normalized_title": _normalize_title_for_trace(p.get("title") or p.get("excerpt") or ""), "reason": "invalid_point_id"})
            continue
        if p.get("segmentation_fallback"):
            validated.append({
                **dict(p),
                "legal_status": "lovpålagt",
                "required_by_forskrift": True,
                "ui_badge": None,
            })
            continue
        title = (p.get("title") or p.get("native_label") or p.get("excerpt") or "").strip()
        norm_title = _normalize_title_v21(title, norm_cfg)
        for pat in patterns:
            if pat.search(norm_title):
                rejected_hard_regex += 1
                if trace is not None:
                    whitelist_rejected.append({"point_id": point_id, "normalized_title": norm_title, "reason": "hard_reject_regex"})
                break
        else:
            match = lookup.get(norm_title)
            if match:
                canonical_id, legal_status = match
                if norm_title in canonical_names:
                    accepted_canonical += 1
                else:
                    accepted_alias += 1
                validated.append({
                    **dict(p),
                    "legal_status": legal_status,
                    "canonical_building_part_id": canonical_id,
                    "required_by_forskrift": legal_status == "lovpålagt",
                    "ui_badge": "(ikke lovpålagt)" if legal_status == "ikke lovpålagt" else None,
                })
            else:
                rejected_not_in_whitelist += 1
                if trace is not None:
                    whitelist_rejected.append({"point_id": point_id, "normalized_title": norm_title, "reason": "whitelist_no_match"})
    if trace is not None:
        trace["whitelist_rejected"] = whitelist_rejected
        trace["whitelist_rejected_count"] = len(whitelist_rejected)
        trace["detected_points_total"] = len(detected)
        trace["rejected_not_in_whitelist_count"] = rejected_not_in_whitelist
        trace["rejected_hard_regex_count"] = rejected_hard_regex
        trace["accepted_canonical_count"] = accepted_canonical
        trace["accepted_alias_count"] = accepted_alias
    return validated


# Point titles that indicate non-bygningsdel content (TG1 cost, floor summary) - exclude from punkt-for-punkt oversikt
_NON_BYGNNINGSDEL_TITLE_PATTERNS = (
    "kostnadspekulasjon",  # Cost speculation - TG1 section
)


def _is_non_bygningsdel_point(point: Dict[str, object]) -> bool:
    """
    True if point title suggests it's not a bygningsdel (e.g. TG1 cost section, etg:, for ytterligere vurderinger).
    Used to exclude from points_overview - also catches stray titles in cached/legacy data.
    """
    if not isinstance(point, dict):
        return False
    title = (
        point.get("title") or point.get("excerpt") or point.get("native_label") or ""
    ).strip().lower()
    if not title:
        return False
    # Cost-related
    if "kostnadspekulasjon" in title:
        return True
    # Stray patterns (must match _STRAY_POINT_PATTERNS) - exclude etg:, for ytterligere vurderinger, etc.
    for p in _STRAY_POINT_PATTERNS:
        if p in title:
            return True
    # "etg:" as primary content (legacy/cache edge cases)
    if title.startswith("etg:"):
        return True
    # Obvious junk: standalone "ukjent", cost-only (e.g. "600 000", "50 000,-")
    if title == "ukjent":
        return True
    if re.match(r"^[\d\s.,\-]+(?:kroner|kr|,-)?\s*$", title) and len(title) < 40:
        return True
    # Meta/report text: "rapporten.", "denne rapporten er"
    if title == "rapporten." or title == "rapporten":
        return True
    if "denne rapporten er" in title:
        return True
    # Checklist-style questions (not building parts): "Er det...", "Er der...", "Finnes det..."
    if title.startswith(("er det ", "er der ", "finnes det ")):
        return True
    # Fragments: starts with comma+digit, or very short (e.g. ",2 vekt%")
    if title.startswith(",") and len(title) < 60:
        return True
    if len(title) < 6 and not any(c.isalpha() for c in title.replace(".", "")):
        return True
    return False


def _normalize_title_for_dedup(title: str) -> str:
    """Normalize title for parent-child deduplication (strip, lower, collapse whitespace)."""
    if not title or not isinstance(title, str):
        return ""
    return " ".join(str(title).strip().lower().split())


def _is_parent_of(parent_id: str, child_id: str) -> bool:
    """True if parent_id is a strict numeric prefix of child_id (e.g. '3' is parent of '3.1')."""
    if not parent_id or not child_id or not _is_numeric_point_id(parent_id) or not _is_numeric_point_id(child_id):
        return False
    if parent_id == child_id:
        return False
    return child_id.startswith(parent_id + ".")


def _compute_parent_child_same_title_skips(
    sorted_points: List[Dict[str, object]],
) -> set:
    """
    When parent and child have the same title (e.g. PUNKT 3 and 3.1 both 'Vinduer og ytterdører'),
    skip the parent so we only show the more specific subpoint once.
    Returns set of point_ids to skip.
    """
    title_to_points: Dict[str, List[Dict[str, object]]] = {}
    for point in sorted_points:
        if not isinstance(point, dict):
            continue
        if isinstance(point.get("kind"), str) and point.get("kind") not in ("point", "subpoint"):
            continue
        point_id = point.get("point_id") or point.get("numeric_id") or point.get("native_label") or ""
        title = _normalize_title_for_dedup(point.get("title") or point.get("native_label") or point_id or "")
        if not title:
            continue
        title_to_points.setdefault(title, []).append(point)
    skip_ids: set = set()
    for title, pts in title_to_points.items():
        if len(pts) < 2:
            continue
        for parent in pts:
            parent_id = parent.get("point_id") or parent.get("numeric_id") or parent.get("native_label") or ""
            for child in pts:
                if parent_id == (child.get("point_id") or child.get("numeric_id") or child.get("native_label") or ""):
                    continue
                if _is_parent_of(parent_id, child.get("point_id") or child.get("numeric_id") or child.get("native_label") or ""):
                    skip_ids.add(parent_id)
                    break
    return skip_ids


def _detect_sort_mode(points: List[Dict[str, object]]) -> str:
    if not points:
        return "DOCUMENT_ORDER"
    numeric_count = 0
    for point in points:
        numeric_id = _numeric_id_for_point(point)
        if numeric_id:
            numeric_count += 1
    ratio = numeric_count / len(points)
    return "NUMERIC" if ratio >= 0.7 else "DOCUMENT_ORDER"


def _dedupe_points(points: List[Dict[str, object]], dedupe_key: str) -> List[Dict[str, object]]:
    unique: Dict[str, Dict[str, object]] = {}
    for idx, point in enumerate(points):
        if not isinstance(point, dict):
            continue
        key = ""
        if dedupe_key == "numeric_id":
            key = _numeric_id_for_point(point)
        if not key:
            key = _point_key_for_point(point)
        if not key:
            key = f"idx-{idx}"
        if key not in unique:
            unique[key] = point
            continue
        existing = unique[key]
        if str(point.get("tg_source") or "") == "fremtind_summary":
            merged = dict(existing)
            merged.update(point)
            unique[key] = merged
            continue
        if not existing.get("tg") and point.get("tg"):
            existing["tg"] = point.get("tg")
            if point.get("tg_source"):
                existing["tg_source"] = point.get("tg_source")
        if not existing.get("anchor_text") and point.get("anchor_text"):
            existing["anchor_text"] = point.get("anchor_text")
        if not existing.get("excerpt") and point.get("excerpt"):
            existing["excerpt"] = point.get("excerpt")
        if not existing.get("page_start") and point.get("page_start"):
            existing["page_start"] = point.get("page_start")
        if not existing.get("page_end") and point.get("page_end"):
            existing["page_end"] = point.get("page_end")
    return list(unique.values())


def _sort_points(points: List[Dict[str, object]]) -> Tuple[str, str, List[Dict[str, object]]]:
    """Sort points: required_by_forskrift first, then numeric or document order."""
    mode = _detect_sort_mode(points)
    _req_first = lambda p: (0 if (isinstance(p, dict) and p.get("required_by_forskrift", True)) else 1)
    if mode == "NUMERIC":
        unique_points = _dedupe_points(points, "numeric_id")
        def _numeric_sort_key(point: Dict[str, object]) -> Tuple[int, int, List[int]]:
            numeric_id = _numeric_id_for_point(point)
            if numeric_id:
                return (_req_first(point), 0, _parse_numeric_id(numeric_id))
            return (_req_first(point), 1, [])
        sorted_points = sorted(unique_points, key=_numeric_sort_key)
        return mode, "numeric_id", sorted_points
    unique_points = _dedupe_points(points, "point_key")
    if all(isinstance(p, dict) and p.get("order_in_doc") is not None for p in unique_points):
        sorted_points = sorted(
            unique_points,
            key=lambda p: (_req_first(p), int(p.get("order_in_doc") or 0)),
        )
    else:
        sorted_points = sorted(
            unique_points,
            key=lambda p: (_req_first(p), int(p.get("page_start") or 0)),
        )
    return mode, "point_key", sorted_points


def _derive_rule_family(rule_id: str) -> str:
    if not rule_id:
        return ""
    if rule_id.startswith(("L-", "L_")):
        return "LEGALITY"
    if "." in rule_id:
        return rule_id.split(".", 1)[0]
    if "_" in rule_id:
        return rule_id.split("_", 1)[0]
    return rule_id


def _normalize_issue_severity(severity: str) -> str:
    mapping = {
        "hard_stop": "critical",
        "major": "high",
        "minor": "low",
    }
    normalized = mapping.get(severity, severity)
    if normalized not in {"info", "low", "medium", "high", "critical"}:
        return "medium"
    return normalized


def _parse_point_id_from_v16_finding(finding: Dict[str, object]) -> Optional[str]:
    """Parse point id from v1.6 finding message or evidence_snippets (e.g. 'Punkt 4.2')."""
    for key in ("point_id", "component_id"):
        value = finding.get(key)
        if isinstance(value, str):
            candidate = _normalize_point_id(value)
            if candidate and candidate.upper() != "GLOBAL" and _is_numeric_point_id(candidate):
                return candidate
    # Avoid parsing generic suffixes like "..._001" from rule/finding IDs.
    # Those are typically rule serials, not report point IDs.
    for key in ("rule_id", "finding_id"):
        value = finding.get(key)
        if isinstance(value, str):
            m = POINT_ID_IN_TEXT_RE.search(value)
            if m:
                candidate = _normalize_point_id(m.group(1))
                if candidate and _is_numeric_point_id(candidate):
                    return candidate
    message = finding.get("message") or ""
    if isinstance(message, str):
        m = POINT_ID_IN_TEXT_RE.search(message)
        if m:
            return m.group(1)
    for snip in finding.get("evidence_snippets") or []:
        if isinstance(snip, str):
            m = POINT_ID_IN_TEXT_RE.search(snip)
            if m:
                return m.group(1)
    return None


def _parse_runtime_point_ref_from_v16_finding(finding: Dict[str, object]) -> Optional[str]:
    """Return the best linked point reference from a v1.6 finding, including canonical child ids."""
    exact_value = finding.get("exact_point_id")
    if isinstance(exact_value, str):
        candidate = _normalize_point_id(exact_value)
        if candidate and candidate.upper() != "GLOBAL" and (
            _is_scoring_eligible_point_id(candidate) or _is_canonical_child_point_id(candidate)
        ):
            return candidate
    for key in ("point_id", "component_id"):
        value = finding.get(key)
        if isinstance(value, str):
            candidate = _normalize_point_id(value)
            if not candidate or candidate.upper() == "GLOBAL":
                continue
            if _is_scoring_eligible_point_id(candidate) or _is_canonical_child_point_id(candidate):
                return candidate
    numeric = _parse_point_id_from_v16_finding(finding)
    if numeric:
        candidate = _normalize_point_id(numeric)
        if _is_scoring_eligible_point_id(candidate):
            return candidate
    return None


def _attach_exact_point_sources_to_findings(
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
) -> None:
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list) or not all_findings:
        return
    lookup = _build_exact_point_source_lookup(detected_points)
    if not lookup:
        return
    analysis_output["exact_point_source_lookup"] = lookup
    for finding in all_findings:
        if not isinstance(finding, dict):
            continue
        point_id = _normalize_point_id(str(_parse_runtime_point_ref_from_v16_finding(finding) or ""))
        if not _is_scoring_eligible_point_id(point_id) or point_id not in lookup:
            continue
        source = lookup[point_id]
        finding["exact_point_id"] = point_id
        finding["exact_point_title"] = _user_visible_point_label(
            str(source.get("title") or point_id),
            source if isinstance(source, dict) else {},
        )
        finding["exact_point_text"] = source.get("text") or ""
        finding["exact_point_signals"] = dict(source.get("signals") or {})
        finding["exact_rule_id"] = str(finding.get("rule_id") or finding.get("finding_id") or "")
        if finding.get("exact_point_text"):
            finding["evidence_snippets"] = [str(finding.get("exact_point_text"))]


def _resolve_canonical_child_point_id(
    point_id: Optional[str],
    text_candidates: List[str],
    point_lookup: Dict[str, Dict[str, object]],
    mapping_points: List[Dict[str, object]],
) -> Optional[str]:
    candidate = _normalize_point_id(point_id or "")
    if candidate and _is_canonical_child_point_id(candidate):
        point_meta = point_lookup.get(candidate) or {}
        if not _point_title_is_ambiguous_for_canonical_child(point_meta, candidate, mapping_points):
            return candidate
        candidate = ""
    search_candidates: List[str] = []
    if candidate and candidate in point_lookup:
        point_meta = point_lookup.get(candidate) or {}
        for key in ("title", "heading", "native_label", "anchor_text", "excerpt", "span_text"):
            value = point_meta.get(key)
            if isinstance(value, str) and value.strip():
                search_candidates.append(value.strip())
    for text in text_candidates:
        if isinstance(text, str) and text.strip():
            search_candidates.append(text.strip())
    if candidate.startswith("9."):
        numeric_blob = _normalize_segment_title_for_canonical_match("\n".join(search_candidates + [candidate]))
        if _is_wet_room_context(numeric_blob):
            return None
        if "fuktmå" in numeric_blob:
            return "P06D_BELOW_GRADE_MOISTURE"
        if "ventilasjon" in numeric_blob:
            return "P06C_BELOW_GRADE_VENTILATION"
        if re.search(r"(?ix)\b(?:underetasje|kjeller|rom\s+under\s+terreng)\b", numeric_blob):
            if re.search(r"(?ix)\b(?:gulv|plate|dekke)\b", numeric_blob):
                return "P06B_BELOW_GRADE_FLOORS"
            return "P06A_BELOW_GRADE_WALLS"
    for text in search_candidates:
        inferred = _infer_canonical_child_from_text(text, mapping_points)
        if inferred:
            return inferred
    return candidate or None


def _infer_canonical_child_from_text(
    text: str,
    mapping_points: List[Dict[str, object]],
) -> Optional[str]:
    """
    Infer canonical child id from free-form finding/evidence text using mapping aliases/regex.
    This is used when v1.6 findings are not tied to numeric point IDs.
    """
    if not text or not isinstance(text, str):
        return None
    norm_text = _normalize_segment_title_for_canonical_match(text)
    if not norm_text:
        return None
    matched_ids: List[str] = []
    for m in mapping_points:
        if not isinstance(m, dict):
            continue
        if _segment_matches_canonical(norm_text, m, {}):
            cid = m.get("canonical_id")
            if isinstance(cid, str) and cid:
                matched_ids.append(cid)
    matched_ids = list(dict.fromkeys(matched_ids))
    if len(matched_ids) == 1:
        return matched_ids[0]
    return None


def _point_title_is_ambiguous_for_canonical_child(
    point_meta: Dict[str, object],
    child_id: str,
    mapping_points: List[Dict[str, object]],
) -> bool:
    if not isinstance(point_meta, dict) or not child_id:
        return False
    candidate_titles: List[str] = []
    for key in ("title", "heading", "anchor_text", "excerpt", "native_label"):
        value = point_meta.get(key)
        if isinstance(value, str) and value.strip():
            candidate_titles.append(value.strip())
    if not candidate_titles:
        return False
    target_parent = ""
    for mapping in mapping_points:
        if isinstance(mapping, dict) and str(mapping.get("canonical_id") or mapping.get("child_id") or "") == child_id:
            target_parent = str(mapping.get("parent_id") or "").strip()
            break
    if not target_parent:
        return False
    for title in candidate_titles:
        norm_title = _normalize_segment_title_for_canonical_match(title)
        if not norm_title:
            continue
        sibling_matches = []
        for mapping in mapping_points:
            if not isinstance(mapping, dict):
                continue
            if str(mapping.get("parent_id") or "").strip() != target_parent:
                continue
            if _segment_matches_canonical(norm_title, mapping, {}):
                sibling_id = str(mapping.get("canonical_id") or mapping.get("child_id") or "").strip()
                if sibling_id:
                    sibling_matches.append(sibling_id)
        sibling_matches = list(dict.fromkeys(sibling_matches))
        if len(sibling_matches) > 1 and child_id in sibling_matches:
            return True
    return False


def _is_tg3_related_finding(payload: Dict[str, object]) -> bool:
    if not isinstance(payload, dict):
        return False
    chunks: List[str] = []
    for key in ("rule_id", "finding_id", "title", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            chunks.append(value.lower())
    for snip in (payload.get("evidence_snippets") or []):
        if isinstance(snip, str) and snip.strip():
            chunks.append(snip.lower())
    joined = " ".join(chunks)
    return "tg3" in joined


def _is_canonical_child_point_id(point_id: str) -> bool:
    if not point_id or not isinstance(point_id, str):
        return False
    return bool(re.match(r"^P\d{2}[A-Z]_", point_id))


def _sorted_parent_cards(parent_cards: List[Dict[str, object]]) -> List[Dict[str, object]]:
    def _order_of(parent: Dict[str, object]) -> int:
        order = parent.get("display_order")
        if isinstance(order, int):
            return order
        if isinstance(order, str) and order.isdigit():
            return int(order)
        cid = str(parent.get("canonical_id") or "")
        m = re.match(r"^P(\d{2})_", cid)
        if m:
            return int(m.group(1))
        return 999

    cards = [p for p in parent_cards if isinstance(p, dict) and p.get("canonical_id")]
    return sorted(cards, key=lambda p: (_order_of(p), str(p.get("canonical_id") or "")))


@lru_cache(maxsize=1)
def _get_canonical_child_title_map() -> Dict[str, str]:
    cfg = get_canonical_points_v30()
    child_titles: Dict[str, str] = {}
    parents = cfg.get("parents") if isinstance(cfg, dict) else []
    for parent in parents or []:
        if not isinstance(parent, dict):
            continue
        for child in parent.get("children") or []:
            if not isinstance(child, dict):
                continue
            cid = str(child.get("child_id") or child.get("id") or "").strip()
            if not cid:
                continue
            title = str(child.get("title_nb") or child.get("label_nb") or "").strip()
            if title:
                child_titles[cid] = title
    return child_titles


def _resolve_child_title(child_id: str, child_meta: Dict[str, object]) -> str:
    canonical_title = _get_canonical_child_title_map().get(str(child_id or ""))
    raw_title = str((child_meta or {}).get("title") or "").strip()
    title = canonical_title or raw_title or str(child_id or "Ukjent")
    title = _strip_suspicious_cjk(title)
    title = " ".join(title.split())
    return title or str(child_id or "Ukjent")


def _user_visible_point_label(point_id: str, child_meta: Optional[Dict[str, object]] = None) -> str:
    normalized = str(point_id or "").strip()
    if not normalized:
        return ""
    if _is_canonical_child_point_id(normalized):
        return _resolve_child_title(normalized, child_meta or {})
    return normalized


def _user_visible_point_prefix(point_id: str, child_meta: Optional[Dict[str, object]] = None) -> str:
    normalized = str(point_id or "").strip()
    if not normalized:
        return ""
    if _is_scoring_eligible_point_id(normalized):
        return f"Punkt {normalized}: "
    if _is_canonical_child_point_id(normalized):
        title = _resolve_child_title(normalized, child_meta or {})
        return f"{title}: " if title else ""
    return ""


def _replace_canonical_child_ids_in_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return "" if not isinstance(text, str) else text
    updated = text
    for child_id, title in sorted(_get_canonical_child_title_map().items(), key=lambda item: len(item[0]), reverse=True):
        updated = re.sub(
            rf"(?<![A-Z0-9_]){re.escape(child_id)}(?![A-Z0-9_])",
            title,
            updated,
        )
    return updated


_USER_FACING_TEXT_KEYS = {
    "title",
    "title_display",
    "message",
    "summary",
    "details",
    "reason",
    "recommended_fix_text",
    "suggested_rewrite_text",
    "what_to_change",
    "good_example",
    "exact_point_title",
}


def _normalize_user_facing_child_titles(payload: object) -> None:
    if isinstance(payload, dict):
        rule_id = str(payload.get("rule_id") or payload.get("finding_id") or "") if isinstance(payload, dict) else ""
        finding_like_payload = any(
            key in payload
            for key in (
                "rule_id",
                "finding_id",
                "severity",
                "category",
                "deduction_band",
                "recommended_fix_text",
                "suggested_rewrite_text",
            )
        )
        for key, value in list(payload.items()):
            if key in {"point_id", "exact_point_id", "point_key"} and isinstance(value, str):
                normalized = _normalize_point_id(value)
                keep_canonical_ref = rule_id == "E_METHOD.tg3_cost_missing" and key in {"point_id", "exact_point_id"}
                if finding_like_payload and not keep_canonical_ref and (_is_report_level_rule(rule_id) or _is_canonical_child_point_id(normalized)):
                    payload[key] = ""
                    continue
            if isinstance(value, (dict, list)):
                _normalize_user_facing_child_titles(value)
            elif isinstance(value, str) and key in _USER_FACING_TEXT_KEYS:
                payload[key] = _replace_canonical_child_ids_in_text(value)
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, (dict, list)):
                _normalize_user_facing_child_titles(item)


def _normalize_report_level_finding_targets(analysis_output: Dict[str, object]) -> None:
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return
    for item in all_findings:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or item.get("finding_id") or "")
        if not _is_report_level_rule(rule_id):
            continue
        item["point_id"] = ""
        item["exact_point_id"] = ""


def _is_report_level_rule(rule_id: str) -> bool:
    rid = str(rule_id or "").strip()
    return rid in {
        "E_METHOD.areal_ns3940_2023",
        "E_METHOD.egenerklaring_missing",
        "E_METHOD.vinterhage_not_assessed_ns3600",
    }


def _public_point_reference(point_id: str, rule_id: str = "") -> str:
    normalized = _normalize_point_id(str(point_id or ""))
    if not normalized or normalized == "GLOBAL":
        return ""
    if _is_report_level_rule(rule_id):
        return ""
    if _is_canonical_child_point_id(normalized) and rule_id != "E_METHOD.tg3_cost_missing":
        return ""
    if normalized.startswith("9."):
        return normalized
    return normalized


def _can_use_all_findings_fallback(
    all_findings: List[Dict[str, object]],
    mapping_points: List[Dict[str, object]],
    allowed_point_ids: set,
) -> bool:
    """
    Tight fallback gate:
    only use all_findings path when we can link findings to concrete report segments.
    """
    if not isinstance(all_findings, list) or not all_findings:
        return False
    linked = 0
    for finding in all_findings:
        if not isinstance(finding, dict):
            continue
        point_id = _parse_runtime_point_ref_from_v16_finding(finding)
        if point_id and point_id in allowed_point_ids and point_id != "GLOBAL":
            linked += 1
            continue
        title_candidates: List[str] = []
        for key in ("exact_point_title", "title"):
            value = finding.get(key)
            if isinstance(value, str) and value.strip():
                title_candidates.append(value)
        inferred = None
        for candidate in title_candidates:
            inferred = _infer_canonical_child_from_text(candidate, mapping_points)
            if inferred:
                break
        if inferred and _is_canonical_child_point_id(inferred):
            linked += 1
    return linked > 0


def _filter_tg3_cost_missing_false_positives(
    report_text: str,
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
) -> None:
    """
    E1/E2: Remove TG3 cost findings when segment has range or cost class (PASS).
    Downgrade to MEDIUM when segment has single amount only (E2).
    Uses combined text (main + linked summary) so cost in summary is not missed.
    """
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list) or not all_findings:
        return
    segment_by_point = {}
    point_by_id: Dict[str, Dict[str, object]] = {}
    for p in detected_points:
        if isinstance(p, dict) and p.get("point_id"):
            pid = _normalize_point_id(str(p["point_id"]))
            if not _is_segment_validation_point_id(pid):
                continue
            combined = str(p.get("effective_span_text") or _get_effective_point_text(p)).strip()
            segment_by_point[pid] = combined
            point_by_id[pid] = p
    if not segment_by_point:
        return

    filtered = []
    for f in all_findings:
        if not isinstance(f, dict):
            filtered.append(f)
            continue
        rid = (f.get("finding_id") or f.get("rule_id") or "")
        if "tg3_cost" not in str(rid).lower() and "cost_missing" not in str(rid).lower():
            filtered.append(f)
            continue
        finding_text_evidence = "\n".join(
            [
                str(f.get("title") or ""),
                str(f.get("message") or ""),
                str(f.get("exact_point_text") or ""),
                "\n".join(s for s in (f.get("evidence_snippets") or []) if isinstance(s, str)),
            ]
        ).strip()
        if _tg3_cost_status(finding_text_evidence) == "pass":
            continue
        point_id = _normalize_point_id(str(_parse_runtime_point_ref_from_v16_finding(f) or ""))
        point_meta = point_by_id.get(point_id or "", {})
        if not _is_segment_validation_point_id(point_id) or point_id not in point_by_id:
            # Exact point linkage is required before surfacing a TG3 cost finding.
            continue
        segment_text = segment_by_point.get(point_id or "", "")
        point_tg = _effective_point_tg(point_meta, report_text)

        if point_id and point_tg != "TG3":
            continue

        statuses = []
        if segment_text:
            statuses.append(_tg3_cost_status(segment_text))
        status = "high"
        if "pass" in statuses:
            status = "pass"
        elif "medium" in statuses:
            status = "medium"
        elif _point_has_accepted_tg3_cost_signal(point_meta):
            status = "pass"

        if status == "pass":
            continue
        if status == "medium":
            f = dict(f)
            f["deduction_band"] = "Middels trekk"
            f["severity"] = "minor"
        filtered.append(f)
    analysis_output["all_findings"] = filtered


_TG_MISMATCH_HINTS = (
    "b_tg.inconsistent_with_text",
    "gate_tg_text_severe_mismatch",
    "klar motstrid",
    "inkonsistens mellom tg og tekst",
    "contradiction",
)
_PRACTICAL_CONSEQUENCE_HINTS = (
    "a_arkat.konsekvens_unclear",
    "gate_konsekvens_not_buyer_oriented",
    "konsekvens_unclear",
    "not buyer oriented",
    "not clearly practical",
)
_SEVERE_TEXT_TERMS = (
    "aktiv lekkasje",
    "alvorlig",
    "akutt",
    "umiddelbar",
    "fare",
    "brannfare",
    "helserisiko",
    "muggsopp",
    "råte",
    "store fuktskader",
    "bør utbedres straks",
)
_MILD_TEXT_TERMS = (
    "ingen avvik",
    "ingen symptomer",
    "normal slitasje",
    "mindre avvik",
    "svak slitasje",
    "anbefales ved behov",
    "kan vurderes",
)
_PRUDENCE_CONTEXT_TERMS = (
    "ikke krav på oppføringstidspunktet",
    "ikke krav da bygget ble oppført",
    "ikke krav ved oppføring",
    "anbefales for sikkerhet",
    "anbefales av sikkerhetsmessige årsaker",
    "føre var",
    "snøfanger",
)
_PRACTICAL_CONSEQUENCE_TERMS = (
    "helserisiko",
    "helsefare",
    "fuktskade",
    "fuktskader",
    "muggsopp",
    "sopp",
    "lekkasje",
    "følgeskade",
    "følgeskader",
    "kostbar",
    "kostbare reparasjoner",
    "utbedringsbehov",
    "videre skade",
    "sikkerhetsrisiko",
    "redusert funksjon",
    "bruksbegrensning",
    "inneklima",
)

# E3 templates often use these headings without bygningsdel classification.
# Keep them through whitelist so canonical P11/P12 matching can mark parents as FOUND.
E3_P11_P12_HEADING_RULES = (
    ("P11", re.compile(r"(?i)\blovlighet(?:\s+og\s+sikkerhet)?\b")),
    ("P12", re.compile(r"(?i)\btilleggsopplysninger\b")),
    ("P12", re.compile(r"(?i)\b(?:v[æa]r|vaer|ver)\s+oppmerksom\s+p(?:[åa]|aa)\b")),
    ("P12", re.compile(r"(?i)\banbefalte?\s+ytterligere\s+unders[øo]kelser\b")),
)


def _match_e3_p11_p12_heading(norm_title: str) -> Optional[str]:
    if not norm_title:
        return None
    for parent_id, rx in E3_P11_P12_HEADING_RULES:
        if rx.search(norm_title):
            return parent_id
    return None


def _finding_text_blob(finding: Dict[str, object]) -> str:
    parts: List[str] = []
    for key in ("finding_id", "rule_id", "title", "message", "recommended_fix_text"):
        value = finding.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    for key in ("evidence_snippets",):
        value = finding.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
    return _normalize_tg3_cost_text("\n".join(parts)).lower()


def _driver_text_blob(driver: Dict[str, object]) -> str:
    parts: List[str] = []
    for key in ("title", "reason"):
        value = driver.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    refs = driver.get("rule_refs")
    if isinstance(refs, list):
        parts.extend([str(r) for r in refs if isinstance(r, str)])
    return _normalize_tg3_cost_text("\n".join(parts)).lower()


def _is_tg_mismatch_candidate(text: str) -> bool:
    return any(h in text for h in _TG_MISMATCH_HINTS)


def _is_practical_consequence_candidate(text: str) -> bool:
    return any(h in text for h in _PRACTICAL_CONSEQUENCE_HINTS)


def _is_clear_tg_text_mismatch(segment_text: str, tg: str) -> bool:
    if not segment_text:
        return False
    text = _normalize_tg3_cost_text(segment_text).lower()
    if any(term in text for term in _PRUDENCE_CONTEXT_TERMS):
        return False
    has_severe = any(term in text for term in _SEVERE_TEXT_TERMS)
    has_mild = any(term in text for term in _MILD_TEXT_TERMS)
    tg_norm = str(tg or "").upper()
    if tg_norm in {"TG0", "TG1"} and has_severe:
        return True
    if tg_norm == "TG3" and has_mild and not has_severe:
        return True
    if tg_norm == "TG2" and ("ingen avvik" in text or "uten avvik" in text) and not has_severe:
        return True
    return False


def _has_practical_consequence_text(segment_text: str) -> bool:
    if not segment_text:
        return False
    return _point_has_buyer_oriented_consequence_text(segment_text)


def _drop_tg_and_consequence_false_positives(
    report_text: str,
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
) -> None:
    """
    Tight post-filter for two frequently over-triggered drivers:
    - TG/text contradiction
    - consequence not practical/buyer-oriented
    Keeps findings only when we can detect a clear mismatch from linked segment text.
    """
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list) or not all_findings:
        return

    linked = _extract_linked_summary_text_per_point(report_text or "")
    segment_by_point: Dict[str, str] = {}
    tg_by_point: Dict[str, str] = {}
    available_point_ids = [
        _normalize_point_id(str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or ""))
        for p in detected_points
        if isinstance(p, dict)
    ]
    for p in detected_points:
        if not isinstance(p, dict):
            continue
        point_id = str(p.get("point_id") or "").strip()
        if not point_id:
            continue
        combined = _get_effective_point_text(p, linked, available_point_ids=available_point_ids)
        if combined:
            segment_by_point[point_id] = combined
        tg_by_point[point_id] = str(p.get("tg") or "").upper()

    mapping_cfg = get_points_overview_mapping_config()
    child_mappings = mapping_cfg.get("child_mappings") if isinstance(mapping_cfg, dict) else []
    mapping_points: List[Dict[str, object]] = []
    if isinstance(child_mappings, list):
        for m in child_mappings:
            if isinstance(m, dict):
                m_copy = dict(m)
                if "child_id" in m_copy:
                    m_copy["canonical_id"] = m_copy["child_id"]
                mapping_points.append(m_copy)
    if mapping_points:
        canonical_map = _map_segments_to_canonical(
            [p for p in detected_points if isinstance(p, dict)],
            mapping_points,
        )
        for canonical_id, seg in canonical_map.items():
            if not isinstance(seg, dict):
                continue
            pid = str(seg.get("point_id") or "").strip()
            if not pid:
                continue
            seg_text = segment_by_point.get(pid)
            if seg_text:
                segment_by_point[canonical_id] = seg_text
            tg_val = tg_by_point.get(pid, "")
            if tg_val:
                tg_by_point[canonical_id] = tg_val

    filtered_findings: List[Dict[str, object]] = []
    for finding in all_findings:
        if not isinstance(finding, dict):
            filtered_findings.append(finding)
            continue
        blob = _finding_text_blob(finding)
        is_tg_candidate = _is_tg_mismatch_candidate(blob)
        is_practical_candidate = _is_practical_consequence_candidate(blob)
        if not is_tg_candidate and not is_practical_candidate:
            filtered_findings.append(finding)
            continue
        point_id = _parse_point_id_from_v16_finding(finding) or ""
        if (not point_id or point_id not in segment_by_point) and mapping_points:
            inferred = _infer_canonical_child_from_text(blob, mapping_points)
            if inferred:
                point_id = inferred
        segment_text = segment_by_point.get(point_id, "")
        if not segment_text:
            filtered_findings.append(finding)
            continue
        if is_tg_candidate:
            tg = tg_by_point.get(point_id, "")
            if not _is_clear_tg_text_mismatch(segment_text, tg):
                continue
        if is_practical_candidate:
            if _has_practical_consequence_text(segment_text):
                continue
        filtered_findings.append(finding)
    analysis_output["all_findings"] = filtered_findings

    top_score_drivers = analysis_output.get("top_score_drivers")
    if isinstance(top_score_drivers, list):
        filtered_drivers = []
        for driver in top_score_drivers:
            if not isinstance(driver, dict):
                filtered_drivers.append(driver)
                continue
            blob = _driver_text_blob(driver)
            if not (_is_tg_mismatch_candidate(blob) or _is_practical_consequence_candidate(blob)):
                filtered_drivers.append(driver)
                continue
            refs = driver.get("rule_refs") or []
            point_id = ""
            for ref in refs:
                if isinstance(ref, str):
                    m = POINT_ID_IN_TEXT_RE.search(ref)
                    if m:
                        point_id = m.group(1)
                        break
            if (not point_id or point_id not in segment_by_point) and mapping_points:
                inferred = _infer_canonical_child_from_text(blob, mapping_points)
                if inferred:
                    point_id = inferred
            segment_text = segment_by_point.get(point_id, "")
            tg = tg_by_point.get(point_id, "")
            if _is_tg_mismatch_candidate(blob):
                if segment_text and not _is_clear_tg_text_mismatch(segment_text, tg):
                    continue
            if _is_practical_consequence_candidate(blob):
                if segment_text and _has_practical_consequence_text(segment_text):
                    continue
            filtered_drivers.append(driver)
        analysis_output["top_score_drivers"] = filtered_drivers

    top_issues = analysis_output.get("top_issues")
    if isinstance(top_issues, list):
        filtered_issues: List[Dict[str, object]] = []
        for issue in top_issues:
            if not isinstance(issue, dict):
                filtered_issues.append(issue)
                continue
            blob = _finding_text_blob(issue)
            if _is_tg_mismatch_candidate(blob):
                point_id = _parse_point_id_from_v16_finding(issue) or ""
                if (not point_id or point_id not in segment_by_point) and mapping_points:
                    inferred = _infer_canonical_child_from_text(blob, mapping_points)
                    if inferred:
                        point_id = inferred
                segment_text = segment_by_point.get(point_id, "")
                tg = tg_by_point.get(point_id, "")
                if segment_text and not _is_clear_tg_text_mismatch(segment_text, tg):
                    continue
            if _is_practical_consequence_candidate(blob):
                point_id = _parse_point_id_from_v16_finding(issue) or ""
                if (not point_id or point_id not in segment_by_point) and mapping_points:
                    inferred = _infer_canonical_child_from_text(blob, mapping_points)
                    if inferred:
                        point_id = inferred
                segment_text = segment_by_point.get(point_id, "")
                if segment_text and _has_practical_consequence_text(segment_text):
                    continue
            filtered_issues.append(issue)
        analysis_output["top_issues"] = filtered_issues


def _extract_linked_summary_text_per_point(report_text: str) -> Dict[str, str]:
    """
    Extract from summary sections (e.g. Takstmannens vurdering) text blocks that are
    explicitly linked to a point (hard match: same punktnummer 6.2 / 6. in summary).
    Returns point_id -> linked summary block text (only when confidently linked).
    """
    linked: Dict[str, str] = {}
    summary_pages = _collect_summary_section_pages(report_text or "")
    if not summary_pages:
        return linked
    lines = "\n".join(summary_pages).splitlines()
    i = 0
    current_summary_tg = ""

    def _summary_section_tg(line: str) -> str:
        normalized = _normalize_tg3_cost_text(line or "").lower()
        if "takstmannens vurdering ved tg3" in normalized:
            return "TG3"
        if "takstmannens vurdering ved tg2" in normalized:
            return "TG2"
        return ""

    def _with_summary_tg(block_text: str) -> str:
        block = str(block_text or "").strip()
        if not block or not current_summary_tg:
            return block
        if re.match(rf"(?i)^\s*{re.escape(current_summary_tg)}\b", block):
            return block
        return f"{current_summary_tg} {block}".strip()

    def _tg_rank(tg: str) -> int:
        return {"TG2": 2, "TG3": 3, "TGIU": 4}.get(str(tg or "").strip().upper(), 0)

    def _merge_linked(existing: str, new_block: str) -> str:
        if not existing:
            return new_block
        existing_tg = _extract_tg_label_from_text(existing)
        new_tg = _extract_tg_label_from_text(new_block)
        if _tg_rank(new_tg) > _tg_rank(existing_tg):
            return f"{new_block}\n{existing}".strip()
        if _normalize_tg3_cost_text(new_block).lower() in _normalize_tg3_cost_text(existing).lower():
            return existing
        return f"{existing}\n{new_block}".strip()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        section_tg = _summary_section_tg(stripped)
        if section_tg:
            current_summary_tg = section_tg
            i += 1
            continue
        match = POINT_HEADER_RE.match(stripped) or POINT_HEADER_FALLBACK_RE.match(stripped)
        if match:
            raw_pid = (match.group(1) or "").strip()
            section_title = (match.group(2) or "").strip() if match.lastindex and match.lastindex >= 2 else ""
            if _looks_like_date_point_id(raw_pid) or _is_noise_point_id(raw_pid) or _looks_like_date_line(stripped):
                i += 1
                continue
            if _is_false_point_header(stripped, raw_pid, section_title):
                i += 1
                continue
            point_id = _normalize_point_id(raw_pid)
            block_lines = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                next_stripped = next_line.strip()
                if _summary_section_tg(next_stripped):
                    break
                next_match = POINT_HEADER_RE.match(next_stripped) or POINT_HEADER_FALLBACK_RE.match(next_stripped)
                if next_match:
                    next_pid = (next_match.group(1) or "").strip()
                    next_title = (next_match.group(2) or "").strip() if next_match.lastindex and next_match.lastindex >= 2 else ""
                    if next_pid and not _looks_like_date_point_id(next_pid) and not _is_false_point_header(next_stripped, next_pid, next_title):
                        break
                block_lines.append(next_line)
                j += 1
            block_text = _with_summary_tg("\n".join(block_lines).strip())
            if block_text:
                existing = linked.get(point_id, "")
                linked[point_id] = _merge_linked(existing, block_text)
            i = j
            continue
        i += 1
    # Inline fallback for summary lines that list multiple punktnummer in running text.
    current_summary_tg = ""
    for raw_line in lines:
        line = (raw_line or "").strip()
        section_tg = _summary_section_tg(line)
        if section_tg:
            current_summary_tg = section_tg
            continue
        if len(line) < 8:
            continue
        refs = [m.group(1) for m in SUMMARY_INLINE_POINT_RE.finditer(line)]
        if not refs or not _line_looks_like_summary_signal(line):
            continue
        for ref in refs:
            if _looks_like_date_point_id(ref) or _is_noise_point_id(ref):
                continue
            pid = _normalize_point_id(ref)
            line_for_link = _with_summary_tg(line)
            existing = linked.get(pid, "")
            linked[pid] = _merge_linked(existing, line_for_link)
    return linked


def _get_linked_summary_for_point(
    linked: Dict[str, str],
    point_id: str,
    available_point_ids: Optional[List[str]] = None,
) -> str:
    """
    Best-effort lookup: try exact normalized punktnummer first (10.5.1),
    then walk up the hierarchy (10.5 -> 10) so a coarse summary like "Punkt 10"
    can still support 10.1, 10.2, etc. without relying on strict numbering.
    """
    pid = _normalize_point_id(point_id or "")
    if not pid:
        return ""
    # Exact match first
    if pid in linked:
        return linked[pid]
    normalized_available = {
        _normalize_point_id(candidate)
        for candidate in (available_point_ids or [])
        if _normalize_point_id(candidate)
    }
    original_pid = pid
    # Parent chain fallback is only safe when the current point is the only detected child
    # under that parent; otherwise a coarse summary like "Punkt 10" could be attached too broadly.
    while "." in pid:
        pid = pid.rsplit(".", 1)[0]
        if pid not in linked:
            continue
        sibling_count = len(
            [
                candidate
                for candidate in normalized_available
                if candidate == original_pid or candidate.startswith(pid + ".")
            ]
        )
        if sibling_count <= 1:
            return linked[pid]
    return ""


def _merge_detected_points_with_linked_summary(
    detected_points: List[Dict[str, object]],
    report_text: str,
) -> List[Dict[str, object]]:
    """
    Pipeline join step for E3: merge main point text with TG2/TG3 summary sections by punktnummer.
    Preserves the point's own TG classification and only attaches linked summary text as
    additional context. Summary wording must never silently upgrade a TG2 point to TG3.
    """
    if not detected_points:
        return detected_points
    linked = _extract_linked_summary_text_per_point(report_text or "")
    available_point_ids = [
        _normalize_point_id(str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or ""))
        for p in detected_points
        if isinstance(p, dict)
    ]
    if not linked:
        return detected_points
    merged: List[Dict[str, object]] = []
    for p in detected_points:
        if not isinstance(p, dict):
            continue
        if p.get("source_primary_tg_conclusion"):
            merged.append(p)
            continue
        point_id = _normalize_point_id(str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or ""))
        if not point_id:
            merged.append(p)
            continue
        summary_text = _get_linked_summary_for_point(linked, point_id, available_point_ids=available_point_ids).strip()
        if not summary_text:
            merged.append(p)
            continue
        out = dict(p)
        out["linked_summary_text"] = summary_text
        out["effective_span_text"] = _get_effective_point_text(out, linked, available_point_ids=available_point_ids)
        merged.append(out)
    return merged


def _build_runtime_e3_scoring_context(
    report_text: str,
    detected_points: List[Dict[str, object]],
) -> str:
    merged_points = _normalize_runtime_scoring_signals(
        _merge_detected_points_with_linked_summary(detected_points, report_text or "")
    )
    lines: List[str] = []
    for point in merged_points:
        if not isinstance(point, dict):
            continue
        linked_summary_text = str(point.get("linked_summary_text") or "").strip()
        if not linked_summary_text:
            continue
        point_id = str(point.get("point_id") or point.get("numeric_id") or point.get("native_label") or "").strip()
        if not point_id:
            continue
        title = str(point.get("title") or "").strip()
        tg = str(point.get("tg") or "").strip()
        effective_text = _normalize_tg3_cost_text(str(point.get("effective_span_text") or _get_effective_point_text(point)))
        if len(effective_text) > 1200:
            effective_text = effective_text[:1200].rstrip() + " ..."
        signals = point.get("normalized_signals") or {}
        signal_bits = []
        for key in (
            "cost_class_present",
            "cost_interval_present",
            "tiltak_present",
            "andre_tiltak_present",
            "konsekvens_tiltak_present",
            "concrete_recommended_measure_in_free_text",
            "no_tg_hms_point",
        ):
            value = point.get(key)
            if value is None and isinstance(signals, dict):
                value = signals.get(key)
            if value is True:
                signal_bits.append(key)
        header = f"Punkt {point_id}"
        if title:
            header += f" | {title}"
        if tg:
            header += f" | {tg}"
        lines.append(header)
        if signal_bits:
            lines.append("Runtime-signaler: " + ", ".join(signal_bits))
        lines.append("Sammenslaatt runtime-kilde:")
        lines.append(effective_text)
        lines.append("")
    if not lines:
        return ""
    return (
        "===== RUNTIME E3 MERGEKONTEKST =====\n"
        "Bruk denne runtime-sammenslaaingen som primarkilde ved scoring for E3-punkter. "
        "Hovedpunkt og korrekt koblet TG2/TG3-oppsummering er allerede slaatt sammen nedenfor.\n\n"
        + "\n".join(lines).strip()
    )


def _segment_content_signals(combined_text: str) -> Dict[str, bool]:
    low = _normalize_tg3_cost_text(combined_text or "").lower()
    if not low:
        return {
            "observation_present": False,
            "risk_present": False,
            "consequence_present": False,
            "recommendation_present": False,
            "documentation_ok": False,
        }
    documentation_ok = bool(DOCUMENTATION_GOOD_ENOUGH_RE.search(low))
    cause_basis_present = bool(
        ARK_ÅRSAK_RE.search(low)
        or re.search(
            r"(?ix)\b(?:tg2|tg3)\s+vurderes\s+da\b|"
            r"\btilstandsgrad\s*[23]\s+vurderes\s+da\b|"
            r"\b(?:tg2|tg3)\s+er\s+satt\s+med\s+bakgrunn\s+i\b|"
            r"\b(?:tilstandsgrad\s*[23]|tg2|tg3)\s+gis\s+med\s+bakgrunn\s+i\b",
            low,
        )
    )
    observation_present = bool(cause_basis_present or OBSERVATION_PRESENT_RE.search(low) or documentation_ok)
    risk_present = bool(ARK_RISIKO_RE.search(low) or PRACTICAL_CONSEQUENCE_RE.search(low))
    consequence_present = bool(
        _point_has_buyer_oriented_consequence_text(low)
        or documentation_ok
    )
    recommendation_present = bool(
        ARK_TILTAK_RE.search(low)
        or RECOMMENDED_ACTION_FREE_TEXT_RE.search(low)
        or documentation_ok
    )
    return {
        "observation_present": observation_present,
        "risk_present": risk_present,
        "consequence_present": consequence_present,
        "recommendation_present": recommendation_present,
        "documentation_ok": documentation_ok,
    }


def _detect_ns_standard_version(report_text: str) -> str:
    if not report_text:
        return ""
    pages = _split_pages(report_text)
    header_text = "\n".join((page.get("text") or "") for page in pages[:4]).strip()
    search_blob = _normalize_tg3_cost_text(header_text or report_text[:50000])
    if not search_blob:
        return ""
    low = search_blob.lower()
    # Tolerant to PDF glyph noise between "3600" and year (colon/ligature substitution).
    m = re.search(r"(?i)ns\s*3600\D{0,10}(2018|2025)\b", low)
    if m:
        return m.group(1)
    m = re.search(r"(?i)ns.{0,16}3600.{0,20}(2018|2025)\b", low)
    if m:
        return m.group(1)
    # OCR-compacted fallback: keep only alnum and detect "ns36002018"/"ns36002025".
    compact = re.sub(r"[^a-z0-9]", "", low)
    m = re.search(r"ns3600(?:standard)?(2018|2025)\b", compact)
    if m:
        return m.group(1)
    return ""


def _detect_ns_version(report_text: str) -> str:
    version = _detect_ns_standard_version(report_text)
    return f"NS 3600:{version}" if version else ""


def _parse_report_date_token(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    normalized = token.replace("/", "-").replace(".", "-")
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _detect_report_date(report_text: str) -> str:
    if not report_text:
        return ""
    pages = _split_pages(report_text)
    header_text = "\n".join((page.get("text") or "") for page in pages[:4]).strip()
    search_text = _normalize_tg3_cost_text(header_text or report_text[:8000])
    if not search_text:
        return ""
    labeled_match = REPORT_DATE_LABEL_RE.search(search_text)
    if labeled_match:
        parsed = _parse_report_date_token(labeled_match.group(1))
        if parsed:
            return parsed
    for match in GENERIC_DATE_RE.finditer(search_text):
        parsed = _parse_report_date_token(match.group(1))
        if parsed:
            return parsed
    return ""


def _detect_report_date_from_document_identity(document_title: Optional[str], document_id: Optional[str]) -> str:
    identity = " ".join(str(value or "") for value in (document_title, document_id))
    if not identity:
        return ""
    match = re.search(r"(?<!\d)(\d{1,2})[-_.](\d{1,2})[-_.](\d{2}|\d{4})(?!\d)", identity)
    if not match:
        return ""
    day, month, year = match.groups()
    if len(year) == 2:
        year = f"20{year}"
    return _parse_report_date_token(f"{int(day):02d}-{int(month):02d}-{year}")


def _iso_date_at_or_after(report_date: str, threshold: str) -> bool:
    if not report_date:
        return False
    return report_date >= threshold


def _detect_report_regime(report_date: str, ns_version: str) -> str:
    if not report_date:
        return "UNKNOWN"
    if report_date < "2025-12-17":
        return "PRE_2025"
    if report_date < "2026-01-01":
        return "TRANSITION_DEC_2025"
    if report_date < "2026-07-01":
        return "TRANSITION_2026"
    if ns_version and ns_version != "NS 3600:2025":
        return "TRANSITION_2026"
    return "FULL_2026"


def _extract_report_regime_context(report_text: str) -> Dict[str, str]:
    report_date = _detect_report_date(report_text)
    ns_version = _detect_ns_version(report_text)
    return {
        "report_date": report_date,
        "ns_version": ns_version,
        "report_regime": _detect_report_regime(report_date, ns_version),
    }


def _report_requires_areal_ns3940_2023(report_text: str) -> bool:
    normalized = _normalize_tg3_cost_text(report_text or "")
    if not normalized:
        return False
    low = normalized.lower()
    if BRA_BREAKDOWN_RE.search(low):
        return False
    if re.search(r"(?ix)ns\s*3940\s*[-:]?\s*2023", low) and BRA_BREAKDOWN_RE.search(low):
        return False
    if re.search(r"(?ix)\b(?:p-rom|s-rom)\b", low):
        return True
    if OLD_AREAL_METHOD_RE.search(low):
        return False
    return bool(re.search(r"(?ix)\b(?:areal|ns\s*3940|bruksareal|bra)\b", low))


def _freestanding_structure_kind(text: str) -> str:
    low = _normalize_tg3_cost_text(text or "").lower()
    if not low:
        return ""
    if HABITABLE_ANNEX_RE.search(low):
        return "habitable_annex"
    if re.search(r"(?ix)\b(?:garasje|garage|frittstående\s+garasje)\b", low):
        return "garage"
    if re.search(r"(?ix)\b(?:uthus|bod|naust|anneks)\b", low):
        return "storage"
    return ""


def _freestanding_present_arkat_keys(
    combined_text: str,
    point_title: str = "",
    standard_version: str = "",
) -> set:
    present = set()
    source_text = str(combined_text or "").strip()
    if not source_text:
        return present
    signals = _segment_content_signals(source_text)
    if _segment_has_qualifying_cause(
        source_text,
        standard_version=standard_version,
        point_title=point_title,
    ):
        present.add("årsak")
    if _point_has_qualifying_risk_text(source_text):
        present.add("risiko")
    if _point_has_buyer_oriented_consequence_text(source_text):
        present.add("konsekvens")
    if signals["recommendation_present"] or signals["documentation_ok"]:
        present.add("anbefalt_tiltak")
    return present


def _point_is_freestanding_building_without_tg(point: Dict[str, object], report_text: Optional[str] = None) -> bool:
    if not isinstance(point, dict):
        return False
    title = str(point.get("title") or point.get("excerpt") or "")
    text = str(point.get("effective_span_text") or point.get("exact_span_text") or point.get("span_text") or "")
    combined = _normalize_tg3_cost_text(f"{title}\n{text}").lower()
    if not combined or not FREESTANDING_BUILDING_RE.search(combined):
        return False
    if not DEVIATION_KEYWORD_RE.search(combined):
        return False
    tg_label = _effective_point_tg(point, report_text)
    return tg_label not in {"TG0", "TG1", "TG2", "TG3", "TGIU"}


def _apply_regime_to_detected_points(
    report_text: str,
    detected_points: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    context = _extract_report_regime_context(report_text)
    report_date = context.get("report_date") or ""
    ns_version = context.get("ns_version") or ""
    standard_version = _detect_ns_standard_version(report_text)
    out: List[Dict[str, object]] = []
    for point in detected_points:
        if not isinstance(point, dict):
            out.append(point)
            continue
        item = dict(point)
        point_id = _normalize_point_id(str(item.get("point_id") or item.get("canonical_point_id") or ""))
        title = str(item.get("title") or item.get("excerpt") or "")
        text = str(item.get("effective_span_text") or item.get("exact_span_text") or item.get("span_text") or "")
        combined = _normalize_tg3_cost_text(f"{title}\n{text}").lower()
        regime_topic_text = combined
        if _is_canonical_child_point_id(point_id):
            regime_topic_text = _normalize_tg3_cost_text(f"{point_id}\n{title}").lower()
        no_tg_hms_point = False if _is_canonical_child_point_id(point_id) else bool(item.get("no_tg_hms_point"))
        if _iso_date_at_or_after(report_date, "2026-01-01") and ("elektr" in regime_topic_text or "sikringsskap" in regime_topic_text):
            no_tg_hms_point = True
        if ns_version == "NS 3600:2025" and RAILINGS_TOPIC_RE.search(regime_topic_text):
            no_tg_hms_point = True
        item["no_tg_hms_point"] = no_tg_hms_point
        freestanding_signal = _point_is_freestanding_building_without_tg(item, report_text)
        item["freestanding_building_deviation_without_tg"] = freestanding_signal
        structure_kind = _freestanding_structure_kind(f"{title}\n{text}")
        tg_label = _effective_point_tg(item, report_text)
        present_arkat_keys = _freestanding_present_arkat_keys(
            text,
            point_title=title,
            standard_version=standard_version,
        )
        missing_arkat = sorted(
            key
            for key in ("årsak", "risiko", "konsekvens", "anbefalt_tiltak")
            if key not in present_arkat_keys
        )
        has_deviation = bool(DEVIATION_KEYWORD_RE.search(combined))
        tg_is_used = tg_label in {"TG0", "TG1", "TG2", "TG3"}
        habitable_annex_without_tg = structure_kind == "habitable_annex" and not tg_is_used and tg_label != "TGIU"
        garage_arkat_missing = (
            structure_kind in {"garage", "storage"}
            and has_deviation
            and not tg_is_used
            and tg_label != "TGIU"
            and bool(missing_arkat)
        )
        garage_tg_arkat_missing = (
            structure_kind in {"garage", "storage"}
            and has_deviation
            and tg_is_used
            and bool(missing_arkat)
        )
        item["freestanding_structure_kind"] = structure_kind
        item["freestanding_missing_arkat_keys"] = missing_arkat
        item["garage_avvik_uten_arkat"] = garage_arkat_missing
        item["garage_tg_uten_full_arkat"] = garage_tg_arkat_missing
        item["habitable_annex_without_tg"] = habitable_annex_without_tg
        normalized_signals = dict(item.get("normalized_signals") or {})
        normalized_signals["freestanding_building_deviation_without_tg"] = freestanding_signal
        normalized_signals["garage_avvik_uten_arkat"] = garage_arkat_missing
        normalized_signals["garage_tg_uten_full_arkat"] = garage_tg_arkat_missing
        normalized_signals["habitable_annex_without_tg"] = habitable_annex_without_tg
        item["normalized_signals"] = normalized_signals
        exact_point_signals = dict(item.get("exact_point_signals") or normalized_signals)
        exact_point_signals["freestanding_building_deviation_without_tg"] = freestanding_signal
        exact_point_signals["garage_avvik_uten_arkat"] = garage_arkat_missing
        exact_point_signals["garage_tg_uten_full_arkat"] = garage_tg_arkat_missing
        exact_point_signals["habitable_annex_without_tg"] = habitable_annex_without_tg
        item["exact_point_signals"] = exact_point_signals
        out.append(item)
    return out


_NS_2025_APPENDIX_C_COMPONENT_PATTERNS = (
    re.compile(r"(?ix)\bdrener"),
    re.compile(r"(?ix)\bundertak\b"),
    re.compile(r"(?ix)\bmembran"),
    re.compile(r"(?ix)\bvarmtvannsbereder\b|\bbereder\b"),
    re.compile(r"(?ix)\bvarmepumpe\b|\bvarmesentral"),
    re.compile(r"(?ix)\bventilasj"),
)


def _point_allows_age_only_under_ns2025(point_title: str, segment_text: str) -> bool:
    blob = _normalize_tg3_cost_text(f"{point_title or ''}\n{segment_text or ''}").lower()
    if not blob:
        return False
    return any(pattern.search(blob) for pattern in _NS_2025_APPENDIX_C_COMPONENT_PATTERNS)


def _segment_relies_on_age_only_reason(segment_text: str) -> bool:
    if not segment_text:
        return False
    cause_text = _extract_arkat_section_text(segment_text, "årsak") or segment_text
    normalized = _normalize_tg3_cost_text(cause_text).lower()
    age_terms = (
        "alder",
        "levetid",
        "brukstid",
        "forventet levetid",
        "forventet brukstid",
        "halvparten av sin forventede levetid",
        "mer enn halvparten av forventet levetid",
        "passert halvparten av sin forventede levetid",
        "oppbrukt levetid",
        "kort gjenværende brukstid",
    )
    if not any(term in normalized for term in age_terms):
        return False
    return not _segment_has_concrete_non_age_support(cause_text)


def _segment_has_qualifying_cause(
    segment_text: str,
    standard_version: str = "",
    point_title: str = "",
) -> bool:
    has_observation = bool(_segment_content_signals(segment_text).get("observation_present"))
    has_concrete_non_age_support = _segment_has_concrete_non_age_support(segment_text)
    age_only_cause = _segment_relies_on_age_only_reason(segment_text)
    hot_water_relief = _point_allows_age_based_risk_relief(
        point_title,
        segment_text,
        standard_version=standard_version,
    )
    if standard_version == "2018":
        if age_only_cause and hot_water_relief:
            return True
        if age_only_cause:
            return False
        return has_concrete_non_age_support or _point_has_explicit_section_text(segment_text, "årsak")
    if standard_version == "2025" and age_only_cause and not _point_allows_age_only_under_ns2025(point_title, segment_text):
        return False
    return has_observation or has_concrete_non_age_support or _point_has_explicit_section_text(segment_text, "årsak")


def _segment_present_keys(
    combined_text: str,
    tg: str,
    standard_version: str = "",
    point_title: str = "",
) -> set:
    present = set()
    low = (combined_text or "").lower()
    if not low:
        return present
    signals = _segment_content_signals(combined_text)
    if "TG2" in str(tg or "").upper() or "TG3" in str(tg or "").upper():
        if _segment_has_qualifying_cause(
            combined_text,
            standard_version=standard_version,
            point_title=point_title,
        ):
            present.add("årsak")
        if _point_has_qualifying_risk_text(combined_text) or _point_allows_age_based_risk_relief(
            point_title,
            combined_text,
            standard_version=standard_version,
        ):
            present.add("risiko")
        # Konsekvens must be buyer-oriented; do not accept generic TG3 "impact" heuristics alone.
        if _point_has_buyer_oriented_consequence_text(combined_text):
            present.add("konsekvens")
    if "TG2" in str(tg or "").upper() or "TG3" in str(tg or "").upper():
        if signals["recommendation_present"] or signals["documentation_ok"]:
            present.add("anbefalt_tiltak")
    if "TG3" in str(tg or "").upper():
        cost_status = _tg3_cost_status(combined_text)
        if cost_status == "pass":
            present.add("kostnad")
        elif cost_status == "medium":
            present.add("kostnad_single_only")
    return present


def _required_segment_keys_for_tg(tg: str) -> Tuple[str, ...]:
    tg_upper = str(tg or "").upper()
    if "TG3" in tg_upper:
        return ("årsak", "risiko", "konsekvens", "anbefalt_tiltak")
    if "TG2" in tg_upper:
        return ("årsak", "risiko", "konsekvens")
    return ()


def _segment_present_keys_from_sources(
    exact_text: str,
    linked_text: str,
    tg: str,
    standard_version: str = "",
    point_title: str = "",
    combined_text: str = "",
) -> set:
    present = set()

    def _explicit_keys(text: str, tg_value: str) -> set:
        explicit = set()
        if not str(text or "").strip():
            return explicit
        if _point_has_explicit_section_text(text, "årsak"):
            explicit.add("årsak")
        if _point_has_explicit_section_text(text, "risiko"):
            explicit.add("risiko")
        if _point_has_explicit_section_text(text, "konsekvens") and _point_has_buyer_oriented_consequence_text(text):
            explicit.add("konsekvens")
        if "TG2" in str(tg_value or "").upper() or "TG3" in str(tg_value or "").upper():
            if _point_has_explicit_section_text(text, "anbefalt_tiltak") or _point_has_explicit_section_text(text, "tiltak"):
                explicit.add("anbefalt_tiltak")
        return explicit

    for source_text in (exact_text, linked_text):
        if not str(source_text or "").strip():
            continue
        present.update(
            _segment_present_keys(
                str(source_text),
                tg,
                standard_version=standard_version,
                point_title=point_title,
            )
        )
        present.update(_explicit_keys(str(source_text), tg))
    # Fallback only when the merged point text itself clearly carries explicit ARKAT labels.
    # This protects point-level sourcing while avoiding regressions where detailed subpoint
    # text is present in the merged span but was not preserved as exact_text.
    if combined_text and re.search(
        r"(?i)\b(?:årsak|arsak|risiko|konsekvens|anbefalt(?:e)?\s+tiltak|tiltak)\s*:",
        str(combined_text),
    ):
        present.update(
            _segment_present_keys(
                str(combined_text),
                tg,
                standard_version=standard_version,
                point_title=point_title,
            )
        )
        present.update(_explicit_keys(str(combined_text), tg))
    return present

def _point_allows_age_based_risk_relief(
    point_title: str,
    segment_text: str,
    standard_version: str = "",
) -> bool:
    blob = _normalize_tg3_cost_text(f"{point_title or ''}\n{segment_text or ''}").lower()
    if not blob:
        return False
    is_hot_water_heater = bool(re.search(r"(?ix)\bvarmtvannsbereder\b|\bbereder\b", blob))
    is_heat_pump = bool(re.search(r"(?ix)\bvarmepumpe\b|\bvarmesentral", blob))
    if not is_hot_water_heater and not is_heat_pump:
        return False
    age_rule_markers = (
        "passert forventet levetid",
        "passert halvparten av sin forventede levetid",
        "halvparten av sin forventede levetid",
        "halvparten av forventet levetid",
        "som følge av alder",
        "alder på varmepumpe",
        "bereder over 20 ar",
        "bereder over 20 år",
        "over 20 ar",
        "over 20 år",
        "oppnadd alder",
        "oppnådd alder",
    )
    return any(marker in blob for marker in age_rule_markers)


def _point_has_explicit_section_text(text: str, section: str) -> bool:
    return bool(_extract_arkat_section_text(text, section).strip())


def _finding_targets_missing_key(finding: Dict[str, object]) -> Optional[str]:
    blob = _normalize_tg3_cost_text(
        f"{finding.get('finding_id', '')} {finding.get('rule_id', '')} {finding.get('title', '')} {finding.get('message', '')}"
    ).lower()
    mapping = (
        ("kostnadsklasse", "kostnad"),
        ("sjablongmessig kostnadsanslag", "kostnad"),
        ("sjablonmessig kostnadsanslag", "kostnad"),
        ("kostnadsanslag", "kostnad"),
        ("mangler kostnad", "kostnad"),
        ("cost_missing", "kostnad"),
        ("kostnad_single_only", "kostnad_single_only"),
        ("ett beløp", "kostnad_single_only"),
        ("årsak", "årsak"),
        ("risiko", "risiko"),
        ("konsekvens", "konsekvens"),
        ("anbefalt tiltak", "anbefalt_tiltak"),
        ("tiltak_missing", "anbefalt_tiltak"),
    )
    for marker, key in mapping:
        if marker in blob:
            return key
    if "arkat" in blob:
        return "arkat"
    return None


# Per-missing-type explained suggestions for SEGMENT_ARKAT (so each finding gets a distinct, helpful recommendation).
_SEGMENT_ARKAT_FIX_BY_KEY = {
    "årsak": (
        "årsak (hva som er feil / hvorfor forholdet har oppstått)",
        "Skriv kort hva som er galt og hvorfor forholdet oppstod, slik at kjøper forstår bakgrunnen.",
    ),
    "risiko": (
        "risiko (hva som kan skje videre)",
        "Beskriv kort hva som kan skje dersom forholdet ikke håndteres, slik at kjøper kan vurdere alvorlighetsgrad.",
    ),
    "konsekvens": (
        "konkret konsekvens, enten bygningsteknisk eller praktisk for kjøper",
        "Presiser konkret konsekvens, enten bygningsteknisk skade/risiko eller praktisk betydning for kjøper.",
    ),
    "anbefalt_tiltak": (
        "anbefalt(e) tiltak",
        "Formuler anbefalingen som et tydelig tiltak kjøper kan iverksette (f.eks. «Det anbefales å …» eller «Bør utføres av fagperson»).",
    ),
    "kostnad": (
        "kostnad / kostnadsklasse for TG3 (E1 – mangler helt)",
        "Angi enten et kostnadsintervall (f.eks. 200 000–500 000 kr), en kostnadsklasse (lav/middels/høy), eller et kostnadsestimat, slik at kjøper kan vurdere økonomisk belastning.",
    ),
    "kostnad_single_only": (
        "TG3 har kun ett beløp – bruk intervall eller kostnadsklasse (E2)",
        "Erstatt ett enkelt beløp med et intervall eller en kostnadsklasse (lav/middels/høy) for bedre brukervurdering.",
    ),
}


def _build_segment_arkat_recommended_fix(
    point_label: str,
    segment_title: str,
    tg: str,
    missing_keys: List[str],
    friendly_names: Dict[str, str],
) -> str:
    """
    Build a helpful, explained recommended_fix_text for one SEGMENT_ARKAT finding,
    so each finding gets a distinct suggestion based on what is missing.
    """
    intro = f"For punkt {point_label} ({segment_title or point_label})"
    if tg:
        intro += f" (vurdert som {tg})"
    intro += " mangler følgende. Slik retter du:"
    lines = [intro]
    for k in missing_keys:
        pair = _SEGMENT_ARKAT_FIX_BY_KEY.get(k)
        if pair:
            label, suggestion = pair
            lines.append(f"• {label}: {suggestion}")
        else:
            label = friendly_names.get(k, k)
            lines.append(f"• {label}: Legg inn kort og tydelig tekst i punktet eller i en oppsummering merket med samme punktnummer.")
    lines.append(
        f"Teksten kan stå i selve punktet for {point_label} eller i en tydelig koblet oppsummering med samme punktnummer."
    )
    return " ".join(lines)


def _segment_arkat_observation_hint(combined_text: str, segment_title: str) -> str:
    text = _normalize_tg3_cost_text(combined_text or "")
    if not text:
        return ""
    for sentence in re.split(r"(?<=[\.\!\?])\s+", text):
        candidate = sentence.strip().strip("\"'")
        low = candidate.lower()
        if len(candidate) < 20:
            continue
        if low.startswith("punkt "):
            continue
        if "mangler " in low or "validering gjores per punkt" in low or "validering gjøres per punkt" in low:
            continue
        if segment_title and segment_title.lower() in low and len(candidate) < 28:
            continue
        return candidate.rstrip(".")
    return ""


def _build_segment_arkat_suggested_rewrite_text(
    point_label: str,
    segment_title: str,
    tg: str,
    missing_keys: List[str],
    combined_text: str,
) -> str:
    title = str(segment_title or "").strip()
    part = title if title and title.lower() not in {"ukjent", point_label.lower()} else f"punkt {point_label}"
    hint = _segment_arkat_observation_hint(combined_text, title)
    missing_set = set(missing_keys or [])
    tg_label = str(tg or "").strip().upper()

    if "årsak" in missing_set or "arsak" in missing_set:
        if hint:
            return f"Punkt {point_label}: {part}: TG2 er satt med bakgrunn i at {hint[:1].lower() + hint[1:]}."
        return f"Punkt {point_label}: {part}: TG2 er satt med bakgrunn i registrerte avvik ved {part.lower()} som bør beskrives tydeligere."
    if "risiko" in missing_set:
        if hint:
            return f"Punkt {point_label}: {part}: Dersom forholdet ikke følges opp, er det risiko for videre utvikling fordi {hint[:1].lower() + hint[1:]}."
        return f"Punkt {point_label}: {part}: Dersom forholdet ikke følges opp, er det risiko for videre skade eller økte vedlikeholdskostnader."
    if "konsekvens" in missing_set:
        if hint:
            return f"Punkt {point_label}: {part}: Kjøper må påregne oppfølging og mulig utbedring fordi {hint[:1].lower() + hint[1:]}."
        return f"Punkt {point_label}: {part}: Kjøper må påregne oppfølging og mulig utbedring dersom forholdet utvikler seg videre."
    if "anbefalt_tiltak" in missing_set:
        return f"Punkt {point_label}: {part}: Det anbefales å undersøke og utbedre forholdet nærmere av kvalifisert fagperson."
    if "kostnad" in missing_set:
        return f"Punkt {point_label}: {part}: Oppgi kostnadsklasse eller kostnadsintervall for nødvendig oppfølging slik at økonomisk omfang blir tydelig."
    if "kostnad_single_only" in missing_set:
        return f"Punkt {point_label}: {part}: Erstatt enkeltbeløpet med et kostnadsintervall eller en kostnadsklasse for dette punktet."
    if tg_label == "TG3":
        return f"Punkt {point_label}: {part}: Beskriv forholdet med full ARKAT og et sjablongmessig kostnadsanslag."
    return f"Punkt {point_label}: {part}: Beskriv forholdet mer konkret med årsak, risiko og konsekvens knyttet til dette punktet."


def _run_ark_arkat_per_segment_validation(
    report_text: str,
    detected_points: List[Dict[str, object]],
    analysis_output: Dict[str, object],
) -> None:
    """
    Validate ARK/ARKAT per segment (not globally). Use segment main text + linked
    summary text (hard match by punktnummer). If any TG2/TG3 segment fails, cap score
    below 100 and add structural findings.
    """
    linked_summary = _extract_linked_summary_text_per_point(report_text)
    standard_version = _detect_ns_standard_version(report_text)
    semantic_points = _semantic_arkat_points_by_id(analysis_output)
    merged_by_id: Dict[str, Dict[str, object]] = {}
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or point.get("native_label") or "").strip())
        if not _is_segment_validation_point_id(point_id):
            continue
        tg = _effective_point_tg(point, report_text)
        exact_text = str(point.get("exact_span_text") or _get_exact_point_text(point)).strip()
        linked_text = str(point.get("linked_summary_text") or "").strip()
        main_text = str(point.get("effective_span_text") or "").strip()
        if not main_text:
            main_text = _get_effective_point_text(point, linked_summary, available_point_ids=[point_id]).strip()
        title = point.get("title") or point_id
        no_tg_hms_point = bool(point.get("no_tg_hms_point")) or _is_no_tg_hms_point(str(title), main_text)
        if point_id not in merged_by_id:
            merged_by_id[point_id] = {
                "point_id": point_id,
                "tg": tg,
                "title": title,
                "combined_text": main_text,
                "linked_summary_text": linked_text,
                "no_tg_hms_point": no_tg_hms_point,
                "cost_interval_present": bool(point.get("cost_interval_present")),
                "cost_class_present": bool(point.get("cost_class_present")),
                "other_schematic_cost_estimate_present": bool(point.get("other_schematic_cost_estimate_present")),
                "report_uses_cost_class_as_schematic_model": bool(point.get("report_uses_cost_class_as_schematic_model")),
                "exact_point_text": exact_text,
                "exact_point_signals": dict(point.get("exact_point_signals") or point.get("normalized_signals") or {}),
                "summary_tg_source": str(point.get("tg_source") or "") == "fremtind_summary",
            }
        else:
            existing = merged_by_id[point_id]
            if main_text and main_text not in str(existing.get("combined_text") or ""):
                existing["combined_text"] = ((existing.get("combined_text") or "") + "\n" + main_text).strip()
            if linked_text and linked_text not in str(existing.get("linked_summary_text") or ""):
                existing["linked_summary_text"] = ((existing.get("linked_summary_text") or "") + "\n" + linked_text).strip()
            if exact_text and not existing.get("exact_point_text"):
                existing["exact_point_text"] = exact_text
            existing["tg"] = _merge_point_tg(existing.get("tg"), tg)
            if str(point.get("tg_source") or "") == "fremtind_summary":
                existing["summary_tg_source"] = True
            if no_tg_hms_point:
                existing["no_tg_hms_point"] = True
            for signal_key in (
                "cost_interval_present",
                "cost_class_present",
                "other_schematic_cost_estimate_present",
                "report_uses_cost_class_as_schematic_model",
            ):
                if bool(point.get(signal_key)):
                    existing[signal_key] = True
    failed_segments = []
    segment_validation = []
    available_point_ids = list(merged_by_id.keys())
    for point_id, point in merged_by_id.items():
        if any(_is_parent_of(point_id, candidate_id) for candidate_id in available_point_ids if candidate_id != point_id):
            segment_validation.append({"point_id": point_id, "tg": str(point.get("tg") or "").strip().upper(), "passed": True, "missing": [], "mode": "PARENT_CONTAINER"})
            continue
        tg = (point.get("tg") or "").strip().upper()
        if bool(point.get("no_tg_hms_point")):
            segment_validation.append({"point_id": point_id, "tg": tg, "passed": True, "missing": [], "mode": "NO_TG_HMS"})
            continue
        if "TG2" not in tg and "TG3" not in tg:
            segment_validation.append({"point_id": point_id, "tg": tg, "passed": True, "missing": []})
            continue
        main_combined = str(point.get("combined_text") or "").strip()
        exact_text = str(point.get("exact_point_text") or "").strip()
        linked_text = str(point.get("linked_summary_text") or "").strip()
        combined = "\n".join(part for part in (main_combined, linked_text) if part).strip()
        local_tg = _extract_local_tg_for_point_id(
            point_id,
            "\n".join(part for part in (exact_text, combined, report_text if bool(point.get("summary_tg_source")) else "") if part),
        )
        if local_tg in {"TG2", "TG3", "TGIU"} and local_tg != tg:
            tg = local_tg
            point["tg"] = local_tg
        semantic_point = semantic_points.get(point_id)
        if semantic_point:
            present_keys, missing = _semantic_arkat_present_and_missing_keys(semantic_point, tg)
            semantic_raw_text = str(semantic_point.get("raw_point_text") or "").strip()
            if semantic_raw_text:
                combined = semantic_raw_text
                exact_text = semantic_raw_text
        else:
            present_keys = _segment_present_keys_from_sources(
                exact_text,
                linked_text,
                tg,
                standard_version=standard_version,
                point_title=str(point.get("title") or point_id),
                combined_text=combined,
            )
            missing = []
            for key in _required_segment_keys_for_tg(tg):
                if key not in present_keys:
                    missing.append(key)
        if "TG3" in tg:
            cost_status = _tg3_cost_status(combined)
            if cost_status == "high":
                missing.append("kostnad")
            elif cost_status == "medium":
                missing.append("kostnad_single_only")
        passed = len(missing) == 0
        if "TG3" in tg and missing and _point_has_accepted_tg3_cost_signal(point):
            missing = [m for m in missing if m not in ("kostnad", "kostnad_single_only")]
            passed = len(missing) == 0
        segment_validation.append({
            "point_id": point_id,
            "tg": tg,
            "passed": passed,
            "missing": missing,
            "combined_text": combined,
            "exact_point_text": exact_text,
            "exact_point_title": str(point.get("title") or point_id),
            "exact_point_signals": dict(point.get("exact_point_signals") or {}),
            "present_keys": sorted(present_keys),
        })
        if not passed:
            failed_segments.append({
                "point_id": point_id,
                "tg": tg,
                "title": point.get("title") or point_id,
                "missing": missing,
                "exact_point_text": exact_text,
                "exact_point_signals": dict(point.get("exact_point_signals") or {}),
            })
    analysis_output["segment_validation"] = segment_validation
    if not failed_segments:
        return
    current_score = analysis_output.get("score_total") or analysis_output.get("trygghetsscore")
    if isinstance(current_score, (int, float)) and current_score >= 100:
        capped = 99
        analysis_output["score_total"] = capped
        if "trygghetsscore" in analysis_output:
            analysis_output["trygghetsscore"] = capped
    elif isinstance(current_score, (int, float)) and current_score > 99:
        capped = min(99, int(current_score))
        analysis_output["score_total"] = capped
        if "trygghetsscore" in analysis_output:
            analysis_output["trygghetsscore"] = capped
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        all_findings = []
        analysis_output["all_findings"] = all_findings
    for seg in failed_segments:
        missing_keys = list(seg.get("missing") or [])
        cost_missing_keys = [key for key in missing_keys if key in {"kostnad", "kostnad_single_only"}]
        arkat_missing_keys = [key for key in missing_keys if key not in {"kostnad", "kostnad_single_only"}]
        if "TG3" in str(seg.get("tg") or "").upper() and cost_missing_keys:
            _ensure_tg3_missing_cost_compliance_finding(
                all_findings,
                point_id=str(seg.get("point_id") or ""),
                title=str(seg.get("title") or ""),
                evidence_text=str(seg.get("exact_point_text") or ""),
                missing_key="kostnad_single_only" if "kostnad_single_only" in cost_missing_keys and "kostnad" not in cost_missing_keys else "kostnad",
            )
        if not arkat_missing_keys:
            continue
        missing_keys = arkat_missing_keys
        friendly_names = {
            "årsak": "årsak (hva som er feil / hvorfor forholdet har oppstått)",
            "risiko": "risiko (hva som kan skje videre)",
            "konsekvens": "konsekvens for kjøper (bruk/sikkerhet/økonomi/videre skade)",
            "anbefalt_tiltak": "anbefalt(e) tiltak",
            "kostnad": "kostnad / kostnadsklasse for TG3 (E1 – mangler helt)",
            "kostnad_single_only": "TG3 har kun ett beløp – bruk intervall eller kostnadsklasse (E2)",
        }
        missing_readable = [friendly_names.get(k, k) for k in missing_keys]
        missing_str = ", ".join(missing_readable)
        point_label = seg.get("point_id", "")
        title = seg.get("title", "")
        is_e2_only = "kostnad_single_only" in missing_keys and "kostnad" not in missing_keys
        deduction_band = "Middels trekk" if is_e2_only else "Høyt trekk"
        severity = "minor" if is_e2_only else "major"
        recommended_fix = _build_segment_arkat_recommended_fix(
            point_label=point_label,
            segment_title=title,
            tg=seg.get("tg", ""),
            missing_keys=missing_keys,
            friendly_names=friendly_names,
        )
        exact_point_text = str(seg.get("exact_point_text") or "")
        all_findings.append({
            "finding_id": f"SEGMENT_ARKAT_{point_label.replace('.', '_')}",
            "point_id": point_label,
            "exact_point_id": point_label,
            "exact_point_title": title,
            "exact_point_text": exact_point_text,
            "exact_point_signals": dict(seg.get("exact_point_signals") or {}),
            "category": "A",
            "severity": severity,
            "title": f"Punkt {point_label} ({seg.get('tg')}) mangler full ARK/ARKAT-tekst",
            "message": f"Punkt {point_label} ({title}): mangler {missing_str}. Validering gjøres per punkt; tekst må finnes i selve punktet eller i en tydelig koblet oppsummering.",
            "deduction_band": deduction_band,
            "recommended_fix_text": recommended_fix,
            "suggested_rewrite_text": _build_segment_arkat_suggested_rewrite_text(
                point_label=point_label,
                segment_title=title,
                tg=seg.get("tg", ""),
                missing_keys=missing_keys,
                combined_text=exact_point_text,
            ),
            "evidence_snippets": [exact_point_text] if exact_point_text.strip() else [],
            "gate_effect": {"blocks_96_gate": False, "caps_total_score_to": None},
        })


def _ensure_tg3_missing_cost_compliance_finding(
    all_findings: List[Dict[str, object]],
    point_id: str,
    title: str,
    evidence_text: str,
    missing_key: str = "kostnad",
) -> None:
    normalized_point_id = _normalize_point_id(str(point_id or ""))
    if not normalized_point_id or not isinstance(all_findings, list):
        return
    rule_id = "E_METHOD.tg3_cost_missing"
    finding_id = f"E_METHOD_tg3_cost_missing_{normalized_point_id.replace('.', '_')}"
    for item in all_findings:
        if not isinstance(item, dict):
            continue
        item_rule = str(item.get("rule_id") or "")
        item_id = str(item.get("finding_id") or "")
        item_point = _normalize_point_id(str(item.get("exact_point_id") or item.get("point_id") or ""))
        if (item_rule == rule_id or item_id == finding_id) and item_point == normalized_point_id:
            return
    missing_text = (
        "TG3 har kun ett beløp, men mangler gyldig kostnadsintervall eller kostnadsklasse"
        if missing_key == "kostnad_single_only"
        else "TG3 mangler påkrevd kostnadsestimat eller kostnadsklasse"
    )
    point_title = str(title or normalized_point_id).strip()
    all_findings.append({
        "finding_id": finding_id,
        "rule_id": rule_id,
        "point_id": normalized_point_id,
        "exact_point_id": normalized_point_id,
        "exact_point_title": point_title,
        "exact_point_text": str(evidence_text or ""),
        "category": "E",
        "severity": "major",
        "is_regulatory_breach": True,
        "title": f"Punkt {normalized_point_id} (TG3) mangler påkrevd kostnadsvurdering",
        "message": f"Punkt {normalized_point_id} ({point_title}): {missing_text}. Dette er en metodikk-/lovforankringsmangel for TG3, uavhengig av om ARKAT-teksten ellers beskriver årsak, risiko, konsekvens og tiltak.",
        "deduction_band": "Høyt trekk",
        "points": 8,
        "deduction_points": 8,
        "recommended_fix_text": "Legg inn kostnadsestimat, kostnadsintervall eller kostnadsklasse for TG3-forholdet, slik at økonomisk omfang er tydelig dokumentert.",
        "suggested_rewrite_text": f"Punkt {normalized_point_id}: Oppgi kostnadsintervall eller kostnadsklasse for nødvendig oppfølging av TG3-forholdet.",
        "evidence_snippets": [str(evidence_text or "")[:1200]] if str(evidence_text or "").strip() else [],
        "rewrite_strategy": "tg3_cost_method_compliance",
        "score_impact": "independent_category_e_compliance",
        "gate_effect": {"blocks_96_gate": True},
    })


def _ensure_tg3_missing_cost_compliance_from_segments(analysis_output: Dict[str, object]) -> None:
    if not isinstance(analysis_output, dict):
        return
    segments = analysis_output.get("segment_validation")
    if not isinstance(segments, list):
        return
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        all_findings = []
        analysis_output["all_findings"] = all_findings
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        tg = str(seg.get("tg") or "").upper()
        missing = seg.get("missing") if isinstance(seg.get("missing"), list) else []
        if "TG3" not in tg or not any(key in {"kostnad", "kostnad_single_only"} for key in missing):
            continue
        point_id = str(seg.get("point_id") or "")
        if not point_id:
            continue
        _ensure_tg3_missing_cost_compliance_finding(
            all_findings,
            point_id=point_id,
            title=str(seg.get("exact_point_title") or point_id),
            evidence_text=str(seg.get("exact_point_text") or seg.get("combined_text") or ""),
            missing_key="kostnad_single_only" if "kostnad_single_only" in missing and "kostnad" not in missing else "kostnad",
        )


def _ensure_semantic_tg3_cost_backstop(
    report_text: str,
    analysis_output: Dict[str, object],
) -> None:
    """
    Safety net for NS3600:2025/semi-structured reports where point segmentation can be
    incomplete but Dommer B has a concrete TG3 point. Cost validation remains point-bound.
    """
    if not isinstance(analysis_output, dict):
        return
    pipeline = analysis_output.get("arkat_semantic_pipeline")
    if not isinstance(pipeline, dict):
        return
    meta = analysis_output.get("meta") if isinstance(analysis_output.get("meta"), dict) else {}
    ns_blob = _normalize_tg3_cost_text(
        " ".join(
            str(value or "")
            for value in (
                pipeline.get("ns_version"),
                pipeline.get("report_format"),
                pipeline.get("extraction_method_used"),
                meta.get("ns_version"),
                meta.get("ns_standard_version"),
            )
        )
    ).lower()
    if "2025" not in ns_blob and "semi_structured" not in ns_blob:
        return
    points = pipeline.get("points")
    if not isinstance(points, list):
        return
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        all_findings = []
        analysis_output["all_findings"] = all_findings

    def _has_existing_cost_finding(point_id: str) -> bool:
        wanted = _normalize_point_id(point_id)
        for item in all_findings:
            if not isinstance(item, dict):
                continue
            item_point = _normalize_point_id(str(item.get("exact_point_id") or item.get("point_id") or ""))
            blob = _normalize_tg3_cost_text(
                " ".join(
                    str(item.get(key) or "")
                    for key in ("finding_id", "rule_id", "title", "message", "recommended_fix_text")
                )
            ).lower()
            if item_point == wanted and ("kostnad" in blob or "cost" in blob):
                return True
        return False

    for point in points:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or ""))
        if not point_id or _has_existing_cost_finding(point_id):
            continue
        tg = _normalize_tg_label(point.get("tg_grade") or point.get("tg"))
        raw_point_text = str(point.get("raw_point_text") or "").strip()
        explicit_tg = _extract_tg_label_from_text(raw_point_text)
        if explicit_tg and explicit_tg != "TG3":
            continue
        if tg != "TG3":
            continue
        fields = point.get("extracted_fields") if isinstance(point.get("extracted_fields"), dict) else {}
        field_text = "\n".join(str(fields.get(key) or "") for key in ("aarsak", "risiko", "konsekvens", "anbefalt_tiltak"))
        evidence_text = raw_point_text or field_text
        cost_status = _tg3_cost_status(evidence_text)
        if cost_status == "pass":
            continue
        missing_key = "kostnad_single_only" if cost_status == "medium" else "kostnad"
        title = str(point.get("title") or point.get("point_label") or point_id).strip()
        deduction_band = "Middels trekk" if missing_key == "kostnad_single_only" else "Høyt trekk"
        severity = "minor" if missing_key == "kostnad_single_only" else "major"
        friendly = (
            "TG3 har kun ett beløp - bruk intervall eller kostnadsklasse (E2)"
            if missing_key == "kostnad_single_only"
            else "kostnad / kostnadsklasse for TG3 (E1 - mangler helt)"
        )
        _ensure_tg3_missing_cost_compliance_finding(
            all_findings,
            point_id=point_id,
            title=title,
            evidence_text=evidence_text,
            missing_key=missing_key,
        )


def _ensure_finding_suggestions_differentiated(analysis_output: Dict[str, object]) -> None:
    """
    Ensure every finding has a helpful, explained recommended_fix_text that is specific
    to that finding (point + title), so suggestions are not generic or duplicated.
    """
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return
    for f in all_findings:
        if not isinstance(f, dict):
            continue
        title = (f.get("title") or "").strip()
        message = (f.get("message") or "").strip()
        existing = (f.get("recommended_fix_text") or "").strip()
        if _recommended_fix_text_looks_malformed(f):
            existing = ""
            f["recommended_fix_text"] = ""
        point_id = str(f.get("exact_point_id") or _parse_point_id_from_v16_finding(f) or "").strip()
        if not point_id and title:
            m = POINT_ID_IN_TEXT_RE.search(title)
            if m:
                point_id = m.group(1)
        fid = (f.get("finding_id") or "").lower()
        # SEGMENT_ARKAT already get explained text from _build_segment_arkat_recommended_fix; keep it
        if "segment_arkat_" in fid:
            continue
        # If no suggestion or suggestion doesn't refer to this point, build a point- and title-specific one
        point_in_text = point_id and (point_id in existing or f"punkt {point_id}" in existing.lower() or f"punkt {point_id}." in existing.lower())
        if existing and point_in_text and len(existing) > 60:
            continue
        # Build a short, finding-specific recommendation
        visible_point = _user_visible_point_label(point_id, f)
        point_ref = f" for punkt {visible_point}" if point_id and _is_scoring_eligible_point_id(point_id) else ""
        if "risiko" in fid or "risiko_missing" in title.lower() or "mangler risiko" in title.lower():
            f["recommended_fix_text"] = (
                f"Legg til kort risiko{point_ref}: beskriv hva som kan skje dersom forholdet ikke håndteres, "
                "slik at kjøper kan vurdere alvorlighetsgrad."
            )
        elif "age_only" in fid or "hovedsakelig med alder" in title.lower() or "hovedsakelig med alder" in message.lower():
            observation = _extract_observation_detail(f)
            if observation:
                f["recommended_fix_text"] = (
                    f"Koble aldershenvisningen{point_ref} til konkrete forhold som {observation.lower()}, "
                    "og forklar kort hva kjøper må påregne videre."
                )
            else:
                f["recommended_fix_text"] = (
                    f"Bytt ut aldersbegrunnelse{point_ref} med konkret tilstand: beskriv observert skade/avvik, "
                    "hvorfor forholdet gir TG, og hva kjøper må påregne."
                )
        elif "konsekvens" in fid or "konsekvens" in title.lower():
            observation = _extract_observation_detail(f)
            if observation:
                f["recommended_fix_text"] = (
                    f"Presiser konsekvensen{point_ref} mer praktisk ved å forklare hva {observation.lower()} "
                    "betyr for bruk, sikkerhet, økonomi eller videre skade."
                )
            else:
                f["recommended_fix_text"] = (
                    f"Presiser konsekvensen{point_ref} med én kort setning om praktisk betydning "
                    "(bruk/sikkerhet/økonomi/videre skade) for kjøper."
                )
        elif "tiltak" in fid or "anbefalt" in title.lower():
            f["recommended_fix_text"] = (
                f"Formuler anbefalt tiltak{point_ref} tydelig (f.eks. «Det anbefales å …» eller «Bør utføres av fagperson»), "
                "slik at kjøper vet hva som bør gjøres."
            )
        elif "årsak" in title.lower() or "arkat" in fid:
            f["recommended_fix_text"] = (
                f"Legg inn kort og tydelig årsak{point_ref} (hva som er feil / hvorfor forholdet har oppstått), "
                "enten i punktteksten eller i en oppsummering merket med samme punktnummer."
            )
        elif "fagspråk" in title.lower() or "jargon" in fid or "språk" in fid:
            f["recommended_fix_text"] = (
                "Forklar kort hva det faglige uttrykket betyr i praksis, slik at kjøper forstår konsekvensen."
            )
        elif "kostnad" in title.lower() or "kostnad" in fid:
            f["recommended_fix_text"] = (
                f"Angi kostnad eller kostnadsklasse{point_ref} (f.eks. intervall, lav/middels/høy), "
                "slik at kjøper kan vurdere økonomisk belastning."
            )
        else:
            f["recommended_fix_text"] = (
                f"Gjør punktteksten{point_ref} mer konkret og punktspesifikk: beskriv hva som er observert, "
                "hvilken risiko/konsekvens dette gir, og hva som anbefales videre."
            )


def _recommended_fix_text_looks_malformed(finding: Dict[str, object]) -> bool:
    text = str(finding.get("recommended_fix_text") or "").strip()
    if not text:
        return False
    normalized = _normalize_tg3_cost_text(text).lower()
    if any(
        marker in normalized
        for marker in (
            "vurdering av avvik:",
            "konsekvens/tiltak:",
            "konsekvens tiltak:",
            "andre tiltak:",
            "kommentar:",
        )
    ):
        return True
    exact_point_text = _normalize_tg3_cost_text(str(finding.get("exact_point_text") or "")).lower().strip()
    if exact_point_text and len(exact_point_text) >= 80 and exact_point_text[:120] in normalized:
        return True
    return False


def _build_source_grounded_recommended_fix(finding: Dict[str, object]) -> str:
    point_id = str(finding.get("exact_point_id") or _parse_runtime_point_ref_from_v16_finding(finding) or "").strip()
    visible_point = _user_visible_point_label(point_id, finding)
    point_ref = f" for punkt {visible_point}" if point_id and _is_scoring_eligible_point_id(point_id) else ""
    fid = str(finding.get("finding_id") or "").lower()
    title = _normalize_tg3_cost_text(str(finding.get("title") or "")).lower()
    message = _normalize_tg3_cost_text(str(finding.get("message") or "")).lower()
    observation = _extract_observation_detail(finding).strip().rstrip(".")

    if "age_only" in fid or "hovedsakelig med alder" in title or "hovedsakelig med alder" in message:
        if observation:
            return (
                f"Koble aldershenvisningen{point_ref} til det som faktisk er beskrevet i rapporten, "
                f"for eksempel «{observation}.», og unngå å legge til sterkere skader enn kilden støtter."
            )
        return (
            f"Koble aldershenvisningen{point_ref} til konkrete forhold som faktisk står i rapporten, "
            "og unngå å legge til nye skader eller funksjonssvikt som ikke er beskrevet."
        )
    if "risiko" in fid or "risiko" in title:
        return (
            f"Beskriv risikoen{point_ref} som hva som kan skje dersom forholdet ikke følges opp, "
            "basert på opplysninger som allerede står i rapporten."
        )
    if "konsekvens" in fid or "konsekvens" in title:
        return (
            f"Beskriv konsekvensen{point_ref} som hva forholdet betyr for kjøper i praksis "
            "(bruk, kostnad, vedlikehold eller videre oppfølging), basert på rapportteksten."
        )
    if "årsak" in title or "arsak" in title or "arkat" in fid:
        return (
            f"Beskriv årsaken{point_ref} med det som faktisk er observert i rapporten, "
            "ikke bare alder eller generelle antakelser."
        )
    if observation:
        return (
            f"Ta utgangspunkt i det som står i rapporten{point_ref}, for eksempel «{observation}.», "
            "og gjør veiledningen kortere og mer punktspesifikk."
        )
    return (
        f"Gjør veiledningen{point_ref} kort, punktspesifikk og forankret i det som faktisk står i rapporten."
    )


def _recommended_fix_text_looks_unsupported(finding: Dict[str, object], text: str) -> bool:
    candidate = _normalize_tg3_cost_text(text or "").lower().strip()
    if not candidate:
        return False
    if _recommended_fix_text_looks_malformed({"recommended_fix_text": text, **finding}):
        return True
    source = _normalize_tg3_cost_text(str(finding.get("exact_point_text") or "")).lower().strip()
    observation = _normalize_tg3_cost_text(_extract_observation_detail(finding)).lower().strip()
    if observation and f"hva {observation}" in candidate:
        return True
    if observation and f"som {observation}" in candidate and len(observation.split()) > 6:
        return True
    if _suggested_rewrite_text_looks_unsupported(finding, text):
        return True
    if not source:
        return False
    stitched_markers = (
        "ved å forklare hva ",
        "konkrete forhold som ",
        "for eksempeltekst du kan bruke",
    )
    if any(marker in candidate for marker in stitched_markers):
        overlap = source[:80].strip()
        if overlap and overlap in candidate:
            return True
    return False


def _is_generic_guidance_text(text: str) -> bool:
    if not text or not isinstance(text, str):
        return True
    low = _normalize_tg3_cost_text(text).lower()
    if len(low) < 40:
        return True
    generic_patterns = (
        "oppdater innholdet",
        "tydelig adresserer funnet",
        "beskriv konkret hva som mangler",
        "forbedre teksten",
        "presiser teksten",
        "legg til mer informasjon",
        "se forbedringsforslag",
    )
    return any(p in low for p in generic_patterns)


def _infer_rewrite_strategy(finding: Dict[str, object]) -> str:
    fid = str(finding.get("finding_id") or "").lower()
    title = str(finding.get("title") or "").lower()
    message = str(finding.get("message") or "").lower()
    text = f"{fid} {title} {message}"
    if "arsak" in text or "årsak" in text:
        return "cause_specific_clarification"
    if "risiko" in text:
        return "risk_contextual_refinement"
    if "konsekvens" in text:
        return "consequence_contextual_refinement"
    if "tiltak" in text or "anbefalt" in text:
        return "measure_contextual_refinement"
    if "kostnad" in text:
        return "cost_estimate_clarification"
    if "hms" in text or "no_tg" in text or "lovlighet" in text:
        return "no_tg_hms_explanation"
    return "finding_specific_rewrite"


def _extract_building_part_context(finding: Dict[str, object]) -> str:
    exact_title = str(finding.get("exact_point_title") or "").strip()
    if _is_canonical_child_point_id(exact_title):
        exact_title = _resolve_child_title(exact_title, finding)
    if exact_title:
        return exact_title
    title = str(finding.get("title") or "").strip()
    message = str(finding.get("message") or "").strip()
    point_id = _parse_runtime_point_ref_from_v16_finding(finding)
    point_marker = _user_visible_point_label(point_id, finding)
    if point_marker and _is_scoring_eligible_point_id(point_id):
        point_marker = f"Punkt {point_marker}"
    for source in (title, message):
        if not source:
            continue
        cleaned = re.sub(r"^\s*Punkt\s+\d+(?:\.\d+)*\s*", "", source, flags=re.IGNORECASE).strip(" :-")
        paren = re.search(r"\(([^)]+)\)", cleaned)
        if paren:
            candidate = paren.group(1).strip()
            if candidate and candidate.upper() not in {"TG0", "TG1", "TG2", "TG3", "TGIU"}:
                return candidate
        if ":" in cleaned:
            candidate = cleaned.split(":", 1)[0].strip()
            if candidate and "mangler" not in candidate.lower():
                return candidate
    return point_marker


def _extract_observation_detail(finding: Dict[str, object]) -> str:
    def _best_line(text: str) -> str:
        normalized_text = _polish_feedback_text(text).strip()
        if not normalized_text:
            return ""
        candidates: List[str] = []
        for raw_line in normalized_text.splitlines():
            candidate = raw_line.strip().strip("\"'")
            if not candidate or len(candidate) < 18:
                continue
            if candidate.lower().startswith("punkt "):
                continue
            candidates.append(candidate.rstrip("."))
        if not candidates:
            return ""

        def _score(candidate: str) -> Tuple[int, int]:
            low = _normalize_tg3_cost_text(candidate).lower()
            age_only_markers = (
                "halvparten av sin forventede levetid",
                "mer enn halvparten av forventet",
                "forventet levetid",
                "forventet brukstid",
                "vurdering er basert pa alder",
                "vurdering er basert på alder",
                "av eldre dato",
                "i slutten av sin forventede levetid",
            )
            concrete_markers = (
                "det registreres",
                "det er registrert",
                "det er pavist",
                "det er påvist",
                "det er målt",
                "fuktskj",
                "råte",
                "sprek",
                "lekk",
                "svikt",
                "misfarging",
                "manglende",
                "hulrom",
                "skade",
                "utett",
                "korros",
                "deform",
                "fall inn mot",
                "utilstrekkelig",
                "slukmansjett",
            )
            practical_consequence_markers = (
                "konsekvens:",
                "kan medføre",
                "fører til",
                "må påregne",
                "høyere strømforbruk",
                "kostbare reparasjoner",
                "vannskader",
                "dårlig inneklima",
                "redusert funksjon",
                "fuktskader",
            )
            recommendation_markers = (
                "anbefalt",
                "anbefales",
                "det bør",
                "bør utføres",
                "må byttes",
                "må utbedres",
                "vurderes",
            )

            score = 0
            if any(marker in low for marker in concrete_markers):
                score += 6
            if any(marker in low for marker in practical_consequence_markers):
                score += 4
            if any(marker in low for marker in recommendation_markers):
                score += 2
            if any(marker in low for marker in age_only_markers):
                score -= 3
            if low.startswith("konsekvens:"):
                score += 2
            elif low.startswith("risiko:"):
                score += 1
            return score, len(candidate)

        return max(candidates, key=_score)

    exact_text = _polish_feedback_text(str(finding.get("exact_point_text") or "")).strip()
    if exact_text:
        best = _best_line(exact_text)
        if best:
            return best
    snippets = [s for s in (finding.get("evidence_snippets") or []) if isinstance(s, str) and s.strip()]
    for snippet in snippets:
        candidate = _best_line(snippet)
        if candidate:
            candidate = re.sub(r"^\s*Punkt\s+\d+(?:\.\d+)*[:\-]?\s*", "", candidate, flags=re.IGNORECASE)
            return candidate.rstrip(".")
    message = _polish_feedback_text(str(finding.get("message") or "")).strip()
    if message:
        message = re.sub(r"^\s*Punkt\s+\d+(?:\.\d+)*[:\-]?\s*", "", message, flags=re.IGNORECASE)
        return message.rstrip(".")
    return ""


def _build_suggested_rewrite_text(finding: Dict[str, object]) -> str:
    existing = str(finding.get("suggested_rewrite_text") or "").strip()
    if existing:
        return existing
    point_id = str(finding.get("exact_point_id") or _parse_runtime_point_ref_from_v16_finding(finding) or "").strip()
    point_prefix = _user_visible_point_prefix(point_id, finding)
    strategy = str(finding.get("rewrite_strategy") or "").strip()
    evidence_detail = _extract_observation_detail(finding)
    building_part = _extract_building_part_context(finding)
    part_prefix = f"{building_part}: " if building_part and building_part != point_prefix.rstrip(": ") else ""
    message = str(finding.get("message") or finding.get("title") or "").strip()
    message_clean = _polish_feedback_text(message).strip().rstrip(".")
    if strategy == "risk_contextual_refinement":
        if evidence_detail:
            return point_prefix + part_prefix + f"Det er risiko for videre utvikling dersom {evidence_detail.lower()} ikke følges opp."
        return point_prefix + part_prefix + "Beskriv hva forholdet kan utvikle seg til dersom det ikke følges opp."
    if strategy == "consequence_contextual_refinement":
        if evidence_detail:
            return point_prefix + part_prefix + f"Konsekvensen bør presiseres praktisk ved å forklare hva {evidence_detail.lower()} betyr for bruk, økonomi, sikkerhet eller videre skade."
        return point_prefix + part_prefix + "Presiser hvilken praktisk betydning forholdet har for bruk, økonomi eller videre skade."
    if strategy == "measure_contextual_refinement":
        if evidence_detail:
            return point_prefix + part_prefix + f"Det anbefales å utbedre eller undersøke nærmere der {evidence_detail.lower()}."
        return point_prefix + part_prefix + "Skriv ett konkret anbefalt tiltak som passer akkurat dette forholdet."
    if strategy == "cost_estimate_clarification":
        if building_part:
            return point_prefix + part_prefix + "Oppgi kostnadsklasse eller kostnadsintervall for dette punktet slik at økonomisk omfang blir tydelig."
        return point_prefix + "Oppgi et kostnadsintervall eller kostnadsklasse slik at økonomisk omfang blir tydelig."
    if strategy == "cause_specific_clarification":
        if evidence_detail:
            return point_prefix + part_prefix + f"Årsaken beskrives som at {evidence_detail.lower()}."
        return point_prefix + part_prefix + "Beskriv kort hva som er observert og hvorfor dette gir avvik på dette punktet."
    if strategy == "no_tg_hms_explanation":
        if evidence_detail:
            return point_prefix + part_prefix + f"Forklar hva forholdet gjelder, at {evidence_detail.lower()}, hvilke konsekvenser dette kan ha, og hvilken oppfølging som anbefales."
        return point_prefix + part_prefix + "Forklar tydelig hva forholdet innebærer, hvilke konsekvenser det kan ha og hva slags oppfølging som anbefales."
    if "age_only" in str(finding.get("finding_id") or "").lower():
        evidence_low = _normalize_tg3_cost_text(evidence_detail).lower()
        service_life_only_markers = (
            "forventet levetid",
            "halvparten av forventet levetid",
            "over halve forventete levetid",
            "oppbrukt levetid",
            "mer enn halvparten av forventet levetid",
            "gjenstående brukstid",
            "kort gjenværende brukstid",
            "alder",
            "byggedato",
        )
        has_concrete_condition = any(
            marker in evidence_low
            for marker in (
                "skade",
                "svikt",
                "fukt",
                "råte",
                "sprek",
                "avvik",
                "lekk",
                "utetthet",
                "mugg",
                "sopp",
                "korros",
                "vannansamling",
                "fall mot grunnmuren",
                "manglende lufting",
                "misfarging",
            )
        )
        service_life_only = evidence_low and any(marker in evidence_low for marker in service_life_only_markers) and not has_concrete_condition
        if evidence_detail and has_concrete_condition and not service_life_only:
            return point_prefix + part_prefix + f"TG2 bør begrunnes med observerte forhold som {evidence_detail.lower()}, og knyttes til hva dette betyr for kjøper, ikke bare alder eller levetid."
        if message_clean:
            return point_prefix + part_prefix + f"TG2 må begrunnes med konkret observert tilstand og praktisk konsekvens, ikke bare alder. {message_clean}."
        return point_prefix + part_prefix + "TG2 må begrunnes med konkrete observerte avvik og praktisk konsekvens, ikke bare alder eller passert levetid."
    if message_clean and not _is_generic_guidance_text(message_clean):
        return f"{point_prefix}{part_prefix}{message_clean}"
    return ""


def _source_has_concrete_condition_signals(text: str) -> bool:
    normalized = _normalize_tg3_cost_text(text or "").lower()
    if not normalized:
        return False
    concrete_markers = (
        "skade",
        "svikt",
        "fukt",
        "råte",
        "mugg",
        "sopp",
        "korros",
        "sprekk",
        "riss",
        "avskalling",
        "misfarging",
        "lekk",
        "deform",
        "setning",
        "punktert",
        "manglende fall",
        "utilstrekkelig",
        "manglende lufting",
        "knirk",
        "buler",
        "sokk",
        "søkk",
        "ujevn",
        "for lavt",
        "ikke forskriftsmessig",
        "mosedannelse",
        "ildfast plate mangler",
    )
    return any(marker in normalized for marker in concrete_markers)


def _suggested_rewrite_text_looks_unsupported(finding: Dict[str, object], text: str) -> bool:
    candidate = _normalize_tg3_cost_text(text or "").lower().strip()
    if not candidate:
        return False
    source = _normalize_tg3_cost_text(str(finding.get("exact_point_text") or "")).lower().strip()
    if not source:
        return False
    fid = str(finding.get("finding_id") or "").lower()
    title_and_message = _normalize_tg3_cost_text(
        f"{finding.get('title', '')} {finding.get('message', '')}"
    ).lower()
    source_has_concrete = _source_has_concrete_condition_signals(source)

    assertive_markers = (
        "viser tegn til",
        "indikerer",
        "skyldes",
        "som følge av",
        "har medfort",
        "har medført",
        "funksjonssvikt",
        "strukturelle problemer",
        "strukturelle skader",
        "aldersrelatert slitasje",
        "redusert funksjon",
        "svekket",
        "svikt i konstruksjonen",
    )
    stronger_defect_markers = (
        "slitasje",
        "funksjonssvikt",
        "strukturelle problemer",
        "strukturelle skader",
        "råte",
        "mugg",
        "sopp",
        "korrosjon",
        "deformasjon",
        "lekkasje",
        "fuktskader",
        "vanninntrenging",
        "punktering",
        "setningsskader",
        "redusert funksjon",
        "svekket",
    )

    for marker in assertive_markers:
        if marker in candidate and marker not in source:
            return True

    age_or_missing_content_finding = (
        "age_only" in fid
        or "mangler ars" in title_and_message
        or "mangler års" in title_and_message
        or "mangler risiko" in title_and_message
        or "mangler konsekvens" in title_and_message
        or "hovedsakelig med alder" in title_and_message
    )
    if age_or_missing_content_finding and not source_has_concrete:
        for marker in stronger_defect_markers:
            if marker in candidate and marker not in source:
                return True

    if "fordi " in candidate and "fordi " not in source:
        because_clause = candidate.split("fordi ", 1)[1]
        if any(marker in because_clause and marker not in source for marker in stronger_defect_markers):
            return True
    return False


def _build_source_grounded_rewrite_fallback(finding: Dict[str, object]) -> str:
    point_id = str(finding.get("exact_point_id") or _parse_runtime_point_ref_from_v16_finding(finding) or "").strip()
    point_prefix = _user_visible_point_prefix(point_id, finding)
    building_part = _extract_building_part_context(finding)
    part_prefix = f"{building_part}: " if building_part and building_part != point_prefix.rstrip(": ") else ""
    observation = _extract_observation_detail(finding)
    if observation:
        return (
            f"{point_prefix}{part_prefix}{observation}. "
            "Presiser dette uten å legge til nye forhold som ikke står i rapporten."
        )
    return ""


def _ensure_writing_help_fields(analysis_output: Dict[str, object]) -> None:
    for key in ("all_findings", "top_issues", "how_to_improve"):
        items = analysis_output.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            recommended_fix = str(item.get("recommended_fix_text") or "").strip()
            if not recommended_fix:
                fallback = str(item.get("message") or item.get("title") or "").strip()
                if fallback:
                    item["recommended_fix_text"] = fallback
            if _recommended_fix_text_looks_unsupported(item, str(item.get("recommended_fix_text") or "")):
                item["recommended_fix_text"] = _build_source_grounded_recommended_fix(item)
            if not str(item.get("rewrite_strategy") or "").strip():
                item["rewrite_strategy"] = _infer_rewrite_strategy(item)
            item["suggested_rewrite_text"] = _build_suggested_rewrite_text(item)
            if _suggested_rewrite_text_looks_unsupported(item, str(item.get("suggested_rewrite_text") or "")):
                item["suggested_rewrite_text"] = _build_source_grounded_rewrite_fallback(item)
            rec_norm = _normalize_tg3_cost_text(str(item.get("recommended_fix_text") or "")).lower().strip()
            sug_norm = _normalize_tg3_cost_text(str(item.get("suggested_rewrite_text") or "")).lower().strip()
            if not sug_norm or sug_norm == rec_norm:
                item["suggested_rewrite_text"] = ""


def _drop_segment_arkat_for_tg2_only_points(analysis_output: Dict[str, object]) -> None:
    """
    Remove SEGMENT_ARKAT (TG3) findings for points that the LLM has assessed as TG2-only
    (e.g. 'TG2 mangler risiko'). Those points should not get a TG3 cost requirement.
    """
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return
    tg2_only_point_ids = set()
    for f in all_findings:
        if not isinstance(f, dict):
            continue
        fid = (f.get("finding_id") or "").lower()
        if "segment_arkat_" in fid:
            continue
        title = (f.get("title") or "").lower()
        msg = (f.get("message") or "").lower()
        # TG2-specific finding: title/message refers to TG2 and not full TG3 ARKAT/cost
        is_tg2_risiko = "tg2" in title and "mangler risiko" in title
        is_tg2_risiko_rule = "arkat_risiko" in fid or "risiko_missing" in fid
        if not (is_tg2_risiko or is_tg2_risiko_rule):
            continue
        point_id = _parse_point_id_from_v16_finding(f)
        if not point_id:
            m = POINT_ID_IN_TEXT_RE.search(str((f.get("title") or "") + " " + (f.get("message") or "")))
            if m:
                point_id = m.group(1)
        if point_id:
            tg2_only_point_ids.add(_normalize_point_id(point_id))
    to_drop = []
    for idx, f in enumerate(all_findings):
        if not isinstance(f, dict):
            continue
        fid = f.get("finding_id") or ""
        if "SEGMENT_ARKAT_" not in fid:
            continue
        # SEGMENT_ARKAT_10_5 -> 10.5, SEGMENT_ARKAT_7_1_1 -> 7.1.1
        suffix = fid.replace("SEGMENT_ARKAT_", "").replace("_", ".")
        if _normalize_point_id(suffix) in tg2_only_point_ids:
            to_drop.append(idx)
    for idx in reversed(to_drop):
        all_findings.pop(idx)


def _drop_tg3_cost_top_issues_if_segments_have_cost(analysis_output: Dict[str, object]) -> None:
    """
    When our own per-segment validation says all TG3 segments have a valid
    cost/cost class (no 'kostnad' or 'kostnad_single_only' missing), drop
    high-level TG3-cost drivers from top_issues / top_score_drivers so we
    don't surface false "TG3 mangler sjablonmessig kostnadsanslag" messages.
    """
    seg_val = analysis_output.get("segment_validation")
    if not isinstance(seg_val, list):
        return
    has_missing_cost = False
    for seg in seg_val:
        if not isinstance(seg, dict):
            continue
        tg = str(seg.get("tg") or "").upper()
        missing = seg.get("missing") or []
        if not isinstance(missing, list):
            continue
        if "TG3" in tg and any(m in ("kostnad", "kostnad_single_only") for m in missing):
            has_missing_cost = True
            break
    if has_missing_cost:
        # Our validator still sees missing TG3 cost for at least one segment – keep messages.
        return

    def _is_cost_issue(title: object, message: object) -> bool:
        text = f"{str(title or '')} {str(message or '')}".lower()
        return (
            "sjablongmessig kostnadsanslag" in text
            or
            "sjablonmessig kostnadsanslag" in text
            or "kostnadsanslag for tg3" in text
            or "tg3-cost" in text
            or "tg3 cost" in text
        )

    top_issues = analysis_output.get("top_issues")
    if isinstance(top_issues, list):
        analysis_output["top_issues"] = [
            ti
            for ti in top_issues
            if not (isinstance(ti, dict) and _is_cost_issue(ti.get("title"), ti.get("message")))
        ]

    top_score_drivers = analysis_output.get("top_score_drivers")
    if isinstance(top_score_drivers, list):
        filtered_drivers = []
        for d in top_score_drivers:
            if not isinstance(d, dict):
                filtered_drivers.append(d)
                continue
            if _is_cost_issue(d.get("title"), d.get("reason")):
                continue
            refs = d.get("rule_refs") or []
            refs_lower = [str(r).lower() for r in refs if isinstance(r, str)]
            if any("tg3_cost" in r or "cost_missing" in r for r in refs_lower):
                continue
            filtered_drivers.append(d)
        analysis_output["top_score_drivers"] = filtered_drivers

    # Tidy category_breakdown: if E summary only talks about sjablonmessig kostnadsanslag, neutralise text.
    category_breakdown = analysis_output.get("category_breakdown")
    if isinstance(category_breakdown, list):
        for entry in category_breakdown:
            if not isinstance(entry, dict):
                continue
            cat = str(entry.get("category") or entry.get("category_id") or "").upper()
            if cat != "E":
                continue
            summary = str(entry.get("summary") or "")
            summary_low = summary.lower()
            if "sjablonmessig kostnadsanslag" in summary_low or "sjablongmessig kostnadsanslag" in summary_low:
                entry["summary"] = "Metodikk og lovforankring: se øvrige funn."


def _drop_tg2_tiltak_requirement_false_positives(analysis_output: Dict[str, object]) -> None:
    """
    TG2 rule guard: TG2 must not be penalized for missing recommended measure (tiltak).
    Remove such findings/drivers/issues while keeping TG3 tiltak requirements intact.
    """
    def _is_tg2_tiltak_only(text: str, tg_hint: str = "") -> bool:
        # Structural segment findings must not be stripped; they reflect regex/semantic checks, not TG2 policy.
        if "SEGMENT_ARKAT_" in (text or ""):
            return False
        low = _normalize_tg3_cost_text(text or "").lower()
        tg_norm = str(tg_hint or "").strip().upper()
        has_tg2 = ("tg2" in low) or (tg_norm == "TG2")
        has_tg3 = ("tg3" in low) or (tg_norm == "TG3")
        has_missing_marker = bool(re.search(r"\bmangl(?:er|ende|et)\b|\buten\b|\bfrav[æa]r\b", low))
        has_tiltak_phrase = (
            "mangler anbefalt tiltak" in low
            or "manglende anbefalt tiltak" in low
            or "tg2-punkt mangler anbefalt tiltak" in low
            or "anbefalt tiltak mangler" in low
            or "mangler tiltak" in low
            or (("anbefalt tiltak" in low or "tiltak" in low) and has_missing_marker)
            or ("tg2-punkt" in low and "tiltak" in low and has_missing_marker)
        )
        if not has_tiltak_phrase:
            return False
        if has_tg3:
            return False
        return has_tg2 or has_tiltak_phrase

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            component_tg = str(component.get("tg") or "").strip().upper()
            issues = component.get("issues")
            if isinstance(issues, list):
                component["issues"] = [
                    issue for issue in issues
                    if not (
                        isinstance(issue, dict)
                        and _is_tg2_tiltak_only(
                            f"{issue.get('title', '')} {issue.get('message', '')} {' '.join([str(r) for r in (issue.get('rule_refs') or []) if isinstance(r, str)])}",
                            component_tg,
                        )
                    )
                ]

    all_findings = analysis_output.get("all_findings")
    if isinstance(all_findings, list):
        analysis_output["all_findings"] = [
            f for f in all_findings
            if not (
                isinstance(f, dict)
                and _is_tg2_tiltak_only(
                    f"{f.get('finding_id', '')} {f.get('rule_id', '')} {f.get('title', '')} {f.get('message', '')} {f.get('recommended_fix_text', '')}",
                    str(f.get("tg") or ""),
                )
            )
        ]

    top_issues = analysis_output.get("top_issues")
    if isinstance(top_issues, list):
        analysis_output["top_issues"] = [
            i for i in top_issues
            if not (
                isinstance(i, dict)
                and _is_tg2_tiltak_only(f"{i.get('title', '')} {i.get('message', '')}")
            )
        ]

    top_score_drivers = analysis_output.get("top_score_drivers")
    if isinstance(top_score_drivers, list):
        analysis_output["top_score_drivers"] = [
            d for d in top_score_drivers
            if not (
                isinstance(d, dict)
                and _is_tg2_tiltak_only(
                    f"{d.get('title', '')} {d.get('reason', '')} {' '.join([str(r) for r in (d.get('rule_refs') or []) if isinstance(r, str)])}"
                )
            )
        ]


def _drop_no_tg_hms_as_regular_tg_findings(
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
) -> None:
    no_tg_points = {
        _normalize_point_id(str(p.get("point_id") or ""))
        for p in detected_points
        if isinstance(p, dict) and bool(p.get("no_tg_hms_point"))
    }
    if not no_tg_points:
        return

    def _is_regular_tg_blob(blob: str) -> bool:
        low = _normalize_tg3_cost_text(blob).lower()
        if "a_no_tg_hms" in low or "no_tg_hms" in low:
            return False
        return ("tg2" in low or "tg3" in low or "arkat" in low or "anbefalt tiltak" in low)

    def _should_drop(item: Dict[str, object]) -> bool:
        point_id = str(item.get("exact_point_id") or _parse_point_id_from_v16_finding(item) or "")
        if _normalize_point_id(point_id) not in no_tg_points:
            return False
        blob = (
            f"{item.get('finding_id', '')} {item.get('rule_id', '')} {item.get('title', '')} "
            f"{item.get('message', '')} {item.get('exact_point_title', '')} {item.get('exact_point_text', '')}"
        )
        return _is_regular_tg_blob(blob)

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            component_id = _normalize_point_id(str(component.get("component_id") or ""))
            if component_id not in no_tg_points:
                continue
            issues = component.get("issues")
            if isinstance(issues, list):
                component["issues"] = [
                    issue
                    for issue in issues
                    if not (
                        isinstance(issue, dict)
                        and _is_regular_tg_blob(
                            f"{issue.get('summary', '')} {issue.get('details', '')} "
                            f"{' '.join([str(r) for r in (issue.get('rule_refs') or []) if isinstance(r, str)])}"
                        )
                    )
                ]
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                component["deductions"] = [
                    deduction
                    for deduction in deductions
                    if not (
                        isinstance(deduction, dict)
                        and _is_regular_tg_blob(
                            f"{deduction.get('rule_id', '')} {deduction.get('reason', '')}"
                        )
                    )
                ]

    all_findings = analysis_output.get("all_findings")
    if isinstance(all_findings, list):
        analysis_output["all_findings"] = [
            f for f in all_findings if not (isinstance(f, dict) and _should_drop(f))
        ]

    top_issues = analysis_output.get("top_issues")
    if isinstance(top_issues, list):
        analysis_output["top_issues"] = [
            i for i in top_issues if not (isinstance(i, dict) and _should_drop(i))
        ]


def _get_legality_rule_date_constraints(rule_id: str) -> Dict[str, str]:
    rules_payload = _load_legality_rules()
    rules = rules_payload.get("rules", []) if isinstance(rules_payload, dict) else []
    for rule in rules:
        if isinstance(rule, dict) and str(rule.get("id") or "") == rule_id:
            return {
                "applies_from_rapportdato": str(rule.get("applies_from_rapportdato") or ""),
                "suppress_if_rapportdato_before": str(rule.get("suppress_if_rapportdato_before") or ""),
            }
    return {}


def _legality_rule_is_active(rule_id: str, report_date: str) -> bool:
    if not rule_id:
        return True
    constraints = _get_legality_rule_date_constraints(rule_id)
    suppress_before = constraints.get("suppress_if_rapportdato_before") or ""
    applies_from = constraints.get("applies_from_rapportdato") or ""
    if suppress_before and report_date and report_date < suppress_before:
        return False
    if applies_from and report_date and report_date < applies_from:
        return False
    return True


def _filter_regime_conditioned_rules(
    report_text: str,
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
) -> None:
    context = _extract_report_regime_context(report_text)
    report_date = context.get("report_date") or ""
    ns_version = context.get("ns_version") or ""
    report_requires_areal_rule = _report_requires_areal_ns3940_2023(report_text)
    no_tg_points = {
        _normalize_point_id(str(point.get("point_id") or ""))
        for point in detected_points
        if isinstance(point, dict) and bool(point.get("no_tg_hms_point"))
    }
    garage_arkat_missing_points = {
        _normalize_point_id(str(point.get("point_id") or ""))
        for point in detected_points
        if isinstance(point, dict) and bool(point.get("garage_avvik_uten_arkat"))
    }
    garage_tg_arkat_missing_points = {
        _normalize_point_id(str(point.get("point_id") or ""))
        for point in detected_points
        if isinstance(point, dict) and bool(point.get("garage_tg_uten_full_arkat"))
    }
    habitable_annex_without_tg_points = {
        _normalize_point_id(str(point.get("point_id") or ""))
        for point in detected_points
        if isinstance(point, dict) and bool(point.get("habitable_annex_without_tg"))
    }

    def _is_rule_active(rule_id: str, point_id: str, blob: str) -> bool:
        rid = str(rule_id or "").strip()
        norm_point_id = _normalize_point_id(point_id)
        low_blob = _normalize_tg3_cost_text(blob).lower()
        if not rid:
            return True
        if rid == "B_TG.el_anlegg_tg_forbudt":
            return _iso_date_at_or_after(report_date, "2026-01-01")
        if rid == "E_METHOD.areal_ns3940_2023":
            return _iso_date_at_or_after(report_date, "2026-01-01") and report_requires_areal_rule
        if rid == "E_METHOD.egenerklaring_missing":
            return False
        if rid == "E_METHOD.fritstaaende_bygg_avvik_uten_tg":
            return False
        if rid == "E_METHOD.garasje_avvik_uten_arkat":
            return bool(garage_arkat_missing_points) if not norm_point_id else norm_point_id in garage_arkat_missing_points
        if rid == "E_METHOD.garasje_tg_uten_full_arkat":
            return bool(garage_tg_arkat_missing_points) if not norm_point_id else norm_point_id in garage_tg_arkat_missing_points
        if rid == "E_METHOD.anneks_varig_opphold_mangler_tg":
            return bool(habitable_annex_without_tg_points) if not norm_point_id else norm_point_id in habitable_annex_without_tg_points
        if rid.startswith("A_NO_TG_HMS") or rid == "A_NO_TG_HMS_ELEKTRISK":
            return bool(no_tg_points) if not norm_point_id else norm_point_id in no_tg_points
        if rid == "L-RK-01":
            return _legality_rule_is_active(rid, report_date)
        if "rekkverk" in low_blob and ("no_tg_hms" in low_blob or "skal ikke gis tilstandsgrad" in low_blob):
            return ns_version == "NS 3600:2025"
        return True

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            component_id = str(component.get("component_id") or "")
            issues = component.get("issues")
            if isinstance(issues, list):
                component["issues"] = [
                    issue
                    for issue in issues
                    if not (
                        isinstance(issue, dict)
                        and any(
                            not _is_rule_active(
                                str(rule_ref),
                                component_id,
                                f"{issue.get('summary', '')} {issue.get('details', '')} {rule_ref}",
                            )
                            for rule_ref in (issue.get("rule_refs") or [])
                            if isinstance(rule_ref, str)
                        )
                    )
                ]
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                component["deductions"] = [
                    deduction
                    for deduction in deductions
                    if not (
                        isinstance(deduction, dict)
                        and not _is_rule_active(
                            str(deduction.get("rule_id") or ""),
                            component_id,
                            f"{deduction.get('rule_id', '')} {deduction.get('reason', '')}",
                        )
                    )
                ]

    def _filter_items(items: object) -> object:
        if not isinstance(items, list):
            return items
        filtered = []
        for item in items:
            if not isinstance(item, dict):
                filtered.append(item)
                continue
            evidence = item.get("evidence")
            evidence_point_id = ""
            if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
                evidence_point_id = str(evidence[0].get("point_id") or "")
            point_id = str(
                item.get("exact_point_id")
                or item.get("point_id")
                or item.get("component_id")
                or evidence_point_id
                or _parse_runtime_point_ref_from_v16_finding(item)
                or _parse_point_id_from_v16_finding(item)
                or ""
            )
            rule_ids = []
            if isinstance(item.get("rule_refs"), list):
                rule_ids = [str(rule_id) for rule_id in item.get("rule_refs") if isinstance(rule_id, str)]
            elif item.get("rule_id"):
                rule_ids = [str(item.get("rule_id"))]
            elif item.get("finding_id"):
                rule_ids = [str(item.get("finding_id"))]
            blob = (
                f"{item.get('finding_id', '')} {item.get('rule_id', '')} "
                f"{item.get('title', '')} {item.get('message', '')} {item.get('reason', '')}"
            )
            if any(not _is_rule_active(rule_id, point_id, blob) for rule_id in rule_ids):
                continue
            filtered.append(item)
        return filtered

    for key in ("all_findings", "top_issues", "top_score_drivers"):
        analysis_output[key] = _filter_items(analysis_output.get(key))


def _soften_no_tg_hms_findings(
    report_text: str,
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
) -> None:
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list) or not all_findings:
        return
    linked = _extract_linked_summary_text_per_point(report_text or "")
    no_tg_segments: Dict[str, str] = {}
    available_point_ids = [
        _normalize_point_id(str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or ""))
        for p in detected_points
        if isinstance(p, dict)
    ]
    for point in detected_points:
        if not isinstance(point, dict) or not bool(point.get("no_tg_hms_point")):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or ""))
        if not point_id:
            continue
        no_tg_segments[point_id] = _get_effective_point_text(point, linked, available_point_ids=available_point_ids)
    if not no_tg_segments:
        return

    def _quality(segment_text: str) -> str:
        signals = _segment_content_signals(segment_text)
        if signals["documentation_ok"]:
            return "good"
        has_explanation = signals["observation_present"]
        has_consequence = signals["consequence_present"]
        has_recommendation = signals["recommendation_present"]
        if has_explanation and (has_consequence or has_recommendation):
            return "good"
        if has_explanation or has_consequence or has_recommendation:
            return "partial"
        return "missing"

    for finding in all_findings:
        if not isinstance(finding, dict):
            continue
        point_id = _parse_runtime_point_ref_from_v16_finding(finding)
        norm_point_id = _normalize_point_id(point_id or "")
        if norm_point_id not in no_tg_segments:
            continue
        blob = _finding_text_blob(finding)
        if "no_tg_hms" not in blob and "hms" not in blob and "elektr" not in blob and "lovlighet" not in blob:
            continue
        level = _quality(no_tg_segments[norm_point_id])
        if level == "good":
            finding["deduction_band"] = "Ikke scoretrekk"
            finding["severity"] = "info"
            finding["title"] = finding.get("title") or f"Punkt {norm_point_id}: no-TG/HMS-forhold er beskrevet"
            finding["message"] = "Forholdet er omtalt med forklaring, konsekvens og anbefalt oppfølging. Videre forbedring er ikke påkrevd."
        elif level == "partial":
            finding["deduction_band"] = "Lavt trekk"
            finding["severity"] = "minor"
            if "mangler" in str(finding.get("title") or "").lower():
                finding["title"] = f"Punkt {norm_point_id}: no-TG/HMS-forklaring kan tydeliggjøres"
            finding["message"] = "Forholdet er omtalt, men forklaring, konsekvens eller anbefalt oppfølging kan gjøres tydeligere for kjøper."



_ELECTRICAL_TG_FORBIDDEN_RULE_IDS = {
    "B_TG.el_anlegg_tg_forbudt",
    "B_TG_el_anlegg_tg_forbudt_001",
}


def _point_is_electrical_installation(point: Dict[str, object]) -> bool:
    if not isinstance(point, dict):
        return False
    blob = _normalize_tg3_cost_text(
        " ".join(
            str(point.get(key) or "")
            for key in (
                "point_id",
                "id",
                "canonical_id",
                "title",
                "component_title",
                "effective_span_text",
                "exact_span_text",
                "span_text",
            )
        )
    ).lower()
    return "electrical-installation" in blob or "elektrisk anlegg" in blob or "sikringsskap" in blob


def _point_has_real_tg_value(point: Dict[str, object]) -> bool:
    if not isinstance(point, dict):
        return False
    for key in ("tg", "tg_grade", "tilstandsgrad", "condition_grade"):
        if _normalize_tg_label(point.get(key)):
            return True
    return False


def _has_electrical_installation_with_real_tg(detected_points: List[Dict[str, object]]) -> bool:
    if not isinstance(detected_points, list):
        return False
    for point in detected_points:
        if not isinstance(point, dict) or not _point_is_electrical_installation(point):
            continue
        if bool(point.get("no_tg_hms_point")) and not _point_has_real_tg_value(point):
            continue
        if _point_has_real_tg_value(point):
            return True
    return False


def _is_electrical_tg_forbidden_item(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    rule_refs = item.get("rule_refs")
    rule_ref_blob = " ".join(str(ref) for ref in rule_refs if isinstance(ref, str)) if isinstance(rule_refs, list) else ""
    blob = _normalize_tg3_cost_text(
        " ".join(
            str(item.get(key) or "")
            for key in ("finding_id", "rule_id", "exact_rule_id", "title", "message", "reason", "summary", "details")
        )
        + " "
        + rule_ref_blob
    ).lower()
    return any(rule_id.lower() in blob for rule_id in _ELECTRICAL_TG_FORBIDDEN_RULE_IDS) or (
        "elektrisk anlegg" in blob and "tilstandsgradert" in blob and "ikke tillatt" in blob
    )


def _drop_false_electrical_tg_forbidden_findings(
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
) -> None:
    if not isinstance(analysis_output, dict):
        return
    if _has_electrical_installation_with_real_tg(detected_points):
        return

    for key in (
        "all_findings",
        "top_issues",
        "top_score_drivers",
        "score_drivers",
        "how_to_improve",
        "improvement_suggestions",
        "action_items",
        "recommended_fixes",
    ):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [item for item in items if not _is_electrical_tg_forbidden_item(item)]

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            for key in ("issues", "deductions"):
                items = component.get(key)
                if isinstance(items, list):
                    component[key] = [item for item in items if not _is_electrical_tg_forbidden_item(item)]

    gate = analysis_output.get("gate")
    if isinstance(gate, dict):
        blocked_by = gate.get("blocked_by")
        if isinstance(blocked_by, list):
            gate["blocked_by"] = [
                item for item in blocked_by
                if str(item or "") not in _ELECTRICAL_TG_FORBIDDEN_RULE_IDS
            ]
            gate["blocked_by_count"] = len(gate["blocked_by"])
            gate["blocked_96"] = bool(gate["blocked_by"])
            gate["active"] = bool(gate["blocked_by"])

    breakdown = analysis_output.get("category_breakdown")
    if isinstance(breakdown, list):
        for entry in breakdown:
            if not isinstance(entry, dict):
                continue
            summary = str(entry.get("summary") or "")
            if _is_electrical_tg_forbidden_item({"summary": summary}):
                entry["summary"] = "Ingen scoretrekk i denne kategorien."

def _ensure_electrical_no_tg_hms_findings(
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
) -> None:
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return

    point_lookup: Dict[str, Dict[str, object]] = {}
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        if not bool(point.get("no_tg_hms_point")):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or ""))
        if not point_id:
            continue
        point_lookup[point_id] = point

    if not point_lookup:
        return

    existing_for_point: Dict[str, List[Dict[str, object]]] = {}
    for finding in all_findings:
        if not isinstance(finding, dict):
            continue
        point_id = _normalize_point_id(
            str(
                finding.get("exact_point_id")
                or _parse_runtime_point_ref_from_v16_finding(finding)
                or _parse_point_id_from_v16_finding(finding)
                or ""
            )
        )
        if point_id:
            existing_for_point.setdefault(point_id, []).append(finding)

    for point_id, point in point_lookup.items():
        title = str(point.get("title") or point_id).strip()
        combined_text = _normalize_tg3_cost_text(
            str(point.get("effective_span_text") or point.get("exact_span_text") or point.get("span_text") or "")
        )
        low = combined_text.lower()
        if "elektr" not in low and "sikringsskap" not in low and "samsvarserkl" not in low:
            continue

        has_fire_box_defect = bool(
            re.search(r"(?ix)\bhull\b[^.\n]{0,80}\b(?:d[oø]r|skap)\b[^.\n]{0,80}\bsikringsskap", low)
            or re.search(r"(?ix)\bsikringsskap\b[^.\n]{0,80}\bikke\s+er\s+branntett", low)
            or re.search(r"(?ix)\bbranntett\b", low)
        )
        if not has_fire_box_defect:
            # Missing samsvarserklaering is handled separately by L-SE-01.
            continue

        existing_blob = _normalize_tg3_cost_text(
            " ".join(
                f"{item.get('title', '')} {item.get('message', '')} {item.get('rule_id', '')}"
                for item in existing_for_point.get(point_id, [])
                if isinstance(item, dict)
            )
        ).lower()
        if "elektr" in existing_blob or "sikringsskap" in existing_blob:
            continue

        detail_text = "det er registrert hull i dør til sikringsskapet, slik at skapet ikke fremstår branntett"
        message = f"Punkt {point_id} ({title}): Elektrisk anlegg har forhold som bør følges opp, fordi {detail_text}."
        all_findings.append(
            {
                "finding_id": f"A_NO_TG_HMS_ELEKTRISK_{point_id.replace('.', '_')}",
                "rule_id": "A_NO_TG_HMS_ELEKTRISK",
                "point_id": point_id,
                "exact_point_id": point_id,
                "exact_point_title": title,
                "exact_point_text": str(point.get("effective_span_text") or point.get("exact_span_text") or point.get("span_text") or ""),
                "category": "A",
                "severity": "info",
                "deduction_band": "Ikke scoretrekk",
                "title": f"Punkt {point_id}: elektrisk anlegg bør følges opp",
                "message": message,
                "recommended_fix_text": "Beskriv tydelig hva som er registrert ved sikringsskapet, hvorfor dette er et avvik, og anbefal videre kontroll eller utbedring av registrert installatør.",
                "suggested_rewrite_text": "Det er registrert hull i dør til sikringsskapet, slik at skapet ikke fremstår branntett. Kontroll og videre oppfølging av registrert installatør anbefales.",
                "rewrite_strategy": "no_tg_hms_explanation",
                "evidence_snippets": [str(point.get("effective_span_text") or point.get("exact_span_text") or point.get("span_text") or "")],
            }
        )

def _append_unique_all_finding(analysis_output: Dict[str, object], finding: Dict[str, object]) -> None:
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        all_findings = []
        analysis_output["all_findings"] = all_findings
    candidate_rule = str(finding.get("rule_id") or finding.get("finding_id") or "")
    candidate_point = _normalize_point_id(str(finding.get("point_id") or finding.get("exact_point_id") or ""))
    for existing in all_findings:
        if not isinstance(existing, dict):
            continue
        existing_rule = str(existing.get("rule_id") or existing.get("finding_id") or "")
        if candidate_rule == "E_METHOD.garasje_avvik_uten_arkat" and existing_rule == candidate_rule:
            return
        existing_point = _normalize_point_id(
            str(
                existing.get("point_id")
                or existing.get("exact_point_id")
                or _parse_runtime_point_ref_from_v16_finding(existing)
                or _parse_point_id_from_v16_finding(existing)
                or ""
            )
        )
        if existing_rule == candidate_rule and existing_point == candidate_point:
            return
    all_findings.append(finding)


def _finding_already_present(
    analysis_output: Dict[str, object],
    rule_id: str,
    point_id: str = "",
) -> bool:
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return False
    target_point = _normalize_point_id(point_id)
    for existing in all_findings:
        if not isinstance(existing, dict):
            continue
        existing_rule = str(existing.get("rule_id") or existing.get("finding_id") or "")
        if existing_rule != rule_id:
            continue
        if not target_point:
            return True
        existing_point = _normalize_point_id(
            str(
                existing.get("point_id")
                or existing.get("exact_point_id")
                or _parse_runtime_point_ref_from_v16_finding(existing)
                or _parse_point_id_from_v16_finding(existing)
                or ""
            )
        )
        if existing_point == target_point:
            return True
    return False


def _point_requires_l_se_01(point: Dict[str, object]) -> bool:
    if not isinstance(point, dict):
        return False
    point_text = str(
        point.get("effective_span_text")
        or point.get("exact_span_text")
        or point.get("span_text")
        or ""
    )
    title = str(point.get("title") or point.get("excerpt") or "")
    combined = _normalize_tg3_cost_text(f"{title}\n{point_text}").lower()
    if "samsvarserkl" not in combined:
        return False
    missing_match = re.search(
        r"(?ix)\b(?:ikke\s+(?:fremlagt|frem?lagt|foreligger)|mangler|manglende|ikke\s+samsvar)\b[^.\n]{0,160}\bsamsvarserkl",
        combined,
    )
    if not missing_match:
        return False
    local_window = combined[missing_match.start(): min(len(combined), missing_match.end() + 220)]
    if re.search(
        r"(?ix)\b(?:konsekvens|risiko|fare|sikkerhetsrisiko|usikkerhet|videre\s+kontroll|oppf[oø]lging|dokumentasjonsbehov|utbedringsbehov)\b",
        local_window,
    ):
        return False
    return True

def _report_excerpt(report_text: str, pattern: str, window: int = 260) -> str:
    normalized = _normalize_tg3_cost_text(report_text or "")
    match = re.search(pattern, normalized, re.IGNORECASE)
    if not match:
        return ""
    start = max(0, match.start() - 40)
    end = min(len(normalized), match.end() + window)
    excerpt = normalized[start:end].strip()
    return excerpt


def _report_requires_egenerklaring_missing(report_text: str) -> bool:
    normalized =(report_text or "").lower()
    if "egenerkl" not in normalized:
        return False
    if re.search(
        r"(?ix)\begenerkl[^\n.]{0,120}\b(?:er\s+levert|ble\s+levert|er\s+fremlagt|ble\s+fremlagt|lagt\s+frem)\b",
        normalized,
    ):
        return False
    if re.search(
        r"(?ix)\b(?:dersom|hvis)\s+egenerkl[^\n.]{0,80}\b(?:ikke\s+foreligger|ikke\s+er\s+levert|ikke\s+levert)\b",
        normalized,
    ):
        return False
    if (
        "skal alltid legges frem for rapportansvarlig" in normalized
        and "er ikke levert i forbindelse med oppdraget" not in normalized
        and "ble ikke levert i forbindelse med oppdraget" not in normalized
        and "egenerklaeringsskjema er ikke levert i forbindelse med oppdraget" not in normalized
        and "egenerklæringsskjema er ikke levert i forbindelse med oppdraget" not in normalized
    ):
        return False
    positive_matches = list(
        re.finditer(
            r"(?ix)\begenerkl[^\n.]{0,140}\b(?:er\s+levert|ble\s+levert|foreligger|er\s+fremlagt|fremlagt|lagt\s+frem)\b",
            normalized,
        )
    )
    negative_matches = list(
        re.finditer(
            r"(?ix)\begenerkl[^\n.]{0,120}\b(?:ikke\s+levert|foreligger\s+ikke|ikke\s+foreligger|mangler)\b",
            normalized,
        )
    )
    filtered_negative_matches = []
    for match in negative_matches:
        context_start = max(0, match.start() - 120)
        window = normalized[context_start: min(len(normalized), match.end() + 220)]
        if re.search(r"(?ix)\b(?:dersom|hvis|skal\s+alltid|legges\s+frem\s+for\s+rapportansvarlig)\b", window):
            continue
        if re.search(r"(?ix)\bvil\s+dette\s+komme\s+tydelig\s+frem\b|\bp[aå]\s+en\s+av\s+de\s+siste\s+sidene\b", window):
            continue
        filtered_negative_matches.append(match)
    negative_matches = filtered_negative_matches
    if not negative_matches:
        return False
    last_positive = max((m.start() for m in positive_matches), default=-1)
    last_negative = max((m.start() for m in negative_matches), default=-1)
    if last_positive >= 0 and last_positive >= last_negative:
        return False
    match = negative_matches[-1]
    context_start = max(0, match.start() - 80)
    window = normalized[context_start: min(len(normalized), match.end() + 220)]
    if re.search(r"(?ix)\b(?:dersom|hvis|skal\s+alltid|legges\s+frem\s+for\s+rapportansvarlig)\b", window):
        return False
    if re.search(r"(?ix)\bvil\s+dette\s+komme\s+tydelig\s+frem\b|\bp[aå]\s+en\s+av\s+de\s+siste\s+sidene\b", window):
        return False
    if not re.search(r"(?ix)\b(?:er|ble|ikke\s+er|ikke\s+ble)\b", window):
        return False
    if re.search(r"(?ix)\b(?:konsekvens|betydning\s+for\s+analysen|usikkerhet\s+for\s+kj[oø]per)\b", window):
        return False
    return True


def _report_requires_l_rk_01(report_text: str, report_date: str) -> bool:
    if not _legality_rule_is_active("L-RK-01", report_date):
        return False
    normalized = _normalize_tg3_cost_text(report_text or "").lower()
    match = re.search(
        r"(?ix)\b(?:innvendige\s+)?rekkverk(?:\s+og\s+h[aå]ndrekker?)?\b[^\n.]{0,160}\bikke\s+i\s+henhold\s+til",
        normalized,
    )
    if not match:
        return False
    window = normalized[match.start(): min(len(normalized), match.end() + 220)]
    if re.search(
        r"(?ix)\b(?:konsekvens|sikkerhetsrisiko|fare\s+for\s+fall|kan\s+medf[oø]re|kan\s+f[oø]re\s+til|kj[oø]per\s+m[aå]\s+p[aå]regne)\b",
        window,
    ):
        return False
    return True


def _report_l_se_01_excerpt(report_text: str) -> str:
    normalized = _normalize_tg3_cost_text(report_text or "")
    match = re.search(
        r"(?ix)\bdet\s+er\s+ikke\s+fremlagt\s+samsvarserkl[æa]ring\b[^.]{0,260}",
        normalized,
    )
    if not match:
        match = re.search(
            r"(?ix)\b(?:ikke\s+fremlagt|mangler|manglende)\s+samsvarserkl[æa]ring\b[^.]{0,260}",
            normalized,
        )
    if not match:
        return ""
    return match.group(0).strip()


def _report_requires_l_se_01(report_text: str, report_date: str) -> bool:
    if not _legality_rule_is_active("L-SE-01", report_date):
        return False
    excerpt = _report_l_se_01_excerpt(report_text)
    if not excerpt:
        return False
    low = excerpt.lower()
    if re.search(
        r"(?ix)\b(?:konsekvens|risiko|fare|sikkerhetsrisiko|usikkerhet|videre\s+kontroll|oppf[oø]lging|dokumentasjonsbehov|utbedringsbehov)\b",
        low,
    ):
        return False
    return True


_LEGAL_CONSEQUENCE_EXPLANATION_RE = re.compile(
    r"(?ix)\b(?:konsekvens|betydning|søknadsplikt|s[øo]knadspliktig|kommunal\s+oppf[øo]lging|"
    r"p[aå]legg|tilbakef[øo]ring|godkjenning\s+er\s+ikke\s+garantert|økonomisk\s+risiko|"
    r"okonomisk\s+risiko|kostnad(?:er)?|ulovlig(?:het)?|rettslig)\b"
)


def _legal_window_has_consequence_explanation(text: str) -> bool:
    return bool(_LEGAL_CONSEQUENCE_EXPLANATION_RE.search(_normalize_tg3_cost_text(text or "").lower()))


def _report_l_bu_01_excerpt(report_text: str) -> str:
    normalized = _normalize_tg3_cost_text(report_text or "")
    patterns = (
        r"(?ix)\bboligen\s+er\s+ikke\s+byggemeldt\s+med\s+kjeller\b[^.]{0,260}",
        r"(?ix)\bs[øo]knad\s*/\s*bruksendring\s+i\s+etterkant\s+er\s+ikke\s+utf[øo]rt\b[^.]{0,260}",
        r"(?ix)\b(?:bruksendring|ikke\s+godkjent\s+til\s+varig\s+opphold|ikke\s+godkjent)\b[^.]{0,260}",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return match.group(0).strip()
    return ""


def _report_requires_l_bu_01(report_text: str, report_date: str) -> bool:
    if not _legality_rule_is_active("L-BU-01", report_date):
        return False
    excerpt = _report_l_bu_01_excerpt(report_text)
    if not excerpt:
        return False
    return not _legal_window_has_consequence_explanation(excerpt)


def _report_l_av_01_excerpt(report_text: str) -> str:
    normalized = _normalize_tg3_cost_text(report_text or "")
    patterns = (
        r"(?ix)\bbyggemeldt(?:e)?\s+tegninger\s+stemmer\s+ikke\s+med\s+planl[øo]sning\b[^.]{0,260}",
        r"(?ix)\b(?:godkjente|byggemeldt(?:e)?)\s+tegninger\b[^.]{0,140}\bstemmer\s+ikke\b[^.]{0,220}",
        r"(?ix)\b(?:avvik\s+fra\s+tegninger|rominndeling\s+stemmer\s+ikke|stemmer\s+ikke\s+med\s+tegning)\b[^.]{0,260}",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return match.group(0).strip()
    return ""


def _report_requires_l_av_01(report_text: str, report_date: str) -> bool:
    if not _legality_rule_is_active("L-AV-01", report_date):
        return False
    excerpt = _report_l_av_01_excerpt(report_text)
    if not excerpt:
        return False
    return not _legal_window_has_consequence_explanation(excerpt)


def _report_freestanding_finding(report_text: str) -> Tuple[str, str]:
    normalized = _normalize_tg3_cost_text(report_text or "")
    lowered = normalized.lower()
    sentence_chunks = [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+", normalized) if chunk.strip()]
    for idx, sentence in enumerate(sentence_chunks):
        low = sentence.lower()
        if not re.search(r"(?ix)\b(?:garasje|frittstående garasje)\b", low):
            continue
        window = " ".join(sentence_chunks[idx: min(len(sentence_chunks), idx + 4)]).strip()
        window_low = window.lower()
        if not DEVIATION_KEYWORD_RE.search(window_low):
            continue
        if re.search(r"(?ix)\bTG\s*(?:0|1|2|3|iu)\b", window_low):
            continue
        return "P05F_GARAGE", window
    for idx, sentence in enumerate(sentence_chunks):
        low = sentence.lower()
        if not re.search(r"(?ix)\b(?:uthus|bod|naust|anneks)\b", low):
            continue
        window = " ".join(sentence_chunks[idx: min(len(sentence_chunks), idx + 4)]).strip()
        window_low = window.lower()
        if HABITABLE_ANNEX_RE.search(window_low):
            continue
        if not DEVIATION_KEYWORD_RE.search(window_low):
            continue
        if re.search(r"(?ix)\bTG\s*(?:0|1|2|3|iu)\b", window_low):
            continue
        return "P05G_STORAGE", window
    patterns = (
        ("P05F_GARAGE", r"(?is)\b(?:garasje(?:\s*/\s*uthus)?|frittstående garasje)\b.{0,900}"),
        ("P05G_STORAGE", r"(?is)\b(?:uthus|bod|naust|anneks)\b.{0,900}"),
    )
    for point_id, pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        block = match.group(0)
        block_low = block.lower()
        if point_id == "P05G_STORAGE" and HABITABLE_ANNEX_RE.search(block_low):
            continue
        if not DEVIATION_KEYWORD_RE.search(block_low):
            continue
        if re.search(r"(?ix)\bTG\s*(?:0|1|2|3|iu)\b", block_low):
            continue
        return point_id, block.strip()
    garage_match = re.search(
        r"(?is)\bgarasje\b.{0,700}(?:fall\s+inn\s+mot|terreng|overflatevann|drenering|knotteplast|topplist|utbedre|anbefales)",
        normalized,
    )
    if garage_match and not re.search(r"(?ix)\bTG\s*(?:0|1|2|3|iu)\b", garage_match.group(0)):
        return "P05F_GARAGE", garage_match.group(0).strip()
    if "garasje" in lowered and DEVIATION_KEYWORD_RE.search(lowered) and "tg" not in lowered:
        return "P05F_GARAGE", _report_excerpt(report_text, r"(?i)\bgarasje\b")
    return "", ""


def _canonical_backstop_point_id(point_title: str, point_text: str, fallback_point_id: str) -> str:
    blob = _normalize_tg3_cost_text(f"{point_title}\n{point_text}").lower()
    if re.search(r"(?ix)\b(?:underetasje|kjeller|rom\s+under\s+terreng)\b", blob):
        if "fuktmå" in blob or "fuktma" in blob:
            return "P06D_BELOW_GRADE_MOISTURE"
        if "ventilasj" in blob:
            return "P06C_BELOW_GRADE_VENTILATION"
        if re.search(r"(?ix)\b(?:gulv|plate|dekke)\b", blob):
            return "P06B_BELOW_GRADE_FLOORS"
        return "P06A_BELOW_GRADE_WALLS"
    if "samsvarserkl" in blob or "elektr" in blob or "sikringsskap" in blob:
        return "P09F_ELECTRICAL_INSTALLATION"
    if "garasje" in blob:
        return "P05F_GARAGE"
    if re.search(r"(?ix)\b(?:uthus|bod|naust|anneks)\b", blob):
        return "P05G_STORAGE"
    return fallback_point_id


def _drop_unexpected_jargon_findings(analysis_output: Dict[str, object]) -> None:
    def _is_jargon_item(item: Dict[str, object]) -> bool:
        blob = _normalize_tg3_cost_text(
            f"{item.get('rule_id', '')} {item.get('finding_id', '')} {item.get('title', '')} "
            f"{item.get('message', '')} {item.get('reason', '')}"
        ).lower()
        return "fagspråk uten forklaring" in blob or "faguttrykk" in blob

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            issues = component.get("issues")
            if isinstance(issues, list):
                component["issues"] = [
                    issue for issue in issues
                    if not (isinstance(issue, dict) and _is_jargon_item(issue))
                ]
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                component["deductions"] = [
                    deduction for deduction in deductions
                    if not (isinstance(deduction, dict) and _is_jargon_item(deduction))
                ]

    for key in ("all_findings", "top_issues", "top_score_drivers"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [
                item for item in items
                if not (isinstance(item, dict) and _is_jargon_item(item))
            ]


def _ensure_generic_backstop_findings(
    report_text: str,
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
) -> None:
    context = _extract_report_regime_context(report_text)
    report_date = context.get("report_date") or ""
    if (
        not _finding_already_present(analysis_output, "E_METHOD.egenerklaring_missing", "P12A_OWNER_INFORMATION")
        and _report_requires_egenerklaring_missing(report_text)
    ):
        excerpt = _report_excerpt(report_text, r"(?i)\begenerkl")
        _append_unique_all_finding(
            analysis_output,
            {
                "finding_id": "E_METHOD_egenerklaring_missing_P12A_OWNER_INFORMATION",
                "rule_id": "E_METHOD.egenerklaring_missing",
                "point_id": "",
                "exact_point_id": "",
                "exact_point_title": "Opplysninger fra eier / egenerklæring",
                "exact_point_text": excerpt,
                "category": "E",
                "severity": "info",
                "deduction_band": "Ikke scoretrekk",
                "title": "Egenerklæring ikke levert",
                "message": "Rapporten opplyser at egenerklæring ikke er levert. Dette skal ikke gi scoretrekk, men bør fremgå som en faglig merknad fordi analysegrunnlaget blir svakere når forhold bare eier kjenner til kan være utelatt.",
                "recommended_fix_text": "Legg inn en kort opplysning om at rapporten er ferdigstilt uten egenerklæring, og forklar hvilken metodisk usikkerhet og hvilket ansvar dette kan medføre for takstmannen.",
                "suggested_rewrite_text": "Egenerklæring er ikke levert i forbindelse med oppdraget. Dette er ikke et forskriftsavvik og skal ikke gi scoretrekk, men det svekker analysegrunnlaget fordi forhold bare eier kjenner til kan være utelatt. NS 3600:2018 pkt. 5 og 9 krever egenerklæring før analysen, og NS 3600:2025 pkt. 8 c) krever at den foreligger før rapporten ferdigstilles. Uten egenerklæring øker risikoen for at viktige opplysninger er oversett, og takstmannen eksponeres for ansvar.",
                "evidence_snippets": [excerpt] if excerpt else [],
                "gate_effect": {"blocks_96_gate": False, "caps_total_score_to": None},
            },
        )
    if (
        not _finding_already_present(analysis_output, "E_METHOD.vinterhage_not_assessed_ns3600", "P12A_OWNER_INFORMATION")
        and _report_requires_vinterhage_ns3600_note(report_text)
    ):
        excerpt = _report_vinterhage_ns3600_excerpt(report_text)
        _append_unique_all_finding(
            analysis_output,
            {
                "finding_id": "E_METHOD_vinterhage_not_assessed_ns3600_P12A_OWNER_INFORMATION",
                "rule_id": "E_METHOD.vinterhage_not_assessed_ns3600",
                "point_id": "",
                "exact_point_id": "",
                "exact_point_title": "Vinterhage",
                "exact_point_text": excerpt,
                "category": "E",
                "severity": "info",
                "deduction_band": "Ikke scoretrekk",
                "title": "Vinterhage er ikke vurdert etter NS 3600",
                "message": "Rapporten opplyser at vinterhagen ikke er tilstandsvurdert etter forskrift til avhendingslova og NS 3600. Dette er vesentlig informasjon for kjøper og bør fremgå tydelig som en faglig merknad.",
                "recommended_fix_text": "Legg inn en tydelig merknad om at vinterhagen ikke er omfattet av tilstandsvurderingen etter forskrift til avhendingslova og NS 3600, slik at kjøper forstår avgrensningen i rapporten.",
                "suggested_rewrite_text": "Vinterhagen er ikke tilstandsvurdert etter forskrift til avhendingslova og NS 3600. Kjøper må derfor være oppmerksom på at denne bygningen ikke er omfattet av den bygningssakkyndige vurderingen i rapporten.",
                "evidence_snippets": [excerpt] if excerpt else [],
                "gate_effect": {"blocks_96_gate": False, "caps_total_score_to": None},
            },
        )
    is_fremtind_format = _report_text_suggests_compressed_mixed_format(report_text or "")
    railings_point_id = "P11G_SAFETY_RAILINGS" if is_fremtind_format else ""
    railings_finding_id = "L_RK_01_P11G_SAFETY_RAILINGS" if is_fremtind_format else "L_RK_01_REKKVERK"
    if (
        not _finding_already_present(analysis_output, "L-RK-01", railings_point_id)
        and _report_requires_l_rk_01(report_text, report_date)
    ):
        excerpt = _report_excerpt(
            report_text,
            r"(?i)\binnvendige\s+rekkverk(?:\s+og\s+h[aå]ndrekker?)?\b[^\n.]{0,220}",
        ) or _report_excerpt(report_text, r"(?i)\brekkverk\b")
        _append_unique_all_finding(
            analysis_output,
            {
                "finding_id": railings_finding_id,
                "rule_id": "L-RK-01",
                "point_id": railings_point_id,
                "exact_point_id": railings_point_id,
                "exact_point_title": "Rekkverk og håndrekker",
                "exact_point_text": excerpt,
                "category": "F",
                "severity": "major",
                "deduction_band": "Lavt trekk",
                "title": "Rekkverk eller håndrekker ikke iht. forskrift uten konsekvens for kjøper",
                "message": "Rekkverk eller håndrekker som ikke er i henhold til forskriftskrav er omtalt uten at sikkerhetskonsekvensen for kjøper er forklart.",
                "recommended_fix_text": "Legg til hva avviket betyr for kjøper i praksis, for eksempel sikkerhetsrisiko og forventet behov for oppfølging eller utbedring.",
                "suggested_rewrite_text": "Innvendige rekkverk og håndrekker er ikke i henhold til dagens forskrifter. Dette utgjør en sikkerhetsrisiko og kjøper må påregne behov for utbedring. Forholdet skal beskrives som et HMS-avvik uten tilstandsgrad.",
                "evidence_snippets": [excerpt] if excerpt else [],
            },
        )
    if (
        _report_requires_l_se_01(report_text, report_date)
    ):
        excerpt = _report_l_se_01_excerpt(report_text)
        _append_unique_all_finding(
            analysis_output,
            {
                "finding_id": "L_SE_01_P09F_ELECTRICAL_INSTALLATION",
                "rule_id": "L-SE-01",
                "point_id": "P09F_ELECTRICAL_INSTALLATION",
                "exact_point_id": "P09F_ELECTRICAL_INSTALLATION",
                "exact_point_title": "Elektrisk anlegg og samsvarserklæring",
                "exact_point_text": excerpt,
                "category": "F",
                "severity": "major",
                "deduction_band": "Lavt trekk",
                "title": "Manglende samsvarserklæring for elektrisk arbeid uten konsekvens for kjøper",
                "message": "Rapporten opplyser om manglende samsvarserklæring for deler av det elektriske anlegget, men forklarer ikke den praktiske konsekvensen for kjøper.",
                "recommended_fix_text": "Forklar hva manglende samsvarserklæring betyr for kjøper i praksis, for eksempel usikkerhet om forskriftsmessig utførelse og behov for videre kontroll eller dokumentasjon.",
                "suggested_rewrite_text": "Det er ikke fremlagt samsvarserklæring for deler av det elektriske anlegget i tilbygg og garasje. Dette gir usikkerhet om arbeidet er forskriftsmessig utført, og kjøper bør påregne videre kontroll og mulig dokumentasjons- eller utbedringsbehov.",
                "evidence_snippets": [excerpt] if excerpt else [],
            },
        )
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        point_id = str(point.get("point_id") or "")
        point_title = str(point.get("title") or point.get("excerpt") or "")
        point_text = str(
            point.get("effective_span_text")
            or point.get("exact_span_text")
            or point.get("span_text")
            or ""
        ).strip()
        if not point_text:
            continue
        canonical_point_id = _canonical_backstop_point_id(point_title, point_text, point_id)

        if (
            _legality_rule_is_active("L-SE-01", report_date)
            and not _finding_already_present(analysis_output, "L-SE-01", canonical_point_id)
            and _point_requires_l_se_01(point)
        ):
            _append_unique_all_finding(
                analysis_output,
                {
                    "finding_id": f"L_SE_01_{_normalize_point_id(canonical_point_id).replace('.', '_') or 'global'}",
                    "rule_id": "L-SE-01",
                    "point_id": canonical_point_id,
                    "exact_point_id": canonical_point_id,
                    "exact_point_title": point_title,
                    "exact_point_text": point_text,
                    "category": "F",
                    "severity": "major",
                    "deduction_band": "Lavt trekk",
                    "title": "Manglende samsvarserklæring for elektrisk arbeid uten konsekvens for kjøper",
                    "message": "Rapporten opplyser om manglende samsvarserklæring for deler av det elektriske anlegget, men forklarer ikke den praktiske konsekvensen for kjøper.",
                    "recommended_fix_text": "Forklar hva manglende samsvarserklæring betyr for kjøper i praksis, for eksempel usikkerhet om forskriftsmessig utførelse og behov for videre kontroll eller dokumentasjon.",
                    "suggested_rewrite_text": "Det er ikke fremlagt samsvarserklæring for deler av det elektriske anlegget. Dette gir usikkerhet om arbeidet er forskriftsmessig utført, og kjøper bør påregne videre kontroll og mulig dokumentasjons- eller utbedringsbehov.",
                    "evidence_snippets": [point_text],
                },
            )

        if (
            not _finding_already_present(analysis_output, "E_METHOD.garasje_avvik_uten_arkat", canonical_point_id)
            and bool(point.get("garage_avvik_uten_arkat"))
        ):
            missing_arkat = ", ".join(point.get("freestanding_missing_arkat_keys") or []) or "årsak, risiko, konsekvens eller anbefalt tiltak"
            _append_unique_all_finding(
                analysis_output,
                {
                    "finding_id": f"E_METHOD_garasje_avvik_uten_arkat_{_normalize_point_id(canonical_point_id).replace('.', '_') or 'global'}",
                    "rule_id": "E_METHOD.garasje_avvik_uten_arkat",
                    "point_id": canonical_point_id,
                    "exact_point_id": canonical_point_id,
                    "exact_point_title": _user_visible_point_label(canonical_point_id, point),
                    "exact_point_text": point_text,
                    "category": "E",
                    "severity": "major",
                    "deduction_band": "Middels trekk",
                    "title": "Avvik i garasje/uthus/naust mangler full ARKAT",
                    "message": f"Rapporten beskriver et tydelig avvik i frittstående bygg uten varig opphold, men mangler full ARKAT. Manglende elementer: {missing_arkat}. TG er ikke påkrevd, men avviket skal beskrives med årsak, risiko, konsekvens og anbefalt tiltak.",
                    "recommended_fix_text": "Beskriv avviket med full ARKAT: hva som er observert, hvilken risiko det gir, hvilken praktisk konsekvens det har for kjøper, og hvilket tiltak som anbefales. TG er ikke nødvendig for slike bygg.",
                    "suggested_rewrite_text": "Det er registrert et avvik i garasje/uthus/naust. Forholdet bør beskrives med full ARKAT: hva som er observert, hvilken risiko det gir videre, hvilken konsekvens det har for kjøper, og hvilket tiltak som anbefales. TG er ikke påkrevd for dette bygget.",
                    "evidence_snippets": [point_text],
                    "gate_effect": {"blocks_96_gate": False, "caps_total_score_to": None},
                },
            )

        if (
            not _finding_already_present(analysis_output, "E_METHOD.garasje_tg_uten_full_arkat", canonical_point_id)
            and bool(point.get("garage_tg_uten_full_arkat"))
        ):
            missing_arkat = ", ".join(point.get("freestanding_missing_arkat_keys") or []) or "årsak, risiko, konsekvens eller anbefalt tiltak"
            _append_unique_all_finding(
                analysis_output,
                {
                    "finding_id": f"E_METHOD_garasje_tg_uten_full_arkat_{_normalize_point_id(canonical_point_id).replace('.', '_') or 'global'}",
                    "rule_id": "E_METHOD.garasje_tg_uten_full_arkat",
                    "point_id": canonical_point_id,
                    "exact_point_id": canonical_point_id,
                    "exact_point_title": _user_visible_point_label(canonical_point_id, point),
                    "exact_point_text": point_text,
                    "category": "E",
                    "severity": "major",
                    "deduction_band": "Middels trekk",
                    "title": "TG satt i garasje/uthus/naust uten full ARKAT",
                    "message": f"Rapporten har satt TG på frittstående bygg uten varig opphold, men ARKAT er ikke komplett. Manglende elementer: {missing_arkat}. Når TG brukes, må full ARKAT følge med.",
                    "recommended_fix_text": "Når TG settes på garasje, uthus eller naust, må avviket beskrives med full ARKAT. Alternativt kan TG tas ut og forholdet beskrives som observasjon med full ARKAT uten TG.",
                    "suggested_rewrite_text": "Det er satt TG på dette forholdet i garasje/uthus/naust. Da må punktet suppleres med full ARKAT: hva som er observert, hvilken risiko det gir, hvilken konsekvens det har for kjøper, og hvilket tiltak som anbefales.",
                    "evidence_snippets": [point_text],
                    "gate_effect": {"blocks_96_gate": False, "caps_total_score_to": None},
                },
            )

        if (
            not _finding_already_present(analysis_output, "E_METHOD.anneks_varig_opphold_mangler_tg", canonical_point_id)
            and bool(point.get("habitable_annex_without_tg"))
        ):
            _append_unique_all_finding(
                analysis_output,
                {
                    "finding_id": f"E_METHOD_anneks_varig_opphold_mangler_tg_{_normalize_point_id(canonical_point_id).replace('.', '_') or 'global'}",
                    "rule_id": "E_METHOD.anneks_varig_opphold_mangler_tg",
                    "point_id": canonical_point_id,
                    "exact_point_id": canonical_point_id,
                    "exact_point_title": _user_visible_point_label(canonical_point_id, point),
                    "exact_point_text": point_text,
                    "category": "E",
                    "severity": "major",
                    "deduction_band": "Høyt trekk",
                    "title": "Anneks med varig opphold mangler TG-basert tilstandsanalyse",
                    "message": "Rapporten omtaler et anneks godkjent for varig opphold uten tilstandsanalyse på bygningsdelsnivå med TG. Slike anneks skal behandles som egen boenhet og ha full tilstandsanalyse med TG.",
                    "recommended_fix_text": "Beskriv annekset som egen boenhet med tilstandsanalyse på bygningsdelsnivå og bruk TG der dette kreves, på samme måte som for boligen.",
                    "suggested_rewrite_text": "Anneks godkjent for varig opphold skal behandles som egen boenhet og ha full tilstandsanalyse på bygningsdelsnivå med TG. Rapporten bør derfor suppleres med slik vurdering for annekset.",
                    "evidence_snippets": [point_text],
                    "gate_effect": {"blocks_96_gate": True, "caps_total_score_to": 95},
                },
            )
    if not _finding_already_present(analysis_output, "E_METHOD.garasje_avvik_uten_arkat"):
        fallback_point_id, fallback_text = _report_freestanding_finding(report_text)
        if fallback_point_id and fallback_text:
            fallback_title = _user_visible_point_label(fallback_point_id, {})
            _append_unique_all_finding(
                analysis_output,
                {
                    "finding_id": f"E_METHOD_garasje_avvik_uten_arkat_{fallback_point_id}",
                    "rule_id": "E_METHOD.garasje_avvik_uten_arkat",
                    "point_id": fallback_point_id,
                    "exact_point_id": fallback_point_id,
                    "exact_point_title": fallback_title,
                    "exact_point_text": fallback_text,
                    "category": "E",
                    "severity": "major",
                    "deduction_band": "Middels trekk",
                    "title": "Avvik i garasje/uthus/naust mangler full ARKAT",
                    "message": "Rapporten beskriver et tydelig avvik i frittstående bygg uten varig opphold, men ARKAT er ikke komplett. TG er ikke påkrevd, men avviket skal beskrives med årsak, risiko, konsekvens og anbefalt tiltak.",
                    "recommended_fix_text": "Beskriv avviket med full ARKAT: hva som er observert, hvilken risiko det gir, hvilken praktisk konsekvens det har for kjøper, og hvilket tiltak som anbefales.",
                    "suggested_rewrite_text": "Det er registrert et avvik i garasje/uthus/naust. Forholdet bør beskrives med full ARKAT: hva som er observert, hvilken risiko det gir videre, hvilken konsekvens det har for kjøper, og hvilket tiltak som anbefales.",
                    "evidence_snippets": [fallback_text],
                    "gate_effect": {"blocks_96_gate": False, "caps_total_score_to": None},
                },
            )


def _is_garage_method_rule_id(rule_id: str) -> bool:
    return str(rule_id or "").strip() in {
        "E_METHOD.garasje_avvik_uten_arkat",
        "E_METHOD.garasje_tg_uten_full_arkat",
    }


def _freestanding_method_finding_has_standalone_scope(item: Dict[str, object]) -> bool:
    title = _normalize_tg3_cost_text(
        f"{item.get('exact_point_title', '')} {item.get('title', '')}"
    ).strip().lower()
    text = _normalize_tg3_cost_text(
        f"{item.get('exact_point_text', '')} {' '.join(item.get('evidence_snippets') or [])}"
    ).strip().lower()
    ids = _normalize_tg3_cost_text(
        f"{item.get('point_id', '')} {item.get('exact_point_id', '')} {item.get('finding_id', '')}"
    ).strip().lower()
    title_is_pcode_only = bool(re.fullmatch(r"p\d{2}[a-z]_[a-z0-9_]+", title or ""))
    title_has_standalone_scope = bool(
        not title_is_pcode_only
        and re.search(r"(?ix)^(?:tg\s*(?:0|1|2|3|iu)\s*)?(?:frittst[aå]ende\s+)?(?:garasje|uthus|naust|bod|anneks)\b", title)
    )
    text_has_standalone_heading = bool(
        re.search(r"(?ix)^(?:tg\s*(?:0|1|2|3|iu)\s*)?(?:frittst[aå]ende\s+)?(?:garasje|uthus|naust|anneks)\b", text)
        or re.search(r"(?ix)\bfrittst[aå]ende\s+(?:garasje|uthus|naust|anneks)\b", text)
    )
    integrated_context = bool(
        re.search(
            r"(?ix)\b(?:underetasje|u\.etg|vaskerom|v[aå]trom|bad|membran|tettesjikt|sluk|kj[øo]kken|thermomur\s+i\s+garasje)\b",
            f"{title} {text}",
        )
        or re.search(r"(?ix)\bhulltaking\b.{0,100}\(\s*bod\s*\)", text)
    )
    if integrated_context and not title_has_standalone_scope:
        return False
    if title_is_pcode_only and not text_has_standalone_heading:
        return False
    if not (title_has_standalone_scope or text_has_standalone_heading):
        return False
    # A bare P05 id is not enough in BMTF/unlabeled reports; require visible standalone wording.
    if re.search(r"\bp05[fg]_", ids) and not (title_has_standalone_scope or text_has_standalone_heading):
        return False
    return True


def _drop_false_freestanding_garage_findings(analysis_output: Dict[str, object]) -> None:
    def _keep_item(item: object) -> bool:
        if not isinstance(item, dict):
            return True
        rule_id = str(item.get("rule_id") or "")
        finding_id = str(item.get("finding_id") or "")
        if not (_is_garage_method_rule_id(rule_id) or "E_METHOD_garasje_" in finding_id):
            return True
        return _freestanding_method_finding_has_standalone_scope(item)

    for key in ("all_findings", "top_issues"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [item for item in items if _keep_item(item)]

    drivers = analysis_output.get("top_score_drivers")
    if isinstance(drivers, list):
        filtered_drivers = []
        for driver in drivers:
            if not isinstance(driver, dict):
                filtered_drivers.append(driver)
                continue
            refs = driver.get("rule_refs") or []
            if any(_is_garage_method_rule_id(str(ref)) for ref in refs if ref):
                continue
            filtered_drivers.append(driver)
        analysis_output["top_score_drivers"] = filtered_drivers

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                component["deductions"] = [
                    deduction for deduction in deductions
                    if not (isinstance(deduction, dict) and _is_garage_method_rule_id(str(deduction.get("rule_id") or "")))
                ]
            issues = component.get("issues")
            if isinstance(issues, list):
                component["issues"] = [
                    issue for issue in issues
                    if not (isinstance(issue, dict) and _is_garage_method_rule_id(str(issue.get("rule_id") or "")))
                ]


def _sync_gate_from_all_findings(analysis_output: Dict[str, object]) -> None:
    blockers: List[Dict[str, object]] = []
    seen: set = set()
    for finding in analysis_output.get("all_findings", []):
        if not isinstance(finding, dict):
            continue
        gate_effect = finding.get("gate_effect")
        if not isinstance(gate_effect, dict) or not gate_effect.get("blocks_96_gate"):
            continue
        blocker_id = str(finding.get("finding_id") or finding.get("rule_id") or "").strip()
        if not blocker_id:
            continue
        if blocker_id in seen:
            continue
        seen.add(blocker_id)
        blockers.append(finding)
    gate = analysis_output.get("gate")
    if not isinstance(gate, dict):
        gate = {}
    blocked = bool(blockers)
    blocked_by = [str(item.get("finding_id") or item.get("rule_id") or "") for item in blockers]
    cap_values = [
        int((item.get("gate_effect") or {}).get("caps_total_score_to"))
        for item in blockers
        if isinstance(item.get("gate_effect"), dict)
        and isinstance((item.get("gate_effect") or {}).get("caps_total_score_to"), (int, float))
    ]
    max_score_if_blocked = min(cap_values) if cap_values else (95 if blocked else None)
    gate["active"] = blocked
    gate["blocked_96"] = blocked
    gate["blocked_by"] = blocked_by
    gate["blocked_by_count"] = len(blocked_by)
    gate["max_score_if_blocked"] = max_score_if_blocked
    gate["message"] = (
        "Rapporten kan ikke oppnå over 95 % før gate-avvik er rettet."
        if blocked
        else "Ingen gate-blokkerende avvik funnet"
    )
    analysis_output["gate"] = gate
    if blocked and isinstance(max_score_if_blocked, int):
        score_total = analysis_output.get("score_total")
        if isinstance(score_total, (int, float)) and int(score_total) > max_score_if_blocked:
            analysis_output["score_total"] = max_score_if_blocked


def _ensure_tgiu_deductions_visible_in_all_findings(analysis_output: Dict[str, object]) -> None:
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        all_findings = []
        analysis_output["all_findings"] = all_findings

    existing_keys = {
        (
            str(item.get("finding_id") or ""),
            str(item.get("rule_id") or ""),
            str(item.get("exact_point_id") or item.get("point_id") or ""),
        )
        for item in all_findings
        if isinstance(item, dict)
    }

    def _append_tgiu_finding(point_id: str, point_title: str, exact_text: str, error_type: str, reason: str, points: object = None) -> None:
        rule_suffix = re.sub(r"[^A-Z0-9_]+", "_", str(error_type or "TGIU_FINDING").upper()).strip("_") or "TGIU_FINDING"
        rule_id = f"C_TGIU.{rule_suffix}"
        finding_id = f"C_TGIU_{point_id.replace('.', '_')}_{rule_suffix}" if point_id else f"C_TGIU_{rule_suffix}"
        key = (finding_id, rule_id, point_id)
        if key in existing_keys:
            return
        band = "Middels trekk"
        if isinstance(points, (int, float)) and points <= 1:
            band = "Lavt trekk"
        all_findings.append(
            {
                "finding_id": finding_id,
                "rule_id": rule_id,
                "point_id": point_id,
                "exact_point_id": point_id,
                "exact_point_title": point_title or point_id,
                "exact_point_text": exact_text,
                "category": "C",
                "severity": "minor",
                "deduction_band": band,
                "title": f"Punkt {point_id}: {rule_suffix}" if point_id else rule_suffix,
                "message": reason or f"TGIU finding: {rule_suffix}",
                "recommended_fix_text": "Oppdater TGIU-begrunnelse og anbefaling i punktet.",
                "suggested_rewrite_text": reason or f"TGIU finding: {rule_suffix}",
                "rewrite_strategy": "arkat_tgiu_alignment",
                "evidence_snippets": [exact_text] if exact_text else [],
                "public_visibility": "internal",
            }
        )
        existing_keys.add(key)

    point_lookup: Dict[str, Dict[str, object]] = {}
    pipeline = analysis_output.get("arkat_semantic_pipeline")
    if isinstance(pipeline, dict):
        for point in pipeline.get("points", []) or []:
            if isinstance(point, dict):
                point_lookup[str(point.get("point_id") or "").strip()] = point
                evaluation = point.get("evaluation") if isinstance(point.get("evaluation"), dict) else {}
                tgiu_findings = (evaluation.get("tgiu_findings") or {}).get("findings") if isinstance(evaluation.get("tgiu_findings"), dict) else []
                for finding in tgiu_findings if isinstance(tgiu_findings, list) else []:
                    if not isinstance(finding, dict):
                        continue
                    _append_tgiu_finding(
                        str(point.get("point_id") or "").strip(),
                        str(point.get("point_title") or point.get("title") or point.get("point_id") or "").strip(),
                        str(point.get("raw_point_text") or ""),
                        str(finding.get("error_type") or "").strip(),
                        str(finding.get("explanation") or "").strip(),
                    )

    for component in analysis_output.get("components", []) or []:
        if not isinstance(component, dict):
            continue
        point_id = str(component.get("component_id") or component.get("point_id") or "").strip()
        source_point = point_lookup.get(point_id) or {}
        point_title = str(component.get("component_title") or component.get("location") or source_point.get("point_title") or source_point.get("title") or point_id).strip()
        for deduction in component.get("deductions", []) or []:
            if not isinstance(deduction, dict):
                continue
            rule_id = str(deduction.get("rule_id") or "").strip()
            if not rule_id.startswith("C_TGIU."):
                continue
            exact_text = str(source_point.get("raw_point_text") or "")
            if not exact_text:
                evidence = deduction.get("evidence")
                if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
                    exact_text = str(evidence[0].get("snippet") or "")
            _append_tgiu_finding(
                point_id,
                point_title,
                exact_text,
                rule_id.split(".", 1)[-1],
                str(deduction.get("reason") or "").strip(),
                deduction.get("points"),
            )


def _category_band_from_deduction(deduction: int) -> str:
    if deduction <= 0:
        return "Ikke scoretrekk"
    if deduction <= 3:
        return "Lavt trekk"
    if deduction <= 8:
        return "Middels trekk"
    return "Høyt trekk"


def _dommer_b_missing_action_state(analysis_output: Dict[str, object]) -> Dict[str, bool]:
    pipeline = analysis_output.get("arkat_semantic_pipeline")
    points = pipeline.get("points") if isinstance(pipeline, dict) else []
    state = {"tg2_missing": False, "tg3_missing": False}
    if not isinstance(points, list):
        return state
    for point in points:
        if not isinstance(point, dict):
            continue
        tg = str(point.get("tg_grade") or "").strip().upper()
        if tg not in {"TG2", "TG3"}:
            continue
        result = (((point.get("evaluation") or {}).get("field_results") or {}).get("anbefalt_tiltak") or {})
        status = str(result.get("status") or "").strip().upper() if isinstance(result, dict) else ""
        if status not in {"MISSING", "WRONG"}:
            continue
        if tg == "TG2":
            state["tg2_missing"] = True
        elif tg == "TG3":
            state["tg3_missing"] = True
    return state


def _dommer_b_missing_consequence_points(analysis_output: Dict[str, object]) -> set:
    out = set()
    for point_id, point in _semantic_arkat_points_by_id(analysis_output).items():
        evaluation = point.get("evaluation") if isinstance(point, dict) else None
        field_results = evaluation.get("field_results") if isinstance(evaluation, dict) else None
        consequence = field_results.get("konsekvens") if isinstance(field_results, dict) else None
        status = str(consequence.get("status") or "").strip().upper() if isinstance(consequence, dict) else ""
        if status == "MISSING":
            normalized = _normalize_point_id(str(point_id or ""))
            if normalized:
                out.add(normalized)
    return out


def _is_legacy_consequence_unclear_item(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    blob = _normalize_tg3_cost_text(
        " ".join(
            str(item.get(key) or "")
            for key in ("finding_id", "rule_id", "exact_rule_id", "title", "message", "recommended_fix_text", "suggested_rewrite_text")
        )
    ).lower()
    return bool(
        "konsekvens_unclear" in blob
        or "praktisk presisert" in blob
        or "praktisk betydning" in blob
    )


def _drop_legacy_consequence_unclear_when_semantic_missing(analysis_output: Dict[str, object]) -> None:
    if not isinstance(analysis_output, dict):
        return
    missing_points = _dommer_b_missing_consequence_points(analysis_output)
    if not missing_points:
        return

    def _keep(item: object) -> bool:
        if not _is_legacy_consequence_unclear_item(item):
            return True
        point_id = _arkat_item_point_id(item) if isinstance(item, dict) else ""
        return point_id not in missing_points

    for key in ("all_findings", "top_issues", "top_score_drivers", "score_drivers", "feedback_findings", "how_to_improve", "improvement_suggestions", "action_items", "recommended_fixes"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [item for item in items if _keep(item)]

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            for key in ("deductions", "issues"):
                items = component.get(key)
                if isinstance(items, list):
                    component[key] = [item for item in items if _keep(item)]


def _has_verified_missing_tg3_cost_issue(analysis_output: Dict[str, object]) -> bool:
    pipeline = analysis_output.get("arkat_semantic_pipeline") if isinstance(analysis_output, dict) else None
    points = pipeline.get("points") if isinstance(pipeline, dict) else []
    if isinstance(points, list):
        for point in points:
            if not isinstance(point, dict):
                continue
            tg = _normalize_tg_label(point.get("tg_grade") or point.get("tg"))
            if tg != "TG3":
                continue
            field_results = ((point.get("evaluation") or {}).get("field_results") or {}) if isinstance(point.get("evaluation"), dict) else {}
            if not isinstance(field_results, dict):
                continue
            for key in ("kostnadsanslag", "kostnadsestimat", "kostnad", "kostnadsinformasjon"):
                result = field_results.get(key)
                status = str(result.get("status") or "").strip().upper() if isinstance(result, dict) else ""
                if status in {"MISSING", "WRONG"}:
                    return True

    claim_re = re.compile(
        r"(?i)(?:TG3|tilstandsgrad\s*3).{0,120}(?:mangler|manglende|uten|ikke\s+(?:oppgitt|angitt|beskrevet)).{0,80}(?:kostnadsanslag|kostnadsestimat|kostnadsklasse|kostnadsinformasjon)|"
        r"(?:kostnadsanslag|kostnadsestimat|kostnadsklasse|kostnadsinformasjon).{0,80}(?:mangler|manglende|uten|ikke\s+(?:oppgitt|angitt|beskrevet)).{0,120}(?:TG3|tilstandsgrad\s*3)"
    )
    for key in ("all_findings", "top_issues", "top_score_drivers", "score_drivers", "how_to_improve", "improvement_suggestions", "action_items", "recommended_fixes"):
        items = analysis_output.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            blob = _normalize_tg3_cost_text(
                " ".join(
                    str(item.get(field) or "")
                    for field in ("finding_id", "rule_id", "title", "message", "reason", "recommended_fix_text", "suggested_rewrite_text")
                )
            )
            if claim_re.search(blob):
                return True
    return False


def _remove_untraceable_tg3_cost_summary_claims(summary: str, analysis_output: Dict[str, object]) -> str:
    if not isinstance(summary, str) or not summary or _has_verified_missing_tg3_cost_issue(analysis_output):
        return summary
    cost_words = r"(?:kostnadsanslag|kostnadsestimat|kostnadsklasse|kostnadsinformasjon)"
    out = re.sub(
        rf"(?i)(?:TG3(?:-?punkter?|-?punkt)?|tilstandsgrad\s*3(?:-?punkter?|-?punkt)?)\s+(?:har\s+)?(?:delvis\s+)?(?:manglende|mangler)\s+(?:også\s+)?(?:sjablonmessig|sjablongmessig)?\s*{cost_words}(?:\s*,?\s*(?:samt|og))?",
        "",
        summary,
    )
    out = re.sub(
        rf"(?i)(?:særlig\s+)?manglende\s+(?:sjablonmessig\s+)?{cost_words}\s+for\s+TG3(?:-?punkter?|-?punkt)?(?:\s*,?\s*samt|\s+og)?",
        "",
        out,
    )
    out = re.sub(
        rf"(?i)(?:\s+og\s+|,\s*)?{cost_words}\s+for\s+TG3(?:-?punkter?|-?punkt)?",
        "",
        out,
    )
    out = re.sub(r"(?i)\bARKAT-kvalitet\s+har\s+betydelige\s+mangler,\s*", "", out)
    out = re.sub(r"(?i)^\s*(?:særlig|samt|og|,)\s+", "", out)
    out = re.sub(r"\s+", " ", out).strip(" .,:")
    return out


def _remove_stale_missing_action_summary_claims(summary: str, analysis_output: Dict[str, object]) -> str:
    if not isinstance(summary, str) or not summary:
        return summary
    action_state = _dommer_b_missing_action_state(analysis_output)
    meta = analysis_output.get("meta") if isinstance(analysis_output.get("meta"), dict) else {}
    ns_blob = _normalize_tg3_cost_text(
        " ".join(
            str(value or "")
            for value in (
                meta.get("ns_standard_version") if isinstance(meta, dict) else "",
                meta.get("ns_version") if isinstance(meta, dict) else "",
                analysis_output.get("ns_version"),
            )
        )
    )
    out = summary
    if not action_state["tg3_missing"] and not action_state["tg2_missing"]:
        out = re.sub(
            r"(?i)(?:særlig\s+)?manglende\s+anbefalt\s+tiltak\s+og\s+",
            "",
            out,
        )
        out = re.sub(
            r"(?i)(?:særlig\s+)?manglende\s+anbefalt\s+tiltak(?:\s*,?\s*(?:samt|og))?",
            "",
            out,
        )
    if not action_state["tg3_missing"]:
        out = re.sub(
            r"(?i)manglende\s+anbefalt\s+tiltak\s+og\s+(kostnadsanslag\s+for\s+TG3)",
            r"manglende \1",
            out,
        )
        out = re.sub(
            r"(?i)(?:manglende\s+anbefalt\s+tiltak\s+(?:ved|på|i|for)\s+TG3(?:-punkter?)?|"
            r"TG3(?:-punkter?)?\s+mangler\s+anbefalt\s+tiltak|"
            r"manglende\s+tiltak\s+(?:ved|på|i|for)\s+TG3(?:-punkter?)?)(?:\s+og)?(?:\.|,)?",
            "",
            out,
        )
    if "2018" in ns_blob or not action_state["tg2_missing"]:
        out = re.sub(
            r"(?i)(?:systematisk\s+mangel\s+p[åa]\s+anbefalt\s+tiltak\s+i\s+TG2(?:-punkter?)?|"
            r"manglende\s+anbefalt\s+tiltak\s+(?:på|ved|i)\s+TG2(?:-punkter?)?|"
            r"TG2(?:-punkter?)?\s+mangler\s+anbefalt\s+tiltak|"
            r"mange\s+TG2(?:-punkter?)?\s+mangler\s+anbefalt\s+tiltak\s+som\s+påkrevd|"
            r"(?:og\s+)?anbefalt\s+tiltak\s+i\s+TG2(?:-punkter?)?|"
            r"(?:og\s+)?anbefalt\s+tiltak\s+som\s+kreves\s+i\s+NS\s*3600:2018-regime)"
            r"(?:\s+i\s+NS\s*3600:2025-regime)?(?:\s+og)?(?:\.|,)?",
            "",
            out,
        )
    out = re.sub(r"(?i)\bMange\s+som\s+kreves\s+i\s+NS\s*3600:2018-regime\.?", "", out)
    out = re.sub(r"(?i)\bHovedutfordringer\s+med\s+ved\s+TG3\s+og\s+enkelte\s+", "", out)
    out = re.sub(r"(?i)\bved\s+TG3\s+og\s+enkelte\s+", "Enkelte ", out)
    out = re.sub(r"(?i)\bmed\s+ved\s+TG3\b", "", out)
    out = re.sub(r"(?i)^\s*(?:og|,|\.)\s+", "", out)
    out = re.sub(r"\s+", " ", out).strip(" ., ")
    return out


def _neutral_zero_category_summary(category_id: str) -> str:
    if str(category_id or "").strip().upper() == "E":
        return "Ingen scoretrekk i metodikk og lovforankring etter endelig validering."
    return "Ingen scoretrekk i denne kategorien."


def _category_has_visible_scored_findings(analysis_output: Dict[str, object], category_id: str) -> bool:
    if not isinstance(analysis_output, dict):
        return False
    target = str(category_id or "").strip().upper()
    if not target:
        return False
    for key in ("all_findings", "top_issues", "top_score_drivers", "score_drivers", "feedback_findings"):
        items = analysis_output.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if _public_finding_category(item) == target and _is_public_scored_finding(item):
                return True
    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            for child_key in ("deductions", "issues"):
                children = component.get(child_key)
                if not isinstance(children, list):
                    continue
                for item in children:
                    if not isinstance(item, dict):
                        continue
                    item_category = str(item.get("category_id") or item.get("category") or _infer_category_from_rule_id(str(item.get("rule_id") or "")) or "").strip().upper()
                    points = item.get("points", item.get("deduction_points", item.get("deduction", 0)))
                    try:
                        point_value = int(float(points or 0))
                    except (TypeError, ValueError):
                        point_value = 0
                    if item_category == target and point_value > 0:
                        return True
    return False


def _sync_category_breakdown_with_score_by_category(analysis_output: Dict[str, object]) -> None:
    scoring_model = _load_scoring_model()
    category_names = scoring_model.get("category_names", {})
    score_rows = _ensure_score_by_category(analysis_output.get("score_by_category", []))
    analysis_output["score_by_category"] = score_rows
    breakdown = analysis_output.get("category_breakdown")
    if not isinstance(breakdown, list):
        breakdown = []
    by_category: Dict[str, Dict[str, object]] = {}
    for entry in breakdown:
        if not isinstance(entry, dict):
            continue
        raw_category = str(entry.get("category") or entry.get("category_id") or "").strip()
        match = re.match(r"^([A-F])\b", raw_category)
        if match:
            by_category[match.group(1)] = entry
    synced: List[Dict[str, object]] = []
    for row in score_rows:
        if not isinstance(row, dict):
            continue
        category_id = str(row.get("category_id") or "").strip().upper()
        if not category_id:
            continue
        deduction = int(row.get("deduction") or 0)
        zero_without_visible_findings = deduction <= 0 and not _category_has_visible_scored_findings(analysis_output, category_id)
        category_name = str(row.get("category_name") or category_names.get(category_id) or "").strip()
        entry = dict(by_category.get(category_id) or {})
        entry["category"] = f"{category_id} - {category_name}" if category_name else category_id
        entry["deduction_band"] = _category_band_from_deduction(deduction)
        summary = str(entry.get("summary") or "").strip()
        positive_without_deduction_re = re.compile(
            r"(?i)\b(?:ingen\s+(?:avvik|vesentlige\s+avvik|scoretrekk)|tilfredsstillende|tilstrekkelig|korrekt)\b"
        )
        if zero_without_visible_findings:
            summary = _neutral_zero_category_summary(category_id)
        elif deduction <= 0 and category_id == "E":
            summary = "Ingen scoretrekk i metodikk og lovforankring etter endelig validering."
        elif deduction > 0 and (not summary or positive_without_deduction_re.search(summary)):
            summary = "Scoretrekk i denne kategorien er synliggjort i funnlisten."
        elif not summary:
            if deduction <= 0:
                summary = "Ingen scoretrekk i denne kategorien."
            else:
                summary = "Scoretrekk i denne kategorien er synliggjort i funnlisten."
        elif deduction <= 0 and re.search(r"(?i)\bscoretrekk\b.*\bfunnlisten\b", summary):
            summary = "Ingen scoretrekk i denne kategorien."
        if category_id == "B" and deduction <= 0:
            summary = "Ingen scoretrekk i denne kategorien."
        if category_id == "A":
            meta = analysis_output.get("meta") if isinstance(analysis_output.get("meta"), dict) else {}
            ns_blob = _normalize_tg3_cost_text(
                " ".join(
                    str(value or "")
                    for value in (
                        meta.get("ns_standard_version") if isinstance(meta, dict) else "",
                        meta.get("ns_version") if isinstance(meta, dict) else "",
                        analysis_output.get("ns_version"),
                    )
                )
            ).lower()
            if "2018" in ns_blob:
                summary = re.sub(
                    r"(?i)(?:manglende anbefalt tiltak på TG2-punkter|mange TG2-punkter mangler anbefalt tiltak som påkrevd)\s+i\s+NS\s*3600:2025-regime(?:\s+og)?(?:\.|,)?",
                    "",
                    summary,
                ).strip(" .,")
                if not summary:
                    summary = "Scoretrekk i denne kategorien er synliggjort i funnlisten."
            summary = _remove_stale_missing_action_summary_claims(summary, analysis_output)
            summary = _remove_untraceable_tg3_cost_summary_claims(summary, analysis_output)
            summary = _remove_untraceable_schematic_summary_claims(summary, analysis_output)
            if deduction > 0 and not summary:
                summary = "Scoretrekk i denne kategorien er synliggjort i funnlisten."
        entry["summary"] = _replace_legacy_buyer_oriented_consequence_wording(summary) if "_replace_legacy_buyer_oriented_consequence_wording" in globals() else summary
        synced.append(entry)
    analysis_output["category_breakdown"] = synced


def _replace_legacy_buyer_oriented_consequence_wording(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    replacement = "Konsekvenser bør beskrive konkrete følger tydeligere, enten bygningsteknisk eller praktisk for kjøper"
    out = re.sub(
        r"(?i)konsekvens(?:er)?\s+(?:som\s+)?kunne\s+v[æa]rt\s+mer\s+kj[oø]perorienterte?",
        replacement,
        text,
    )
    out = re.sub(
        r"(?i)enkelte\s+konsekvenser\s+" + re.escape(replacement),
        replacement,
        out,
    )
    out = re.sub(
        r"(?i)(?:enkelte\s+)?konsekvens(?:er|formuleringer)?\s+som\s+ikke\s+er\s+kj[oø]perorienterte?\s+nok",
        replacement,
        out,
    )
    out = re.sub(
        r"(?i)konsekvens(?:er)?\s+(?:er\s+)?ikke\s+kj[oø]perorienterte?",
        replacement,
        out,
    )
    out = _replace_legacy_buyer_consequence_text(out)
    return re.sub(r"\s+", " ", out).strip()


def _normalize_category_summary_consequence_wording(analysis_output: Dict[str, object]) -> None:
    breakdown = analysis_output.get("category_breakdown")
    if isinstance(breakdown, list):
        for entry in breakdown:
            if isinstance(entry, dict) and isinstance(entry.get("summary"), str):
                entry["summary"] = _replace_legacy_buyer_oriented_consequence_wording(str(entry.get("summary") or ""))


def _remove_untraceable_schematic_summary_claims(summary: str, analysis_output: Dict[str, object]) -> str:
    if not isinstance(summary, str) or not summary:
        return summary
    blob_sources = []
    for key in ("all_findings", "top_issues", "top_score_drivers", "score_drivers", "how_to_improve", "improvement_suggestions", "action_items", "recommended_fixes"):
        items = analysis_output.get(key) if isinstance(analysis_output, dict) else None
        if isinstance(items, list):
            blob_sources.extend(json.dumps(item, ensure_ascii=False) for item in items if isinstance(item, dict))
    findings_blob = _normalize_tg3_cost_text(" ".join(blob_sources)).lower()
    supported = bool(re.search(r"(?i)sjablon|sjablong|kostnadsanslag|kostnadsestimat|kostnadsklasse|kostnadsinformasjon", findings_blob))
    if supported:
        return summary
    out = re.sub(r"(?i)(?:og\s+)?sjablongmessig\s*,?\s*(?:samt|og)?", "", summary)
    out = re.sub(r"(?i)(?:og\s+)?sjablonmessig\s*,?\s*(?:samt|og)?", "", out)
    out = re.sub(r"(?i)\bsjablongmessig\b", "", out)
    out = re.sub(r"(?i)\bsjablonmessig\b", "", out)
    out = re.sub(r"\s+", " ", out).strip(" .,:")
    out = re.sub(r"(?i)^\s*(?:og|samt|,)\s+", "", out).strip(" .,:")
    return out


def _finalize_category_summary_public_contracts(analysis_output: Dict[str, object]) -> None:
    breakdown = analysis_output.get("category_breakdown")
    if not isinstance(breakdown, list):
        return
    for entry in breakdown:
        if not isinstance(entry, dict) or not isinstance(entry.get("summary"), str):
            continue
        category = str(entry.get("category") or entry.get("category_id") or "").strip().upper()
        summary = str(entry.get("summary") or "")
        category_id_match = re.match(r"^([A-F])\b", category)
        category_id = category_id_match.group(1) if category_id_match else category[:1]
        if (
            str(entry.get("deduction_band") or "").strip() == "Ikke scoretrekk"
            and category_id
            and not _category_has_visible_scored_findings(analysis_output, category_id)
        ):
            entry["summary"] = _neutral_zero_category_summary(category_id)
            continue
        if category.startswith("A"):
            summary = _remove_stale_missing_action_summary_claims(summary, analysis_output)
            summary = _remove_untraceable_tg3_cost_summary_claims(summary, analysis_output)
            summary = _remove_untraceable_schematic_summary_claims(summary, analysis_output)
            if not summary and _score_category_deduction_map(analysis_output).get("A", 0) > 0:
                summary = "Scoretrekk i denne kategorien er synliggjort i funnlisten."
        if str(entry.get("deduction_band") or "").strip() == "Ikke scoretrekk" and re.search(r"(?i)\bscoretrekk\b.*\bfunnlisten\b", summary):
            summary = "Ingen scoretrekk i denne kategorien."
        if category.startswith("B") and str(entry.get("deduction_band") or "").strip() == "Ikke scoretrekk":
            summary = "Ingen scoretrekk i denne kategorien."
        entry["summary"] = _replace_legacy_buyer_oriented_consequence_wording(summary)
    _scrub_age_only_category_summary_without_finding(analysis_output)


def _dommer_b_tg_by_point(analysis_output: Dict[str, object]) -> Dict[str, str]:
    pipeline = analysis_output.get("arkat_semantic_pipeline") if isinstance(analysis_output, dict) else None
    points = pipeline.get("points") if isinstance(pipeline, dict) else []
    out: Dict[str, str] = {}
    if not isinstance(points, list):
        return out
    for point in points:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or ""))
        tg = _normalize_tg_label(point.get("tg_grade") or point.get("tg"))
        if point_id and tg:
            out[point_id] = tg
    return out


def _dommer_b_tg2_not_applicable_tiltak_points(analysis_output: Dict[str, object]) -> set:
    pipeline = analysis_output.get("arkat_semantic_pipeline")
    points = pipeline.get("points") if isinstance(pipeline, dict) else []
    out = set()
    if not isinstance(points, list):
        return out
    for point in points:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or ""))
        if not point_id:
            continue
        tg = str(point.get("tg_grade") or "").strip().upper()
        result = (((point.get("evaluation") or {}).get("field_results") or {}).get("anbefalt_tiltak") or {})
        status = str(result.get("status") or "").strip().upper() if isinstance(result, dict) else ""
        if tg == "TG2" and status == "NOT_APPLICABLE":
            out.add(point_id)
    return out


def _item_refs_any_point(item: object, point_ids: set) -> bool:
    if not isinstance(item, dict) or not point_ids:
        return False
    direct = _normalize_point_id(str(item.get("point_id") or item.get("exact_point_id") or item.get("component_id") or ""))
    if direct in point_ids:
        return True
    blob = _normalize_tg3_cost_text(json.dumps(item, ensure_ascii=False)).lower()
    return any(re.search(rf"(?<![0-9.]){re.escape(point_id.lower())}(?![0-9.])", blob) for point_id in point_ids)


def _item_is_tg3_missing_tiltak_claim(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    blob = _normalize_tg3_cost_text(
        " ".join(str(item.get(key) or "") for key in (
            "finding_id", "rule_id", "title", "message", "reason", "recommended_fix_text", "suggested_rewrite_text", "what_to_change"
        ))
    ).lower()
    return bool(
        "tg3_missing_recommended_action" in blob
        or "tg3_missing_tiltak" in blob
        or "a_arkat.tg3.tiltak_missing" in blob
        or ("tg3" in blob and "mangler" in blob and "tiltak" in blob)
        or "tiltak_missing" in blob
    )


def _drop_tg3_missing_tiltak_for_semantic_tg2_not_applicable(analysis_output: Dict[str, object]) -> None:
    suppressed_points = _dommer_b_tg2_not_applicable_tiltak_points(analysis_output)
    if not suppressed_points:
        return

    def _keep(item: object) -> bool:
        return not (_item_is_tg3_missing_tiltak_claim(item) and _item_refs_any_point(item, suppressed_points))

    for key in ("all_findings", "top_issues", "top_score_drivers", "score_drivers", "how_to_improve", "improvement_suggestions", "action_items", "recommended_fixes"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [item for item in items if _keep(item)]

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                component["deductions"] = [deduction for deduction in deductions if _keep(deduction)]




_FORBIDDEN_BUYER_CONSEQUENCE_RE = re.compile(
    r"(?i)kjøperorientert|kjøperorienterte|kjøperkonsekvens|kjøperperspektiv|kjøperrettet"
)
_NS2025_USER_TEXT_RE = re.compile(r"(?i)NS\s*3600:2025|2025-regime")


def _replace_legacy_buyer_consequence_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    replacement = "Konsekvenser bør beskrive konkrete følger tydeligere, enten bygningsteknisk eller praktisk for kjøper."
    consequence_replacement = "konkret konsekvens, enten bygningsteknisk eller praktisk for kjøper"
    fix_replacement = "Presiser konkret konsekvens, enten bygningsteknisk skade/risiko eller praktisk betydning for kjøper."
    out = text
    out = re.sub(r"(?i)konsekvens(?:en)?\s+ikke\s+kjøperorientert(?:e)?\s+nok", replacement, out)
    out = re.sub(r"(?i)mangler\s+tydelig\s+kjøperkonsekvens", "bør beskrive konkrete følger tydeligere", out)
    out = re.sub(r"(?i)mangler\s+(?:tydelig\s+)?konsekvens\s+for\s+kjøper\s*\([^)]*\)", "mangler tydelig konkret konsekvens", out)
    out = re.sub(r"(?i)mangler\s+(?:tydelig\s+)?konsekvens\s+for\s+kjøper", "mangler tydelig konkret konsekvens", out)
    out = re.sub(r"(?i)konsekvens\s+for\s+kjøper\s+kunne\s+v[æa]rt\s+tydeligere", "konkret konsekvens kunne vært tydeligere", out)
    out = re.sub(r"(?i)konsekvens\s+for\s+kjøper\s*\([^)]*\)", consequence_replacement, out)
    out = re.sub(r"(?i)Presiser\s+den\s+praktiske\s+konsekvensen\s+for\s+kjøper\s*\([^)]*\)\.", fix_replacement, out)
    out = re.sub(r"(?i)Presiser\s+den\s+praktiske\s+konsekvensen\s+for\s+kjøper", fix_replacement.rstrip('.'), out)
    out = re.sub(
        r"(?i)Konsekvens\s+må\s+beskrive\s+hva\s+forholdet\s+betyr\s+for\s+kjøper,?\s+ikke\s+bare\s+teknisk\s+skadeutvikling\.?",
        "Konsekvens bør beskrive konkrete følger, enten bygningsteknisk eller praktisk for kjøper.",
        out,
    )
    out = re.sub(
        r"(?i)(?:alle\s+TG2-punkter\s+)?mangler\s+tydelig\s+forklaring\s+av\s+hva\s+avviket\s+betyr\s+for\s+kjøper\s+i\s+praksis",
        "mangler tydelig konkret konsekvens",
        out,
    )
    out = re.sub(
        r"(?i)hva\s+avviket\s+betyr\s+for\s+kjøper\s+i\s+praksis",
        "konkret konsekvens",
        out,
    )
    out = re.sub(
        r"(?i)Flere\s+punkter\s+beskriver\s+kun\s+tekniske\s+forhold\s+uten\s+å\s+forklare\s+praktisk\s+betydning\s+for\s+kjøper",
        "Flere punkter mangler konkret konsekvens, enten bygningsteknisk eller praktisk for kjøper",
        out,
    )
    out = re.sub(r"(?i)kjøperkonsekvens(?:en)?", "konkret følge", out)
    out = re.sub(r"(?i)kjøperperspektiv(?:et)?", "konkrete følger", out)
    out = re.sub(r"(?i)kjøperorienterte?", "konkrete", out)
    out = re.sub(r"(?i)kjøperrettet", "konkret", out)
    out = re.sub(r"(?i)\bARKAT-kvalitet:\s*Hovedutfordringer\s+med\s*,\s*samt\s+enkelte\s+", "", out)
    out = re.sub(r"(?i)\bHovedutfordringer\s+med\s*,\s*samt\s+enkelte\s+", "", out)
    out = re.sub(r"(?i)\bmangler\s+konkrete\s+konsekvens\b", "mangler tydelig konkret konsekvens", out)
    return re.sub(r"\s+", " ", out).strip(" .,:")


_BUYER_ONLY_CONSEQUENCE_PUBLIC_RE = re.compile(
    r"(?i)(?:consequence_buyer_orientation_required|gate_konsekvens_not_buyer_oriented|"
    r"konsekvens\s+ikke\s+kjøperorientert|kjøperorientert|kjøperorienterte|"
    r"kjøperkonsekvens|kjøperperspektiv|kjøperrettet|hva\s+forholdet\s+betyr\s+for\s+kjøper|"
    r"teknisk\s+skadeutvikling\s+eller\s+bygningsrisiko|praktisk\s+presisert)"
)


def _contains_buyer_only_consequence_public_claim(item: object) -> bool:
    if isinstance(item, str):
        blob = item
    else:
        try:
            blob = json.dumps(item, ensure_ascii=False)
        except TypeError:
            blob = str(item)
    return bool(_BUYER_ONLY_CONSEQUENCE_PUBLIC_RE.search(blob or ""))


def _is_public_finding_like_item(item: object) -> bool:
    if not isinstance(item, dict):
        return isinstance(item, str)
    return any(key in item for key in ("finding_id", "rule_id", "message", "recommended_fix_text", "suggested_rewrite_text", "reason", "what_to_change"))


def _drop_buyer_only_consequence_public_claims(payload: object) -> None:
    if isinstance(payload, dict):
        for key in (
            "all_findings",
            "top_issues",
            "top_score_drivers",
            "score_drivers",
            "how_to_improve",
            "improvement_suggestions",
            "action_items",
            "recommended_fixes",
            "feedback_findings",
            "findings",
            "deductions",
            "issues",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                payload[key] = [
                    item for item in value
                    if not (
                        _is_public_finding_like_item(item)
                        and _contains_buyer_only_consequence_public_claim(item)
                    )
                ]
        for value in list(payload.values()):
            _drop_buyer_only_consequence_public_claims(value)
    elif isinstance(payload, list):
        # Generic lists may be Dommer B point lists. Only the named public finding
        # keys above are filtered; all other lists are traversed in place.
        for item in payload:
            _drop_buyer_only_consequence_public_claims(item)


def _sanitize_ns2018_user_text(text: str, ns_version: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    if "2018" not in str(ns_version or ""):
        return text
    out = re.sub(r"(?i)NS\s*3600:2025", "NS 3600:2018", text)
    out = re.sub(r"(?i)NS3600:2025", "NS3600:2018", out)
    out = re.sub(r"(?i)\s*i\s+NS\s*3600:2025-regime", "", out)
    out = re.sub(r"(?i)(?:og\s+)?anbefalt\s+tiltak\s+i\s+TG2(?:-punkter?)?", "", out)
    out = re.sub(r"(?i)(?:og\s+)?anbefalt\s+tiltak\s+som\s+kreves\s+i\s+NS\s*3600:2018-regime", "", out)
    out = re.sub(r"(?i)2025-regime", "2018-regime", out)
    return re.sub(r"\s+", " ", out).strip(" .,;")


def _walk_user_text(node: object, transform) -> None:
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(value, str):
                node[key] = transform(value)
            else:
                _walk_user_text(value, transform)
    elif isinstance(node, list):
        for idx, value in enumerate(list(node)):
            if isinstance(value, str):
                node[idx] = transform(value)
            else:
                _walk_user_text(value, transform)


def _detected_ns_version_for_output(analysis_output: Dict[str, object]) -> str:
    meta = analysis_output.get("meta") if isinstance(analysis_output.get("meta"), dict) else {}
    return " ".join(
        str(value or "")
        for value in (
            meta.get("ns_standard_version") if isinstance(meta, dict) else "",
            meta.get("ns_version") if isinstance(meta, dict) else "",
            analysis_output.get("ns_version"),
        )
    )


def _sanitize_user_facing_text_contracts(analysis_output: Dict[str, object]) -> None:
    if not isinstance(analysis_output, dict):
        return
    ns_blob = _detected_ns_version_for_output(analysis_output)

    def _transform(value: str) -> str:
        value = _replace_legacy_buyer_consequence_text(value)
        value = _sanitize_ns2018_user_text(value, ns_blob)
        return value

    _walk_user_text(analysis_output, _transform)
    if "2018" in ns_blob:
        disclaimers = analysis_output.get("disclaimers")
        if isinstance(disclaimers, list):
            analysis_output["disclaimers"] = [
                item for item in disclaimers
                if not (
                    isinstance(item, str)
                    and (
                        re.search(r"(?i)96%", item)
                        and re.search(r"(?i)TG2", item)
                        and re.search(r"(?i)anbefalt\s+tiltak|tiltak", item)
                    )
                )
            ]


def _is_duplicate_f001_legality_summary(item: object, legal_rule_ids: set) -> bool:
    if not isinstance(item, dict):
        return False
    blob = json.dumps(item, ensure_ascii=False)
    if "F_001" not in blob and "Lovlighetsmangler uten tilstrekkelig konsekvens" not in blob:
        return False
    return bool(legal_rule_ids.intersection({"L_RK_01_REKKVERK", "L-RK-01", "L-BU-01", "L-AV-01"}))


def _mark_duplicate_f001_informational(analysis_output: Dict[str, object]) -> None:
    if not isinstance(analysis_output, dict):
        return
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return
    legal_rule_ids = {
        str(item.get("rule_id") or item.get("finding_id") or "")
        for item in all_findings
        if isinstance(item, dict)
    }
    f001_found = False
    for item in all_findings:
        if not _is_duplicate_f001_legality_summary(item, legal_rule_ids):
            continue
        f001_found = True
        item["severity"] = "info"
        item["deduction_band"] = "Ikke scoretrekk"
        item["score_impact"] = "informational_duplicate_summary"
        item["deduction"] = 0
        item["deduction_points"] = 0
        item["points"] = 0
        item["message"] = "Oppsummerer lovlighetsforhold som allerede er scoret i egne lovlighetsfunn, og gir ikke ekstra scoretrekk."
        item["recommended_fix_text"] = "Se de konkrete lovlighetsfunnene for tiltak og dokumentasjon."
        item["suggested_rewrite_text"] = "Se de konkrete lovlighetsfunnene for konsekvens og oppfølging."
    if not f001_found:
        return
    analysis_output["all_findings"] = [
        item for item in all_findings
        if not _is_duplicate_f001_legality_summary(item, legal_rule_ids)
    ]
    for key in ("top_issues", "top_score_drivers", "score_drivers", "how_to_improve", "improvement_suggestions", "action_items", "recommended_fixes"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [item for item in items if not _is_duplicate_f001_legality_summary(item, legal_rule_ids)]
    gate = analysis_output.get("gate")
    if isinstance(gate, dict):
        blocked_by = gate.get("blocked_by")
        if isinstance(blocked_by, list):
            gate["blocked_by"] = [item for item in blocked_by if str(item or "") != "F_001"]
            gate["blocked_by_count"] = len(gate["blocked_by"])
            gate["blocked_96"] = bool(gate["blocked_by"])
            gate["active"] = bool(gate["blocked_by"])
            if not gate["blocked_by"]:
                gate["max_score_if_blocked"] = None
    rows = analysis_output.get("score_by_category")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("category_id") or row.get("category") or "").strip().upper().startswith("F"):
                try:
                    deduction = int(row.get("deduction") or 0)
                except (TypeError, ValueError):
                    deduction = 0
                row["deduction"] = max(0, deduction - 5)
    for key in ("score_total", "trygghetsscore"):
        if isinstance(analysis_output.get(key), (int, float)):
            analysis_output[key] = min(100, int(analysis_output[key]) + 5)


def _drop_arkat_false_positives(analysis_output: Dict[str, object]) -> None:
    """
    Remove LLM findings that claim missing anbefalt tiltak / ARKAT for a point when
    our per-segment validation (semantic ARK/ARKAT check) passed for that point.
    """
    segment_validation = analysis_output.get("segment_validation")
    if not isinstance(segment_validation, list):
        return
    meta = analysis_output.get("meta")
    standard_version = str(meta.get("ns_standard_version") or "") if isinstance(meta, dict) else ""
    passed_point_ids = set()
    present_by_point: Dict[str, set] = {}
    for seg in segment_validation:
        if not isinstance(seg, dict):
            continue
        point_id = _normalize_point_id(str(seg.get("point_id") or ""))
        if not point_id:
            continue
        if seg.get("passed") is True:
            passed_point_ids.add(point_id)
        present_keys = seg.get("present_keys")
        if isinstance(present_keys, list):
            present_by_point[point_id] = {
                str(key) for key in present_keys if isinstance(key, str) and str(key).strip()
            }
            continue
        combined_text = str(seg.get("combined_text") or "")
        tg = str(seg.get("tg") or "")
        if combined_text:
            present_by_point[point_id] = _segment_present_keys(
                combined_text,
                tg,
                standard_version=standard_version,
                point_title=str(seg.get("exact_point_title") or seg.get("title") or point_id),
            )
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return
    to_drop = []
    for idx, f in enumerate(all_findings):
        if not isinstance(f, dict):
            continue
        fid = (f.get("finding_id") or "").lower()
        title = (f.get("title") or "").lower()
        msg = (f.get("message") or "").lower()
        is_arkat_finding = (
            "tiltak" in fid or "arkat" in fid or "anbefalt" in title or "mangler anbefalt" in msg
            or "mangler full arkat" in msg or "tg3 mangler" in title
        )
        target_key = _finding_targets_missing_key(f)
        point_id = str(
            f.get("exact_point_id")
            or _parse_runtime_point_ref_from_v16_finding(f)
            or _parse_point_id_from_v16_finding(f)
            or ""
        )
        if point_id:
            point_id = _normalize_point_id(point_id)
        if not point_id:
            continue
        if point_id in passed_point_ids and (is_arkat_finding or target_key == "arkat"):
            to_drop.append(idx)
            continue
        if target_key and point_id in present_by_point:
            present = present_by_point.get(point_id, set())
            if target_key in present or (target_key == "kostnad" and "kostnad_single_only" in present):
                to_drop.append(idx)
    for idx in reversed(to_drop):
        all_findings.pop(idx)


def _content_claim_keys(item: Dict[str, object]) -> set:
    blob = _normalize_tg3_cost_text(
        f"{item.get('finding_id', '')} {item.get('rule_id', '')} {item.get('title', '')} {item.get('message', '')} "
        f"{item.get('reason', '')} {item.get('recommended_fix_text', '')}"
    ).lower()
    claims = set()
    if any(marker in blob for marker in ("årsak/begrunnelse mangler", "mangler årsak", "mangler begrunnelse", "arsak", "begrunnelse")):
        claims.add("årsak")
    if "risiko mangler" in blob or "mangler risiko" in blob:
        claims.add("risiko")
    if "konsekvens mangler" in blob or "mangler konsekvens" in blob:
        claims.add("konsekvens")
    if "praktisk presisert" in blob or "praktisk betydning" in blob:
        claims.add("konsekvens")
    if (
        "tg2 mangler full ark-struktur" in blob
        or ("tg2-punkt" in blob and "ark-struktur" in blob)
        or ("tg2-punkt" in blob and "mangler tydelig risiko" in blob)
        or ("tg2-punkt" in blob and "mangler tydelig konsekvens" in blob)
    ):
        claims.add("tg2_ark")
    if (
        "tg3 mangler anbefalt tiltak" in blob
        or "mangler anbefalt tiltak" in blob
        or "anbefalt tiltak mangler" in blob
        or "tiltak_missing" in blob
    ):
        claims.add("anbefalt_tiltak")
    if (
        "hms-forhold uten tg mangler forklaring" in blob
        or "no_tg_hms" in blob
        or ("hms" in blob and "mangler" in blob)
    ):
        claims.add("no_tg_hms")
    if "formulert som ordre" in blob or "ordre/pålegg" in blob or "prosjekterende" in blob:
        claims.add("imperative_measure")
    if "undersøkelsesbegrensning" in blob or "undersøkelsesbegrensninger" in blob:
        claims.add("survey_limitation")
    return claims


_SURVEY_LIMITATION_RE = re.compile(
    r"(?ix)\b(?:"
    r"helt\s+tildekket\s+av\s+sn[oø]\b|"
    r"tildekket\s+av\s+sn[oø]\s+p[aå]\s+befaringsdagen\b|"
    r"ikke\s+mulig\s+[aå]\s+avgj[oø]re\s+hvilken\s+tilstand\b|"
    r"tilstand(?:en)?\s+kunne\s+ikke\s+vurderes\b|"
    r"ikke\s+mulig\s+[aå]\s+befare\b|"
    r"ikke\s+mulig\s+[aå]\s+kontrollere\b|"
    r"ikke\s+synlig\s+for\s+inspeksjon\b|"
    r"kun\s+observ(?:ert|ert)\s+fra\b|"
    r"kun\s+besikt(?:et|iget)\s+fra\b|"
    r"vurderingen\s+begrenset\b|"
    r"tilstandsanalysen\s+begrenset\b|"
    r"ikke\s+vært\s+sikkerhetsmessig\s+forsvarlig\b|"
    r"ikke\s+mulig\s+uten\b|"
    r"ikke\s+undersøkt\s+fra\s+nært\s+hold\b|"
    r"ikke\s+undersøkt\b|"
    r"utilgjengelig\b|"
    r"fra\s+bakkenivå\b|"
    r"fra\s+luken\b|"
    r"skjulte\s+.*kan\s+ikke\s+utelukkes\b"
    r")"
)


def _point_has_survey_limitation_text(text: str) -> bool:
    normalized = _normalize_tg3_cost_text(text or "").lower()
    if not normalized:
        return False
    return bool(_SURVEY_LIMITATION_RE.search(normalized))


def _point_has_non_imperative_recommendation(text: str) -> bool:
    normalized = _normalize_tg3_cost_text(text or "").lower()
    if not normalized:
        return False
    return bool(
        "det anbefales" in normalized
        or "anbefales å" in normalized
        or "anbefaler at" in normalized
        or "anbefaler å" in normalized
        or "det bør" in normalized
        or "bør vurderes" in normalized
        or "bør utføres" in normalized
    )


def _extract_arkat_section_text(text: str, section: str) -> str:
    normalized_text = _normalize_tg3_cost_text(text or "")
    if not normalized_text:
        return ""
    section_patterns = {
        "årsak": r"(?:årsak|arsak|årask|arask)",
        "risiko": r"(?:risiko|risko)",
        "konsekvens": r"(?:konsekvens|konsekv(?:ens)?\.?)",
        # OCR variants: "tiltak" can be misread as "ltak" / "tltak"
        "tiltak": r"(?:anbefalt(?:e)?\s+(?:tiltak|tltak|ltak)|tiltak|tltak|ltak)",
        "anbefalt_tiltak": r"(?:anbefalt(?:e)?\s+(?:tiltak|tltak|ltak)|tiltak|tltak|ltak)",
        # OCR variants: "Konsekvens/tiltak" often appears without ":" and can be misread as ".../ltak"
        "konsekvens_tiltak": r"(?:konsekvens\s*(?:/|-|og)\s*(?:tiltak|tltak|ltak)|konsekvens\s+(?:tiltak|tltak|ltak))",
    }
    label = section_patterns.get(section)
    if not label:
        return ""
    any_label = "|".join(section_patterns.values())
    # Fremtind/iVerdi tables frequently render headers without ":" and sometimes
    # without a hard newline (just column spacing). Accept 2+ spaces/tabs as a separator.
    label_sep = r"(?::|\n|[ \t]{2,})"
    match = re.search(
        rf"(?is)\b{label}\s*{label_sep}\s*(.+?)(?=\s*(?:{any_label})\s*{label_sep}|\Z)",
        normalized_text,
    )
    if match:
        return match.group(1).strip()
    if section in {"konsekvens", "tiltak", "anbefalt_tiltak"}:
        merged_label = section_patterns["konsekvens_tiltak"]
        merged = re.search(
            rf"(?is)\b{merged_label}\s*{label_sep}\s*(.+?)(?=\s*(?:{any_label})\s*{label_sep}|\Z)",
            normalized_text,
        )
        if merged:
            return merged.group(1).strip()
    if section == "årsak":
        for line in normalized_text.splitlines():
            stripped = line.strip()
            if re.search(r"(?i)\b(?:tg2|tg3|tilstandsgrad\s*[23])\b", stripped) and "vurderes da" in stripped.lower():
                stripped = re.split(
                    rf"(?i)\b(?:{any_label})\s*(?::|\n)",
                    stripped,
                    maxsplit=1,
                )[0].strip()
                return stripped
    return ""


def _point_has_qualifying_risk_text(text: str) -> bool:
    normalized = _normalize_tg3_cost_text(text or "").lower()
    if not normalized:
        return False
    source = _extract_arkat_section_text(text, "risiko").lower() or normalized
    harm_markers = (
        "lekk",
        "fukt",
        "råte",
        "mugg",
        "sopp",
        "brann",
        "personskade",
        "videre skade",
        "følgeskade",
        "kortslutning",
        "svikt",
        "slitasje",
        "korro",
        "ikke vanntett",
        "mister evnen",
        "frostskade",
        "stromforbruk",
        "strømforbruk",
    )
    limitation_markers = (
        "ikke synlig for inspeksjon",
        "ikke mulig a inspisere",
        "ikke mulig å inspisere",
        "kan vaere forhold som tilsier",
        "kan være forhold som tilsier",
        "ikke vurdert med behov for tiltak",
        "må oppgraderes innen",
        "ma oppgraderes innen",
    )
    if (
        _point_has_survey_limitation_text(source)
        or any(marker in source for marker in limitation_markers)
    ) and not any(marker in source for marker in harm_markers):
        return False
    risk_markers = (
        "kan føre til",
        "kan medføre",
        "risiko for",
        *harm_markers,
        "energief",
    )
    return any(marker in source for marker in risk_markers)


def _point_has_buyer_oriented_consequence_text(text: str) -> bool:
    normalized = _normalize_tg3_cost_text(text or "").lower()
    if not normalized:
        return False
    source = _extract_arkat_section_text(text, "konsekvens").lower() or normalized
    source = re.sub(r"\s+", " ", source).strip()
    buyer_markers = (
        "må påregne",
        "ma paregne",
        "utbedringsbehov",
        "vedlikeholdsbehov",
        "økte kostnader",
        "okte kostnader",
        "kostbare reparasjoner",
        "redusert bruksverdi",
        "høyere strømforbruk",
        "hoyere stromforbruk",
        "energiforbruk",
        "energikostnader",
        "praktisk betydning",
        "for kjøper",
        "for kjoper",
        "behov for tiltak",
        "bruksverdi",
        "kostbare utbedringstiltak",
        "omfattende reparasjoner",
        "dyrere skader",
        "større skader",
        "bortfall av varmtvann",
        "akutt utbedring",
        "utskifting",
        "ma paregnes utskifting",
        "påregnes utskifting",
        "energitap",
        "redusert verdi",
        "verdireduksjon",
        "kan ikke brukes",
        "ikke brukes som",
        "kan ikke benyttes",
        "ikke benyttes som",
        "bruksbegrensning",
        "helserisiko",
        "helsefare",
        "sikkerhetsrisiko",
        "fare for personskade",
        "brannfare",
        "dårlig inneklima",
        "redusert inneklima",
        "kan ikke forsikres",
        "ikke forsikres",
        "forsikringsmessig",
        "kommunen kan kreve",
        "myndighetene kan kreve",
        "pålegg om utbedring",
        "avvik fra forskrift",
        "avvik fra dagens forskriftskrav",
    )
    buyer_patterns = (
        r"\b(?:må|ma)\s+p[aå]regne\b.{0,80}\b(?:kostnad(?:er)?|utbedring|reparasjon(?:er)?|vedlikehold|oppf[oø]lging|utskifting|kontroll)\b",
        r"\b(?:økte?|h[oø]yere)\s+(?:kostnad(?:er)?|energikostnad(?:er)?|str[oø]mforbruk)\b",
        r"\b(?:rom|bad|vaskerom|kj[oø]kken|bolig(?:en)?|areal(?:et)?)\b.{0,40}\b(?:kan\s+ikke\s+brukes|kan\s+ikke\s+benyttes|ikke\s+kan\s+brukes)\b",
        r"\b(?:redusert|nedsatt)\s+(?:bruksverdi|bruksmulighet|funksjon\s+for\s+beboer)\b",
        r"\b(?:helserisiko|helsefare|sikkerhetsrisiko|fare\s+for\s+personskade|brannfare)\b",
        r"\b(?:kan\s+ikke\s+forsikres|ikke\s+forsikres|kommunen\s+kan\s+kreve|myndighetene\s+kan\s+kreve|p[aå]legg\s+om\s+utbedring)\b",
    )
    implicit_buyer_patterns = (
        r"\b(?:redusert\s+levetid|redusert\s+gjenst[aå]ende\s+brukstid|forkortet\s+levetid)\b",
        r"\b(?:fuktskader|r[aå]teskader|vannskader|lekkasjer|kondensproblemer|videre\s+skadeutvikling|videre\s+skader?)\b",
        r"\bskader?\s+p[åa]\s+(?:underliggende|omkringliggende)\s+konstruksjon(?:er)?\b",
        r"\b(?:nedb[oø]yning|skjevheter|fuktinnsig|vannansamlinger|redusert\s+sklisikkerhet)\b",
        r"\b(?:fare\s+for\s+personskade|personskade|brannfare)\b",
    )
    future_risk_markers = (
        "kan trenge inn",
        "kan føre til",
        "kan medføre",
        "fare for",
        "risiko for",
    )
    technical_only_markers = (
        "skader i konstruksjonen",
        "skader pa konstruksjonen",
        "skader på konstruksjonen",
        "skade i konstruksjonen",
        "skjulte skader",
        "skjult skade",
        "fuktinntrengning",
        "underliggende konstruksjon",
        "bakenforliggende veggkonstruksjon",
        "videre utvikling",
        "utvikle seg",
        "lekkasjer som medforer til skader pa underliggende konstruksjon",
        "lekkasjer som medfører til skader på underliggende konstruksjon",
        "risiko for skader i konstruksjonen",
        "kan føre til skader i konstruksjonen",
        "kan medføre skader i konstruksjonen",
        "redusert funksjon",
        "tidspunkt for utskifting",
        "tidspunkt for utskiftning",
        "nærmer seg utskifting",
        "naermer seg utskifting",
        "modent for å skiftes",
        "modent for a skiftes",
        "moden for utskifting",
        "moden for utbedring",
        "kan føre til plutselig lekkasjer",
        "kan fore til plutselig lekkasjer",
        "kan føre til lekkasjer",
        "kan fore til lekkasjer",
    )
    has_buyer_marker = any(marker in source for marker in buyer_markers) or any(
        re.search(pattern, source) for pattern in buyer_patterns
    ) or any(
        re.search(pattern, source) for pattern in implicit_buyer_patterns
    )
    if any(marker in source for marker in technical_only_markers) and not has_buyer_marker:
        return False
    if any(marker in source for marker in future_risk_markers) and not has_buyer_marker:
        return False
    return has_buyer_marker


def _point_has_technical_only_consequence_text(text: str) -> bool:
    normalized = _normalize_tg3_cost_text(text or "").lower()
    if not normalized:
        return False
    source = _extract_arkat_section_text(text, "konsekvens").lower() or normalized
    source = re.sub(r"\s+", " ", source).strip()
    if not source:
        return False
    if _point_has_buyer_oriented_consequence_text(text):
        return False
    technical_only_markers = (
        "skader i konstruksjonen",
        "skader pa konstruksjonen",
        "skader på konstruksjonen",
        "skade i konstruksjonen",
        "skjulte skader",
        "skjult skade",
        "fuktinntrengning",
        "underliggende konstruksjon",
        "bakenforliggende veggkonstruksjon",
        "videre utvikling",
        "utvikle seg",
        "lekkasjer som medforer til skader pa underliggende konstruksjon",
        "lekkasjer som medfører til skader på underliggende konstruksjon",
        "risiko for skader i konstruksjonen",
        "kan føre til skader i konstruksjonen",
        "kan medføre skader i konstruksjonen",
        "redusert funksjon",
        "tidspunkt for utskifting",
        "tidspunkt for utskiftning",
        "nærmer seg utskifting",
        "naermer seg utskifting",
        "modent for å skiftes",
        "modent for a skiftes",
        "moden for utskifting",
        "moden for utbedring",
        "kan føre til plutselig lekkasjer",
        "kan fore til plutselig lekkasjer",
        "kan føre til lekkasjer",
        "kan fore til lekkasjer",
    )
    future_risk_markers = (
        "kan trenge inn",
        "kan føre til",
        "kan medføre",
        "fare for",
        "risiko for",
    )
    technical_only_patterns = (
        r"\btidspunkt\s+for\s+utskift(?:ing|ning)\b.{0,40}\bn[æa]rmer\s+seg\b",
        r"\b(?:eldre\s+)?[a-zæøå]+\s+har\s+redusert\s+funksjon\b",
        r"\bkan\b.{0,40}\bf[øo]re\s+til\b.{0,40}\blekkasje(?:r)?\b",
        r"\bkan\b.{0,80}\bikke\s+t[øo]rke\s+opp\b",
        r"\bskaper\s+ideelle\s+forhold\s+for\b.{0,40}\b(?:r[aå]tesopp|muggvekst|svertesopp)\b",
        r"\b(?:muggvekst|r[aå]tesopp|svertesopp)\b",
        r"\bfukt(?:ighet)?\b.{0,60}\b(?:ikke\s+t[øo]rke\s+opp|blir\s+st[aå]ende)\b",
    )
    return (
        not _point_has_buyer_oriented_consequence_text(text)
        and (
            any(marker in source for marker in technical_only_markers)
            or any(marker in source for marker in future_risk_markers)
            or any(re.search(pattern, source) for pattern in technical_only_patterns)
        )
    )


def _normalize_non_buyer_oriented_consequence_findings(analysis_output: Dict[str, object]) -> None:
    # Buyer-only consequence findings are legacy behavior. Dommer B accepts
    # either building-technical consequence or practical buyer consequence.
    return


def _ensure_non_buyer_oriented_consequence_findings(
    detected_points: List[Dict[str, object]],
    analysis_output: Dict[str, object],
    report_text: str = "",
) -> None:
    # Legacy backstop intentionally disabled: a consequence may be valid when it
    # describes technical damage development/building risk without spelling out
    # buyer impact separately.
    return


def _point_has_age_lifespan_only_consequence_regression(point_id: str, title: str, text: str) -> bool:
    """
    Client regression guard: technical restlevetid/TG-age rationale is not a valid
    buyer-oriented consequence. Keep this narrow so Priority 2 points such as 10.2
    are not pulled back into false-positive territory.
    """
    normalized = _normalize_tg3_cost_text(text or "").lower()
    normalized_title = _normalize_tg3_cost_text(title or "").lower()
    if not normalized:
        return False
    if _point_has_buyer_oriented_consequence_text(normalized):
        return False
    if point_id == "7.3.3" or ("bad 1.etasje" in normalized_title and "membran" in normalized_title):
        return bool(
            re.search(r"\bredusert\s+gjenst[aå]ende\s+brukstid\s+som\s+konsekvens\b", normalized)
            or re.search(
                r"\bforventet\s+levetid\b.{0,80}\b(?:50\s*%|halvparten)\b.{0,120}\bpassert\b.{0,120}\bredusert\s+gjenst[aå]ende\s+brukstid\b",
                normalized,
            )
        )
    if point_id == "10.4" or "varmesentral" in normalized_title:
        return bool(
            re.search(r"\bvalgt\s+tilstandsgrad\s+gis\s+som\s+f[øo]lge\s+av\s+alder(?:\s+p[åa]\s+varmepumpe)?\b", normalized)
            or ("ikke funksjonstestet" in normalized and "alder" in normalized)
        )
    return False


def _force_age_lifespan_only_consequence_flags(
    detected_points: List[Dict[str, object]],
    analysis_output: Dict[str, object],
) -> None:
    """
    Make the final API payload obey the ARKAT rule used by the client:
    consequence is OK only when it states practical buyer impact. This protects
    exact regression cases where later output builders had published OK after the
    semantic pipeline.
    """
    if not isinstance(analysis_output, dict):
        return

    point_sources: Dict[str, Dict[str, str]] = {}
    for point in detected_points or []:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or point.get("numeric_id") or point.get("native_label") or ""))
        if not point_id:
            continue
        point_sources.setdefault(point_id, {
            "title": str(point.get("title") or point_id),
            "text": str(
                point.get("effective_span_text")
                or point.get("exact_span_text")
                or point.get("span_text")
                or point.get("excerpt")
                or ""
            ),
        })

    semantic_points = _semantic_arkat_points_by_id(analysis_output)
    for point_id, semantic_point in semantic_points.items():
        if not isinstance(semantic_point, dict):
            continue
        title = str(semantic_point.get("title") or point_sources.get(point_id, {}).get("title") or point_id)
        raw_text = str(semantic_point.get("raw_point_text") or point_sources.get(point_id, {}).get("text") or "")
        if not _point_has_age_lifespan_only_consequence_regression(point_id, title, raw_text):
            continue
        point_sources[point_id] = {"title": title, "text": raw_text}
        evaluation = semantic_point.setdefault("evaluation", {})
        if not isinstance(evaluation, dict):
            evaluation = {}
            semantic_point["evaluation"] = evaluation
        field_results = evaluation.setdefault("field_results", {})
        if not isinstance(field_results, dict):
            field_results = {}
            evaluation["field_results"] = field_results
        consequence = field_results.setdefault("konsekvens", {})
        if not isinstance(consequence, dict):
            consequence = {}
            field_results["konsekvens"] = consequence
        consequence.update({
            "field_name": "konsekvens",
            "status": "WRONG",
            "error_type": "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS",
            "severity": "medium",
            "explanation": (
                "Konsekvensen beskriver alder, restlevetid eller TG-begrunnelse, "
                "ikke hva forholdet betyr for kjøper i praksis."
            ),
        })
        evaluation["has_errors"] = True

    forced_points = {
        point_id: meta
        for point_id, meta in point_sources.items()
        if _point_has_age_lifespan_only_consequence_regression(point_id, meta.get("title", ""), meta.get("text", ""))
    }
    if not forced_points:
        return

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            component_id = _normalize_point_id(str(component.get("component_id") or component.get("point_id") or ""))
            if component_id not in forced_points:
                continue
            arkat = component.setdefault("arkat", {})
            if isinstance(arkat, dict):
                consequence_payload = arkat.setdefault("konsekvens", {})
                if isinstance(consequence_payload, dict):
                    consequence_payload["status"] = "unclear"
                    consequence_payload["required"] = True
                    consequence_payload["comment"] = (
                        "Konsekvensen er teknisk/alderbasert og forklarer ikke praktisk betydning for kjøper."
                    )
            deductions = component.setdefault("deductions", [])
            if isinstance(deductions, list):
                already = any(
                    isinstance(d, dict)
                    and str(d.get("rule_id") or "") == "gate_konsekvens_not_buyer_oriented"
                    for d in deductions
                )
                if not already:
                    deductions.append({
                        "rule_id": "gate_konsekvens_not_buyer_oriented",
                        "category_id": "A",
                        "points": 5,
                        "severity": "major",
                        "reason": "Konsekvens beskriver teknisk status, alder eller restlevetid i stedet for praktisk betydning for kjøper.",
                    })

    for point_id, meta in forced_points.items():
        evidence = meta.get("text", "")
        _append_unique_all_finding(
            analysis_output,
            {
                "finding_id": f"A_ARKAT_KONSEKVENS_NOT_BUYER_ORIENTED_{point_id.replace('.', '_')}",
                "rule_id": "gate_konsekvens_not_buyer_oriented",
                "point_id": point_id,
                "exact_point_id": point_id,
                "exact_point_title": meta.get("title") or point_id,
                "exact_point_text": evidence,
                "category": "A",
                "severity": "major",
                "deduction_band": "Høyt trekk",
                "title": "Konsekvens ikke kjøperorientert",
                "message": "Konsekvens beskriver teknisk skadeutvikling eller bygningsrisiko i stedet for hva forholdet betyr for kjøper.",
                "recommended_fix_text": (
                    "Beskriv konsekvensen som hva forholdet betyr for kjøper i praksis, for eksempel kostnad, "
                    "bruksbegrensning, helse/sikkerhet eller myndighetsmessige følger."
                ),
                "suggested_rewrite_text": (
                    "Presiser konsekvensen ved å forklare hva forholdet betyr for kjøper i praksis, ikke bare hva som kan skje med konstruksjonen."
                ),
                "rewrite_strategy": "consequence_buyer_orientation_required",
                "evidence_snippets": [evidence] if evidence else [],
            },
        )


def _drop_good_enough_content_false_positives(
    report_text: str,
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
) -> None:
    standard_version = _detect_ns_standard_version(report_text)
    point_texts: Dict[str, str] = {}
    point_titles: Dict[str, str] = {}
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or ""))
        if not point_id:
            continue
        point_texts[point_id] = str(point.get("effective_span_text") or _get_effective_point_text(point) or _get_exact_point_text(point) or "")
        point_titles[point_id] = str(point.get("title") or point_id)
    for point_id, semantic_point in _semantic_arkat_points_by_id(analysis_output).items():
        semantic_text = str(semantic_point.get("raw_point_text") or "").strip()
        if semantic_text:
            point_texts[point_id] = semantic_text
        point_titles.setdefault(point_id, str(semantic_point.get("title") or point_id))

    def _keep_item(item: Dict[str, object]) -> bool:
        point_id = _normalize_point_id(
            str(
                item.get("exact_point_id")
                or _parse_runtime_point_ref_from_v16_finding(item)
                or _parse_point_id_from_v16_finding(item)
                or item.get("point_id")
                or item.get("component_id")
                or ""
            )
        )
        if not point_id or point_id not in point_texts:
            return True
        claims = _content_claim_keys(item)
        if not claims:
            return True
        point_text = str(point_texts.get(point_id, ""))
        signals = _segment_content_signals(point_text)
        point_tg = ""
        for point in detected_points:
            if isinstance(point, dict) and _normalize_point_id(str(point.get("point_id") or "")) == point_id:
                point_tg = _effective_point_tg(point, report_text)
                break
        if "årsak" in claims and _segment_has_qualifying_cause(
            point_text,
            standard_version=standard_version,
            point_title=point_titles.get(point_id, ""),
        ):
            claims.discard("årsak")
        if "risiko" in claims and (
            _point_has_qualifying_risk_text(point_text)
            or _point_allows_age_based_risk_relief(
                point_titles.get(point_id, ""),
                point_text,
                standard_version=standard_version,
            )
        ):
            claims.discard("risiko")
        if "konsekvens" in claims and _point_has_buyer_oriented_consequence_text(point_text):
            claims.discard("konsekvens")
        if "anbefalt_tiltak" in claims and signals["recommendation_present"]:
            claims.discard("anbefalt_tiltak")
        if "no_tg_hms" in claims and (
            signals["documentation_ok"]
            or (
                _segment_has_qualifying_cause(
                    point_text,
                    standard_version=standard_version,
                    point_title=point_titles.get(point_id, ""),
                )
                and _point_has_buyer_oriented_consequence_text(point_text)
            )
            or (signals["observation_present"] and signals["recommendation_present"])
        ):
            claims.discard("no_tg_hms")
        if "tg2_ark" in claims and point_tg == "TG2" and (
            _segment_has_qualifying_cause(
                point_text,
                standard_version=standard_version,
                point_title=point_titles.get(point_id, ""),
            )
            and (
                _point_has_qualifying_risk_text(point_text)
                or _point_allows_age_based_risk_relief(
                    point_titles.get(point_id, ""),
                    point_text,
                    standard_version=standard_version,
                )
            )
            and _point_has_buyer_oriented_consequence_text(point_text)
        ):
            claims.discard("tg2_ark")
        if "imperative_measure" in claims and (
            _point_has_non_imperative_recommendation(point_text) or not signals["recommendation_present"]
        ):
            claims.discard("imperative_measure")
        if "survey_limitation" in claims and _point_has_survey_limitation_text(point_text):
            claims.discard("survey_limitation")
        return len(claims) > 0

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            component_id = _normalize_point_id(str(component.get("component_id") or ""))
            if not component_id or component_id not in point_texts:
                continue
            issues = component.get("issues")
            if isinstance(issues, list):
                filtered_issues = []
                for issue in issues:
                    if not isinstance(issue, dict):
                        filtered_issues.append(issue)
                        continue
                    proxy_item = {
                        "point_id": component_id,
                        "exact_point_id": component_id,
                        "title": issue.get("summary") or "",
                        "message": issue.get("details") or issue.get("summary") or "",
                        "reason": issue.get("details") or "",
                        "recommended_fix_text": issue.get("details") or "",
                        "rule_id": " ".join(str(r) for r in (issue.get("rule_refs") or []) if isinstance(r, str)),
                    }
                    if _keep_item(proxy_item):
                        filtered_issues.append(issue)
                component["issues"] = filtered_issues
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                filtered_deductions = []
                for deduction in deductions:
                    if not isinstance(deduction, dict):
                        filtered_deductions.append(deduction)
                        continue
                    proxy_item = {
                        "point_id": component_id,
                        "exact_point_id": component_id,
                        "title": deduction.get("reason") or "",
                        "message": deduction.get("reason") or "",
                        "reason": deduction.get("reason") or "",
                        "recommended_fix_text": deduction.get("reason") or "",
                        "rule_id": deduction.get("rule_id") or "",
                    }
                    if _keep_item(proxy_item):
                        filtered_deductions.append(deduction)
                component["deductions"] = filtered_deductions

    all_findings = analysis_output.get("all_findings")
    if isinstance(all_findings, list):
        analysis_output["all_findings"] = [
            item for item in all_findings if not (isinstance(item, dict) and not _keep_item(item))
        ]

    top_issues = analysis_output.get("top_issues")
    if isinstance(top_issues, list):
        analysis_output["top_issues"] = [
            item for item in top_issues if not (isinstance(item, dict) and not _keep_item(item))
        ]

    top_score_drivers = analysis_output.get("top_score_drivers")
    if isinstance(top_score_drivers, list):
        analysis_output["top_score_drivers"] = [
            item for item in top_score_drivers if not (isinstance(item, dict) and not _keep_item(item))
        ]


def _normalize_remediation_intent(text: str) -> str:
    if not text or not isinstance(text, str):
        return "unspecified"
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    normalized = " ".join(normalized.split())
    if len(normalized) > 96:
        normalized = normalized[:96].strip()
    return normalized or "unspecified"


def _build_duplicate_safe_key(finding: Dict[str, object]) -> str:
    point_id = _parse_point_id_from_v16_finding(finding) or "GLOBAL"
    rule_id = str(finding.get("rule_id") or finding.get("finding_id") or "")
    if rule_id == "E_METHOD.garasje_avvik_uten_arkat":
        point_id = "GLOBAL"
    rule_family = _derive_rule_family(rule_id) or "UNKNOWN"
    category = str(finding.get("category") or _infer_category_from_rule_id(rule_id) or "UNKNOWN")
    remediation_intent = _normalize_remediation_intent(
        str(finding.get("rewrite_strategy") or finding.get("recommended_fix_text") or finding.get("title") or finding.get("message") or "")
    )
    return f"{point_id}|{rule_family}|{category}|{remediation_intent}"


def _dedupe_all_findings_duplicate_safe(analysis_output: Dict[str, object]) -> None:
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list) or not all_findings:
        return
    deduped: Dict[str, Dict[str, object]] = {}
    severity_rank = {"info": 0, "minor": 1, "major": 2}
    for entry in all_findings:
        if not isinstance(entry, dict):
            continue
        current = dict(entry)
        key = _build_duplicate_safe_key(current)
        current["duplicate_safe_key"] = key
        existing = deduped.get(key)
        if not existing:
            deduped[key] = current
            continue
        existing_rank = severity_rank.get(str(existing.get("severity") or "").lower(), 0)
        current_rank = severity_rank.get(str(current.get("severity") or "").lower(), 0)
        winner, other = (current, existing) if current_rank > existing_rank else (existing, current)
        winner_snips = winner.get("evidence_snippets")
        other_snips = other.get("evidence_snippets")
        same_exact_point = (
            _normalize_point_id(str(winner.get("exact_point_id") or ""))
            and _normalize_point_id(str(winner.get("exact_point_id") or "")) == _normalize_point_id(str(other.get("exact_point_id") or ""))
        )
        if same_exact_point:
            merged_snips: List[str] = []
            for src in (winner_snips, other_snips):
                if isinstance(src, list):
                    for s in src:
                        if isinstance(s, str) and s.strip() and s not in merged_snips:
                            merged_snips.append(s)
            if merged_snips:
                winner["evidence_snippets"] = merged_snips[:5]
        for field in ("recommended_fix_text", "suggested_rewrite_text", "rewrite_strategy", "point_id", "exact_point_id", "exact_point_text", "exact_point_title"):
            if not winner.get(field) and other.get(field):
                winner[field] = other[field]
        deduped[key] = winner
    analysis_output["all_findings"] = list(deduped.values())


def _parse_dommer_b_rule_triplet(item: Dict[str, object]) -> Tuple[str, str, str]:
    if not isinstance(item, dict):
        return ("", "", "")
    point_id = _arkat_item_point_id(item)
    rule_id = str(item.get("rule_id") or "").strip()
    m = re.match(r"^A_ARKAT(?:_SEMANTIC)?\.([A-Z_]+)\.([A-Z0-9_]+)$", rule_id)
    if not m:
        return ("", "", "")
    field_token = m.group(1).lower()
    error_type = m.group(2).upper()
    field_map = {
        "AARSAK": "aarsak",
        "RISIKO": "risiko",
        "KONSEKVENS": "konsekvens",
        "ANBEFALT_TILTAK": "anbefalt_tiltak",
        "TGIU": "tgiu",
    }
    field_name = field_map.get(field_token, field_token)
    return (point_id, field_name, error_type)


def _finalize_dommer_b_canonical_output(analysis_output: Dict[str, object]) -> None:
    """
    Final strict cleanup:
    - one canonical finding per (point_id, field, error_type) for Dommer B entries
    - suppress legacy consequence phrasing when canonical Dommer B entry exists
    - apply the same filtering to public lists (drivers/issues/feedback)
    """
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list) or not all_findings:
        return

    canonical_keys: set = set()
    consequence_points_with_canonical: set = set()
    for item in all_findings:
        if not isinstance(item, dict):
            continue
        triplet = _parse_dommer_b_rule_triplet(item)
        if all(triplet):
            canonical_keys.add(triplet)
            if triplet[1] == "konsekvens":
                consequence_points_with_canonical.add(triplet[0])
    if not canonical_keys:
        return

    # Also treat explicit consequence-status titles as canonical, even if the source
    # finding is not perfectly shaped as A_ARKAT.* (legacy bridge compatibility).
    for item in all_findings:
        if not isinstance(item, dict):
            continue
        point_id = _arkat_item_point_id(item)
        if not point_id:
            continue
        text_blob = _normalize_tg3_cost_text(
            f"{item.get('title', '')} {item.get('message', '')} {item.get('rule_id', '')} {item.get('finding_id', '')}"
        ).lower()
        if "konsekvens vurdert som" in text_blob:
            consequence_points_with_canonical.add(point_id)

    # Canonical source of truth: semantic pipeline point evaluation.
    # If Dommer B already produced a consequence status for a point, suppress legacy
    # consequence wording for that point in public UI lists.
    semantic_points = _semantic_arkat_points_by_id(analysis_output)
    for point_id, point in semantic_points.items():
        if not isinstance(point, dict):
            continue
        evaluation = point.get("evaluation")
        if not isinstance(evaluation, dict):
            continue
        field_results = evaluation.get("field_results")
        if not isinstance(field_results, dict):
            continue
        konsekvens = field_results.get("konsekvens")
        if not isinstance(konsekvens, dict):
            continue
        status = str(konsekvens.get("status") or "").strip().upper()
        if status.startswith("WRONG:") or status.startswith("MISSING"):
            consequence_points_with_canonical.add(_normalize_point_id(str(point_id or "")))

    def _filter_items(items: List[object]) -> List[object]:
        seen_dommer_b: set = set()
        seen_ui_keys: set = set()
        filtered: List[object] = []
        for item in items:
            if not isinstance(item, dict):
                filtered.append(item)
                continue
            triplet = _parse_dommer_b_rule_triplet(item)
            if all(triplet):
                if triplet in seen_dommer_b:
                    continue
                seen_dommer_b.add(triplet)
            point_id = _arkat_item_point_id(item)
            text_blob = _normalize_tg3_cost_text(
                f"{item.get('title', '')} {item.get('message', '')} {item.get('rule_id', '')} {item.get('finding_id', '')}"
            ).lower()
            # Suppress legacy wording whenever a canonical consequence finding exists for that point.
            is_legacy_consequence = (
                "konsekvens ikke kjøperorientert" in text_blob
                or str(item.get("rule_id") or "").strip() == "gate_konsekvens_not_buyer_oriented"
            )
            if point_id and is_legacy_consequence and point_id in consequence_points_with_canonical:
                continue
            # Hard UI/output dedup to avoid duplicates leaking via list merges.
            ui_key = (
                point_id,
                str(item.get("rule_id") or item.get("finding_id") or "").strip().upper(),
                _normalize_tg3_cost_text(str(item.get("title") or "")).lower(),
            )
            if ui_key in seen_ui_keys:
                continue
            seen_ui_keys.add(ui_key)
            filtered.append(item)
        return filtered

    analysis_output["all_findings"] = _filter_items(all_findings)
    for key in ("top_issues", "top_score_drivers", "score_drivers", "feedback_findings"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = _filter_items(items)


def _arkat_item_point_id(item: Dict[str, object]) -> str:
    return _normalize_point_id(
        str(
            item.get("exact_point_id")
            or item.get("point_id")
            or item.get("component_id")
            or _parse_runtime_point_ref_from_v16_finding(item)
            or _parse_point_id_from_v16_finding(item)
            or ""
        )
    )


def _claims_missing_anbefalt_tiltak(item: Dict[str, object]) -> bool:
    blob = _normalize_tg3_cost_text(
        f"{item.get('finding_id', '')} {item.get('rule_id', '')} {item.get('title', '')} "
        f"{item.get('message', '')} {item.get('reason', '')} {item.get('recommended_fix_text', '')}"
    ).lower()
    mentions_tiltak = bool(re.search(r"\b(?:anbefalt[_\s-]?tiltak|anbefalte\s+tiltak|tiltak)\b", blob))
    mentions_missing = bool(re.search(r"\b(?:missing|mangler|manglende)\b", blob))
    return mentions_tiltak and mentions_missing


def _is_freestanding_garage_arkat_item(item: Dict[str, object]) -> bool:
    rule_id = str(item.get("rule_id") or item.get("finding_id") or "").strip()
    if rule_id in {"E_METHOD.garasje_avvik_uten_arkat", "E_METHOD.garasje_tg_uten_full_arkat"}:
        return True
    blob = _normalize_tg3_cost_text(
        f"{item.get('point_id', '')} {item.get('exact_point_id', '')} {item.get('title', '')} "
        f"{item.get('message', '')} {item.get('exact_point_title', '')} {item.get('what_to_change', '')}"
    ).lower()
    return bool(
        re.search(r"\b(?:garasje|uthus|naust)\b", blob)
        and (
            "frittstående" in blob
            or "frittstaende" in blob
            or "uten varig opphold" in blob
            or "tg er ikke påkrevd" in blob
            or "tg er ikke pakrevd" in blob
            or "full arkat" in blob
            or "full ark" in blob
        )
    )


def _is_mechanical_missing_tiltak_item(item: Dict[str, object]) -> bool:
    if _is_freestanding_garage_arkat_item(item):
        return False
    rule_id = str(item.get("rule_id") or item.get("finding_id") or "").strip().upper()
    rewrite_strategy = str(item.get("rewrite_strategy") or "").strip().lower()
    blob = _normalize_tg3_cost_text(
        f"{item.get('title', '')} {item.get('message', '')} {item.get('recommended_fix_text', '')} {item.get('suggested_rewrite_text', '')}"
    ).lower()
    return (
        _claims_missing_anbefalt_tiltak(item)
        and (
            rewrite_strategy == "arkat_semantic_alignment"
            or rule_id.startswith("A_ARKAT.ANBEFALT_TILTAK")
            or rule_id.startswith("A_ARKAT_ANBEFALT_TILTAK")
            or "anbefalt_tiltak" in blob
            or "vurdert som missing" in blob
        )
    )



def _point_raw_text_has_action_wording(text: object) -> bool:
    normalized = _normalize_tg3_cost_text(str(text or "")).lower()
    if not normalized:
        return False
    return bool(
        re.search(
            r"(?ix)\b(?:"
            r"det\s+anbefales|anbefales\s+[aå]|anbefalt\s+[aå]|"
            r"b[oø]r\s+(?:utf[oø]res|skiftes|utbedres|kontrolleres|unders[oø]kes|vurderes|planlegges|totalrenoveres|renoveres|etableres)|"
            r"m[aå]\s+(?:skiftes(?:\s+ut)?(?:/utbedres)?|utbedres|totalrenoveres|renoveres|repareres|p[åa]regnes\s+tiltak)|"
            r"(?:skiftes\s+ut|utbedres|totalrenoveres|renoveres|repareres)\b|"
            r"utskiftning\s+av|utbedring\s+av|kontroll\s+og\s+eventuell\s+utbedring|tiltaksplan"
            r")\b",
            normalized,
        )
    )


def _dommer_b_tg3_points_with_raw_action(analysis_output: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    pipeline = analysis_output.get("arkat_semantic_pipeline") if isinstance(analysis_output, dict) else None
    points = pipeline.get("points") if isinstance(pipeline, dict) else []
    out: Dict[str, Dict[str, object]] = {}
    if not isinstance(points, list):
        return out
    for point in points:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or ""))
        tg = _normalize_tg_label(point.get("tg_grade") or point.get("tg"))
        if not point_id or tg != "TG3":
            continue
        raw = str(point.get("raw_point_text") or point.get("exact_point_text") or "")
        fields = point.get("extracted_fields") if isinstance(point.get("extracted_fields"), dict) else {}
        action_text = str(fields.get("anbefalt_tiltak") or "") if isinstance(fields, dict) else ""
        if _point_raw_text_has_action_wording(f"{action_text}\n{raw}"):
            out[point_id] = point
    return out


def _missing_tiltak_item_matches_action_point(item: Dict[str, object], action_points: Dict[str, Dict[str, object]]) -> bool:
    if not isinstance(item, dict) or not _claims_missing_anbefalt_tiltak(item) or not action_points:
        return False
    point_id = _arkat_item_point_id(item)
    if point_id and point_id in action_points:
        return True
    blob = _normalize_tg3_cost_text(
        " ".join(
            str(item.get(key) or "")
            for key in ("finding_id", "rule_id", "title", "message", "reason", "exact_point_title", "exact_point_text", "recommended_fix_text")
        )
        + " "
        + " ".join(str(snippet or "") for snippet in (item.get("evidence_snippets") or []) if isinstance(snippet, str))
    ).lower()
    for point_id, point in action_points.items():
        title = _normalize_tg3_cost_text(str(point.get("title") or "")).lower()
        raw = _normalize_tg3_cost_text(str(point.get("raw_point_text") or "")).lower()
        title_leaf = title.split(">")[-1].strip() if ">" in title else title
        if point_id and point_id.lower() in blob:
            return True
        if title_leaf and title_leaf in blob:
            return True
        if "kjølerom" in raw and "kjølerom" in blob:
            return True
        if "kjøleaggregat" in raw and "kjøleaggregat" in blob:
            return True
    return False


def _drop_missing_tiltak_when_raw_action_present(analysis_output: Dict[str, object]) -> None:
    if not isinstance(analysis_output, dict):
        return
    action_points = _dommer_b_tg3_points_with_raw_action(analysis_output)
    if not action_points:
        return

    for key in ("all_findings", "top_issues", "top_score_drivers", "score_drivers", "how_to_improve", "improvement_suggestions", "action_items", "recommended_fixes"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [
                item for item in items
                if not (isinstance(item, dict) and _missing_tiltak_item_matches_action_point(item, action_points))
            ]

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            component_id = _normalize_point_id(str(component.get("component_id") or ""))
            for key in ("issues", "deductions"):
                items = component.get(key)
                if isinstance(items, list):
                    component[key] = [
                        item for item in items
                        if not (
                            isinstance(item, dict)
                            and _claims_missing_anbefalt_tiltak(item)
                            and (component_id in action_points or _missing_tiltak_item_matches_action_point(item, action_points))
                        )
                    ]

def _drop_duplicate_missing_tiltak_findings(analysis_output: Dict[str, object]) -> None:
    """
    Keep one public deduction for the same point/field.
    Prefer the stronger human-facing TG3 missing-tiltak finding over the lower-level
    semantic field finding/deduction for the same exact point.
    """
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return
    for item in all_findings:
        if not isinstance(item, dict) or not _is_freestanding_garage_arkat_item(item):
            continue
        if str(item.get("rule_id") or "") == "TG3_MISSING_RECOMMENDED_ACTION":
            item["rule_id"] = "E_METHOD.garasje_avvik_uten_arkat"
        item["finding_id"] = str(item.get("finding_id") or "E_METHOD_garasje_avvik_uten_arkat_global").replace(
            "TG3_MISSING_TILTAK",
            "E_METHOD_garasje_avvik_uten_arkat",
        )
        item["category"] = "E"
        item["severity"] = "major"
        item["deduction_band"] = "Middels trekk"
        item["title"] = "Avvik i garasje/uthus/naust mangler full ARKAT"
        item["message"] = (
            "Rapporten beskriver et tydelig avvik i frittstående bygg uten varig opphold, men mangler full ARKAT. "
            "TG er ikke påkrevd, men avviket skal beskrives med årsak, risiko, konsekvens og anbefalt tiltak."
        )
        item["recommended_fix_text"] = (
            "Beskriv avviket med full ARKAT: hva som er observert, hvilken risiko det gir, hvilken praktisk konsekvens "
            "det har for kjøper, og hvilket tiltak som anbefales. TG er ikke nødvendig for slike bygg."
        )
        item["suggested_rewrite_text"] = (
            "Det er registrert et avvik i garasje/uthus/naust. Forholdet bør beskrives med full ARKAT: hva som er observert, "
            "hvilken risiko det gir videre, hvilken konsekvens det har for kjøper, og hvilket tiltak som anbefales. TG er ikke påkrevd for dette bygget."
        )
    deduped_garage: List[object] = []
    garage_seen = False
    for item in all_findings:
        if isinstance(item, dict) and _is_freestanding_garage_arkat_item(item):
            if garage_seen:
                continue
            garage_seen = True
        deduped_garage.append(item)
    all_findings = deduped_garage
    analysis_output["all_findings"] = all_findings
    tg_by_point = _dommer_b_tg_by_point(analysis_output)
    mechanical_missing_tiltak_points = {
        _arkat_item_point_id(item)
        for item in all_findings
        if isinstance(item, dict)
        and _arkat_item_point_id(item)
        and _is_mechanical_missing_tiltak_item(item)
        and tg_by_point.get(_arkat_item_point_id(item), "TG3") == "TG3"
    }
    non_tg3_mechanical_missing_tiltak_points = {
        _arkat_item_point_id(item)
        for item in all_findings
        if isinstance(item, dict)
        and _arkat_item_point_id(item)
        and _is_mechanical_missing_tiltak_item(item)
        and tg_by_point.get(_arkat_item_point_id(item), "TG3") != "TG3"
    }
    if non_tg3_mechanical_missing_tiltak_points:
        all_findings = [
            item for item in all_findings
            if not (
                isinstance(item, dict)
                and _arkat_item_point_id(item) in non_tg3_mechanical_missing_tiltak_points
                and _is_mechanical_missing_tiltak_item(item)
            )
        ]
        analysis_output["all_findings"] = all_findings
    stronger_points = {
        _arkat_item_point_id(item)
        for item in all_findings
        if isinstance(item, dict)
        and _arkat_item_point_id(item)
        and not _is_freestanding_garage_arkat_item(item)
        and _claims_missing_anbefalt_tiltak(item)
        and not _is_mechanical_missing_tiltak_item(item)
    }
    if not stronger_points and not mechanical_missing_tiltak_points:
        return

    def _keep_item(item: object) -> bool:
        if not isinstance(item, dict):
            return True
        point_id = _arkat_item_point_id(item)
        return not (point_id in stronger_points and _is_mechanical_missing_tiltak_item(item))

    analysis_output["all_findings"] = [item for item in all_findings if _keep_item(item)]
    for key in ("top_issues", "top_score_drivers", "score_drivers"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [item for item in items if _keep_item(item)]

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            component_id = _normalize_point_id(str(component.get("component_id") or ""))
            if component_id not in stronger_points:
                continue
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                component["deductions"] = [
                    deduction
                    for deduction in deductions
                    if not (isinstance(deduction, dict) and _is_mechanical_missing_tiltak_item(deduction))
                ]

    if stronger_points:
        return

    # If no human-facing duplicate exists, convert the mechanical semantic entry
    # into one clean public TG3 tiltak finding instead of leaking implementation wording.
    for item in all_findings:
        if not isinstance(item, dict) or not _is_mechanical_missing_tiltak_item(item):
            continue
        point_id = _arkat_item_point_id(item)
        if not point_id:
            continue
        point_title = str(item.get("exact_point_title") or item.get("point_title") or item.get("title") or "").strip()
        if point_title.lower().startswith("punkt "):
            point_title = ""
        label = f"Punkt {point_id}" + (f" {point_title}" if point_title else "")
        item["finding_id"] = f"A_ARKAT_{point_id.replace('.', '_')}_ANBEFALT_TILTAK_MISSING_ANBEFALT_TILTAK"
        item["rule_id"] = "A_ARKAT_SEMANTIC.ANBEFALT_TILTAK.MISSING_ANBEFALT_TILTAK"
        item["title"] = f"Punkt {point_id} mangler anbefalt tiltak"
        item["message"] = f"{label} mangler konkret anbefalt tiltak."
        item["recommended_fix_text"] = (
            f"Formuler anbefalt tiltak for punkt {point_id} tydelig, slik at kjøper vet hva som bør undersøkes, "
            "utbedres eller følges opp videre."
        )
        item["suggested_rewrite_text"] = (
            f"Punkt {point_id}: Det anbefales å undersøke og utbedre forholdet nærmere av kvalifisert fagperson."
        )
        item["rewrite_strategy"] = "tg3_missing_recommended_action"
        item["deduction_band"] = "Høyt trekk"
        item["severity"] = "major"
        item.pop("public_visibility", None)

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            component_id = _normalize_point_id(str(component.get("component_id") or ""))
            if component_id not in mechanical_missing_tiltak_points:
                continue
            point_title = str(component.get("component_title") or component.get("location") or "").strip()
            label = f"Punkt {component_id}" + (f" {point_title}" if point_title else "")
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                for deduction in deductions:
                    if not isinstance(deduction, dict) or not _is_mechanical_missing_tiltak_item(deduction):
                        continue
                    deduction["rule_id"] = "A_ARKAT_SEMANTIC.ANBEFALT_TILTAK.MISSING_ANBEFALT_TILTAK"
                    deduction["category_id"] = "A"
                    deduction["points"] = max(5, int(deduction.get("points") or 0))
                    deduction["severity"] = "major"
                    deduction["reason"] = f"{label} mangler konkret anbefalt tiltak."
                    deduction["suggested_rewrite_text"] = (
                        f"Punkt {component_id}: Det anbefales å undersøke og utbedre forholdet nærmere av kvalifisert fagperson."
                    )


def _is_consequence_only_segment_arkat_item(item: Dict[str, object]) -> bool:
    if not isinstance(item, dict):
        return False
    finding_id = str(item.get("finding_id") or item.get("rule_id") or "").strip().upper()
    if not finding_id.startswith("SEGMENT_ARKAT_"):
        return False
    claims = _content_claim_keys(item)
    if not claims:
        return False
    arkat_claims = {
        claim
        for claim in claims
        if claim in {"årsak", "risiko", "konsekvens", "anbefalt_tiltak", "tg2_ark"}
    }
    if "konsekvens" not in arkat_claims:
        return False
    # Overlap guard: only suppress when this SEGMENT_ARKAT finding is
    # exclusively about consequence (same deficiency as not-buyer-oriented).
    return not any(claim in arkat_claims for claim in {"årsak", "risiko", "anbefalt_tiltak", "tg2_ark"})


def _drop_overlapping_consequence_missing_findings(analysis_output: Dict[str, object]) -> None:
    """
    Priority 3 guard:
    If a point already has "Konsekvens ikke kjøperorientert", do not also keep
    a separate high "SEGMENT_ARKAT ... mangler konsekvens" finding for the same
    point when that SEGMENT_ARKAT finding is consequence-only.
    """
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list) or not all_findings:
        return

    non_buyer_points = {
        _arkat_item_point_id(item)
        for item in all_findings
        if isinstance(item, dict)
        and _arkat_item_point_id(item)
        and (
            str(item.get("rule_id") or "").strip() == "gate_konsekvens_not_buyer_oriented"
            or "konsekvens ikke kjøperorientert" in _normalize_tg3_cost_text(
                f"{item.get('title', '')} {item.get('message', '')}"
            ).lower()
        )
    }
    if not non_buyer_points:
        return

    def _keep_item(item: object) -> bool:
        if not isinstance(item, dict):
            return True
        point_id = _arkat_item_point_id(item)
        if point_id not in non_buyer_points:
            return True
        return not _is_consequence_only_segment_arkat_item(item)

    analysis_output["all_findings"] = [item for item in all_findings if _keep_item(item)]
    for key in ("top_issues", "top_score_drivers", "score_drivers", "feedback_findings"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [item for item in items if _keep_item(item)]

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            component_id = _normalize_point_id(str(component.get("component_id") or ""))
            if component_id not in non_buyer_points:
                continue
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                component["deductions"] = [
                    deduction
                    for deduction in deductions
                    if not (isinstance(deduction, dict) and _is_consequence_only_segment_arkat_item(deduction))
                ]
            issues = component.get("issues")
            if isinstance(issues, list):
                component["issues"] = [
                    issue
                    for issue in issues
                    if not (isinstance(issue, dict) and _is_consequence_only_segment_arkat_item(issue))
                ]


def _drop_missing_claims_when_semantic_field_correct(analysis_output: Dict[str, object]) -> None:
    """
    Final consistency guard:
    If Dommer B semantic evaluation says a field is CORRECT for a point,
    remove downstream/public "missing field" findings for that same point/field.
    """
    semantic_points = _semantic_arkat_points_by_id(analysis_output)
    if not semantic_points:
        return

    correct_fields_by_point: Dict[str, set] = {}
    for point_id, point in semantic_points.items():
        if not isinstance(point, dict):
            continue
        evaluation = point.get("evaluation")
        if not isinstance(evaluation, dict):
            continue
        field_results = evaluation.get("field_results")
        if not isinstance(field_results, dict):
            continue
        for field_name in ("aarsak", "risiko", "konsekvens", "anbefalt_tiltak"):
            result = field_results.get(field_name)
            status = str(result.get("status") or "").strip().upper() if isinstance(result, dict) else ""
            if status.startswith("CORRECT"):
                correct_fields_by_point.setdefault(_normalize_point_id(str(point_id or "")), set()).add(field_name)

    if not correct_fields_by_point:
        return

    def _is_missing_claim_for_field(item: Dict[str, object], field_name: str) -> bool:
        blob = _normalize_tg3_cost_text(
            f"{item.get('finding_id', '')} {item.get('rule_id', '')} {item.get('title', '')} "
            f"{item.get('message', '')} {item.get('reason', '')} {item.get('recommended_fix_text', '')}"
        ).lower()
        if not re.search(r"\b(?:missing|mangler|manglende)\b", blob):
            return False
        if field_name == "anbefalt_tiltak":
            return bool(re.search(r"\b(?:anbefalt[_\s-]?tiltak|anbefalte\s+tiltak|tiltak)\b", blob))
        if field_name == "konsekvens":
            return "konsekvens" in blob
        if field_name == "risiko":
            return "risiko" in blob
        if field_name == "aarsak":
            return bool(re.search(r"\b(?:årsak|arsak)\b", blob))
        return False

    def _keep_item(item: object) -> bool:
        if not isinstance(item, dict):
            return True
        point_id = _arkat_item_point_id(item)
        if not point_id:
            return True
        correct_fields = correct_fields_by_point.get(point_id) or set()
        if not correct_fields:
            return True
        for field_name in correct_fields:
            if _is_missing_claim_for_field(item, field_name):
                return False
        return True

    all_findings = analysis_output.get("all_findings")
    if isinstance(all_findings, list):
        analysis_output["all_findings"] = [item for item in all_findings if _keep_item(item)]

    for key in ("top_issues", "top_score_drivers", "score_drivers", "feedback_findings"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [item for item in items if _keep_item(item)]

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            point_id = _normalize_point_id(str(component.get("component_id") or ""))
            if not point_id:
                continue
            correct_fields = correct_fields_by_point.get(point_id) or set()
            if not correct_fields:
                continue
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                component["deductions"] = [
                    d for d in deductions
                    if not (
                        isinstance(d, dict)
                        and any(_is_missing_claim_for_field(d, field_name) for field_name in correct_fields)
                    )
                ]
            issues = component.get("issues")
            if isinstance(issues, list):
                component["issues"] = [
                    i for i in issues
                    if not (
                        isinstance(i, dict)
                        and any(_is_missing_claim_for_field(i, field_name) for field_name in correct_fields)
                    )
                ]


def _drop_tg3_missing_tiltak_false_positives_from_point_text(
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
    report_text: str = "",
) -> None:
    """
    Drop false positives where a TG3 point already contains explicit actionable tiltak
    in the point text, but downstream layers still emit "mangler anbefalt tiltak".
    """
    point_text_by_id: Dict[str, str] = {}
    tg_by_id: Dict[str, str] = {}
    for point in detected_points or []:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or point.get("numeric_id") or point.get("native_label") or ""))
        if not point_id:
            continue
        point_text_by_id[point_id] = str(
            point.get("effective_span_text")
            or point.get("exact_span_text")
            or point.get("span_text")
            or point.get("excerpt")
            or ""
        )
        tg_by_id[point_id] = _effective_point_tg(point, report_text)

    if not point_text_by_id:
        return

    def _has_actionable_tiltak_text(text: str) -> bool:
        low = _normalize_tg3_cost_text(text or "").lower()
        if not low:
            return False
        # Explicit recommendation language should count as tiltak present.
        if re.search(
            r"(?ix)\b(?:det\s+anbefales|anbefales\s+å|b[øo]r\s+\w+|m[åa]\s+\w+|tiltak\s*:|konsekvens/tiltak)\b",
            low,
        ):
            return True
        return False

    def _has_causal_aarsak_text(text: str) -> bool:
        low = _normalize_tg3_cost_text(text or "").lower()
        if not low:
            return False
        return bool(
            re.search(r"(?ix)\b(?:årsak|arsak|skyldes|forårsaket|grunnet|på grunn av|oppstod)\b", low)
            or re.search(r"(?ix)\b(?:det\s+er\s+avvik|det\s+er\s+påvist)\b", low)
        )

    def _has_buyer_oriented_konsekvens_text(text: str) -> bool:
        low = _normalize_tg3_cost_text(text or "").lower()
        if not low:
            return False
        if _point_has_buyer_oriented_consequence_text(low):
            return True
        return bool(
            re.search(
                r"(?ix)\b(?:for\s+kjøper|kjoeper|sikkerhetsrisiko|brannrisiko|personskade|kostnad|"
                r"økte?\s+kostnader|vedlikeholdsbehov|funksjonssvikt|følgeskader?)\b",
                low,
            )
        )

    false_positive_points_by_field: Dict[str, set] = {}
    for point_id, text in point_text_by_id.items():
        if str(tg_by_id.get(point_id) or "").upper() != "TG3":
            continue
        fields = set()
        if _has_actionable_tiltak_text(text):
            fields.add("anbefalt_tiltak")
        if _has_causal_aarsak_text(text):
            fields.add("aarsak")
        if _has_buyer_oriented_konsekvens_text(text):
            fields.add("konsekvens")
        if fields:
            false_positive_points_by_field[point_id] = fields
    if not false_positive_points_by_field:
        return

    def _missing_claim_field(item: Dict[str, object]) -> str:
        blob = _normalize_tg3_cost_text(
            f"{item.get('finding_id', '')} {item.get('rule_id', '')} {item.get('title', '')} "
            f"{item.get('message', '')} {item.get('reason', '')} {item.get('recommended_fix_text', '')}"
        ).lower()
        if not re.search(r"\b(?:missing|mangler|manglende)\b", blob):
            return ""
        if re.search(r"\b(?:anbefalt[_\s-]?tiltak|anbefalte\s+tiltak|tiltak)\b", blob):
            return "anbefalt_tiltak"
        if "konsekvens" in blob:
            return "konsekvens"
        if re.search(r"\b(?:årsak|arsak)\b", blob):
            return "aarsak"
        return ""

    def _item_has_field_evidence(item: Dict[str, object], field_name: str) -> bool:
        evidence_blob = _normalize_tg3_cost_text(
            f"{item.get('exact_point_text', '')} "
            f"{' '.join([str(x) for x in (item.get('evidence_snippets') or []) if isinstance(x, str)])} "
            f"{item.get('message', '')}"
        ).lower()
        if field_name == "anbefalt_tiltak":
            return _has_actionable_tiltak_text(evidence_blob)
        if field_name == "aarsak":
            return _has_causal_aarsak_text(evidence_blob)
        if field_name == "konsekvens":
            return _has_buyer_oriented_konsekvens_text(evidence_blob)
        return False

    def _keep_item(item: object) -> bool:
        if not isinstance(item, dict):
            return True
        rule_id = str(item.get("rule_id") or item.get("finding_id") or "").strip()
        if rule_id.startswith("A_ARKAT_SEMANTIC."):
            return True
        claim_field = _missing_claim_field(item)
        # Direct evidence-based suppression: if this item itself shows explicit field
        # evidence, it cannot simultaneously be a missing-field finding.
        if claim_field and _item_has_field_evidence(item, claim_field):
            return False
        point_id = _arkat_item_point_id(item)
        fields = false_positive_points_by_field.get(point_id) or set()
        if not claim_field or not fields:
            return True
        return claim_field not in fields

    all_findings = analysis_output.get("all_findings")
    if isinstance(all_findings, list):
        analysis_output["all_findings"] = [item for item in all_findings if _keep_item(item)]

    for key in ("top_issues", "top_score_drivers", "score_drivers", "feedback_findings"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [item for item in items if _keep_item(item)]

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            point_id = _normalize_point_id(str(component.get("component_id") or ""))
            fields = false_positive_points_by_field.get(point_id) or set()
            if not fields:
                continue
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                component["deductions"] = [
                    d for d in deductions
                    if not (
                        isinstance(d, dict)
                        and (_missing_claim_field(d) in fields)
                    )
                ]
            issues = component.get("issues")
            if isinstance(issues, list):
                component["issues"] = [
                    i for i in issues
                    if not (
                        isinstance(i, dict)
                        and (_missing_claim_field(i) in fields)
                    )
                ]


_PUBLIC_BAND_RANK = {
    "Ikke scoretrekk": 0,
    "Lavt trekk": 1,
    "Middels trekk": 2,
    "Høyt trekk": 3,
}


def _public_band_for_item(item: Dict[str, object]) -> str:
    band = str(item.get("deduction_band") or "").strip()
    if band in _PUBLIC_BAND_RANK:
        return band
    severity = str(item.get("severity") or "").strip().lower()
    if severity in {"critical", "major"}:
        return "Høyt trekk"
    if severity == "minor":
        return "Lavt trekk"
    return "Middels trekk"


def _overview_band_from_public_band(band: str) -> str:
    return {
        "Høyt trekk": "high",
        "Middels trekk": "medium",
        "Lavt trekk": "low",
        "Ikke scoretrekk": "none",
    }.get(str(band or "").strip(), "none")


def _is_public_scored_finding(item: Dict[str, object]) -> bool:
    return _PUBLIC_BAND_RANK.get(_public_band_for_item(item), 0) > 0


def _score_category_deduction_map(analysis_output: Dict[str, object]) -> Dict[str, int]:
    rows = analysis_output.get("score_by_category")
    if not isinstance(rows, list):
        return {}
    result: Dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        category = str(row.get("category_id") or row.get("category") or "").strip().upper()
        if not category:
            continue
        try:
            deduction = int(float(row.get("deduction") or 0))
        except (TypeError, ValueError):
            deduction = 0
        result[category] = deduction
    return result


def _is_long_sentence_language_finding(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    if _public_finding_category(item) != "D":
        return False
    blob = _normalize_tg3_cost_text(
        " ".join(
            str(item.get(key) or "")
            for key in ("finding_id", "rule_id", "title", "message", "rewrite_strategy")
        )
    ).lower()
    return bool(
        "d_001" in blob
        or "long_sentences" in blob
        or "lange setninger" in blob
    )


def _normalize_zero_score_language_findings(analysis_output: Dict[str, object]) -> None:
    if not isinstance(analysis_output, dict):
        return
    if _score_category_deduction_map(analysis_output).get("D", 0) != 0:
        return

    def _normalize_item(item: object) -> None:
        if not _is_long_sentence_language_finding(item):
            return
        item["deduction_band"] = "Ikke scoretrekk"
        item["score_impact"] = "informational_language_observation"
        item["deduction"] = 0
        item["deduction_points"] = 0
        item["points"] = 0

    for key in ("all_findings", "top_issues", "top_score_drivers", "score_drivers", "how_to_improve", "improvement_suggestions", "action_items", "recommended_fixes"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            for item in items:
                _normalize_item(item)

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in findings:
            if not isinstance(component, dict):
                continue
            for key in ("deductions", "issues"):
                items = component.get(key)
                if isinstance(items, list):
                    for item in items:
                        _normalize_item(item)


def _public_finding_category(item: Dict[str, object]) -> str:
    category = str(item.get("category") or item.get("category_id") or "").strip().upper()
    if category in {"A", "B", "C", "D", "E", "F"}:
        return category
    rule_id = str(item.get("rule_id") or item.get("finding_id") or "")
    inferred = _infer_category_from_rule_id(rule_id)
    return str(inferred or "").strip().upper()


def _is_reconciled_public_score_driver_finding(
    analysis_output: Dict[str, object],
    item: Dict[str, object],
) -> bool:
    gate_effect = item.get("gate_effect")
    if isinstance(gate_effect, dict) and gate_effect.get("blocks_96_gate"):
        return True
    if not _is_public_scored_finding(item):
        return False
    category = _public_finding_category(item)
    category_deductions = _score_category_deduction_map(analysis_output)
    if category and category_deductions:
        return category_deductions.get(category, 0) > 0
    return True


def _is_mechanical_arkat_public_finding(item: Dict[str, object]) -> bool:
    rule_id = str(item.get("rule_id") or item.get("finding_id") or "").strip()
    rewrite_strategy = str(item.get("rewrite_strategy") or "").strip().lower()
    title = str(item.get("title") or "").strip()
    message = str(item.get("message") or "").strip()
    blob = _normalize_tg3_cost_text(f"{rule_id} {title} {message} {rewrite_strategy}").lower()
    if _public_finding_category(item) == "A" and _is_public_scored_finding(item):
        return False
    if rewrite_strategy == "arkat_semantic_alignment":
        return True
    return bool(
        re.search(
            r"\bpunkt\s+[^\s:]+:\s+(?:årsak|arsak|risiko|konsekvens|anbefalt(?:e)?(?:\s+tiltak)?)\s+vurdert\s+som\s+(?:wrong|missing|correct)",
            blob,
        )
    )


def _is_low_quality_public_suggested_rewrite_text(text: object) -> bool:
    if not isinstance(text, str):
        return False
    normalized = _normalize_tg3_cost_text(text).strip()
    if not normalized:
        return False
    low = normalized.lower()
    field_label_hits = sum(
        low.count(marker)
        for marker in (
            "årsak:",
            "arsak:",
            "risiko:",
            "konsekvens:",
            "anbefalt tiltak:",
            "anbefalte tiltak:",
        )
    )
    if "ikke følges opp" in low:
        return True
    if field_label_hits >= 2 and len(normalized) >= 160:
        return True
    if low.startswith("punkt ") and "det er risiko for videre utvikling dersom" in low:
        return True
    if low.startswith("punkt ") and "konsekvensen bør presiseres praktisk ved å forklare hva" in low:
        return True
    if low.startswith("punkt ") and "det anbefales å utbedre eller undersøke nærmere der" in low:
        return True
    # Guard against leaked LLM analysis chatter / non-user-facing meta text.
    bad_english_markers = (
        "looking at this report point",
        "let me check the full point text",
        "after reviewing the full point text",
        "all four arkat fields",
        "the full point text contains",
        "this appears to be",
    )
    if any(marker in low for marker in bad_english_markers):
        return True
    if "**årsak:**" in low or "**risiko:**" in low or "**konsekvens:**" in low or "**anbefalt tiltak:**" in low:
        return True
    if low.count("mising") >= 2 or low.count("missing") >= 2:
        return True
    if normalized.count("\n") >= 6 and ("årsak:" in low or "risiko:" in low or "konsekvens:" in low):
        return True
    return False


def _is_tg3_missing_recommended_action_public_finding(item: Dict[str, object]) -> bool:
    blob = _normalize_tg3_cost_text(
        f"{item.get('finding_id', '')} {item.get('rule_id', '')} {item.get('title', '')} "
        f"{item.get('message', '')} {item.get('rewrite_strategy', '')}"
    ).lower()
    return (
        "tg3_missing_recommended_action" in blob
        or "tg3_missing_tiltak" in blob
        or "tg3 mangler anbefalt tiltak" in blob
    )


def _clean_tg3_missing_recommended_action_text(item: Dict[str, object]) -> str:
    point_id = _normalize_point_id(
        str(
            item.get("exact_point_id")
            or item.get("point_id")
            or _parse_runtime_point_ref_from_v16_finding(item)
            or _parse_point_id_from_v16_finding(item)
            or ""
        )
    )
    label = f"Punkt {point_id}" if point_id else "TG3-punktet"
    return f"{label}: Det anbefales å undersøke og utbedre forholdet nærmere av kvalifisert fagperson."


def _clean_feedback_example_text(example_text: object, fallback_text: object = "") -> str:
    primary = str(example_text or "").strip()
    fallback = str(fallback_text or "").strip()
    if primary and not _is_low_quality_public_suggested_rewrite_text(primary):
        return primary
    if fallback and not _is_low_quality_public_suggested_rewrite_text(fallback):
        return fallback
    return ""



def _age_only_blob_is_internal_water_or_drain_service_life(blob: str) -> bool:
    low = _normalize_tg3_cost_text(blob or "").lower()
    if not low:
        return False
    has_internal_pipe = bool(
        re.search(r"(?ix)\binnvendige?\s+(?:vannledninger|avl[oø]psledninger|avl[oø]psr[oø]r)\b", low)
        or re.search(r"(?ix)\btekniske\s+installasjoner\b.{0,80}\b(?:vannledninger|avl[oø]psr[oø]r)\b", low)
        or "fremtind-water-pipes" in low
        or "fremtind-drain-pipes" in low
    )
    has_service_life = bool(
        re.search(r"(?ix)\b(?:mer\s+enn\s+)?halvparten\s+av\s+forventet\s+brukstid\s+er\s+passert\b", low)
        or re.search(r"(?ix)\bforventet\s+(?:brukstid|levetid)\b", low)
        or "passert" in low and "brukstid" in low
    )
    return has_internal_pipe and has_service_life


def _age_only_item_only_refs_allowed_internal_pipe_service_life(item: Dict[str, object], segment_text: str) -> bool:
    if not isinstance(item, dict):
        return False
    snippets = [snippet for snippet in (item.get("evidence_snippets") or []) if isinstance(snippet, str) and snippet.strip()]
    if snippets:
        age_snippets = [snippet for snippet in snippets if _segment_relies_on_age_only_reason(snippet) or "brukstid" in snippet.lower()]
        if age_snippets and all(_age_only_blob_is_internal_water_or_drain_service_life(snippet) for snippet in age_snippets):
            return True
    blob = " ".join(
        str(item.get(key) or "")
        for key in ("finding_id", "rule_id", "title", "message", "reason", "exact_point_id", "point_id", "exact_point_title", "exact_point_text", "recommended_fix_text")
    )
    return _age_only_blob_is_internal_water_or_drain_service_life(f"{blob}\n{segment_text}")

def _is_age_only_candidate(item: Dict[str, object]) -> bool:
    blob = _normalize_tg3_cost_text(
        f"{item.get('finding_id', '')} {item.get('rule_id', '')} {item.get('title', '')} {item.get('message', '')}"
    ).lower()
    return (
        "age_only" in blob
        or "alder alene" in blob
        or "hovedsakelig med alder" in blob
        or "begrunnet med alder alene" in blob
    )


def _segment_has_concrete_non_age_support(segment_text: str) -> bool:
    if not segment_text:
        return False
    text = _normalize_tg3_cost_text(segment_text).lower()
    condition_terms = (
        "skade",
        "svikt",
        "sprek",
        "sprekk",
        "fukt",
        "lekk",
        "råte",
        "korros",
        "rust",
        "deform",
        "sig",
        "utetthet",
        "slitasje",
        "flass",
        "avvik",
        "misfarging",
        "sopp",
        "mugg",
        "bevegelse",
        "svelling",
        "brudd",
        "løs",
        "ufagmessig",
        "kondens",
        "punktert",
        "hulrom",
        "mose",
        "skjot",
        "skjøt",
        "beslag",
        "mangelfull ventilasjon",
        "slukmansjett",
        "mansjett",
        "klemring",
        "ikke synlig slukmansjett",
        "begrenset inspeksjonsmulighet",
        "begrenset inspeksjonsmulighet",
        "ikke funksjonstestet",
        "ikke sikre observasjoner",
        "nærmere undersøkelser",
        "tiltak kan bli nødvendig",
        "usikkert om",
        "fuktinntrengning",
        "drypplekkasje",
        "vannrør",
        "rørene",
        "veggen",
        "sperre",
        "rafter",
        "taksperre",
        "taksperrene",
    )
    evidence_terms = (
        "observert",
        "registrert",
        "påvist",
        "målt",
        "måledata",
        "forhøyet",
        "indikasjon",
        "tegn til",
    )
    if _point_has_explicit_section_text(segment_text, "årsak"):
        cause_text = _extract_arkat_section_text(segment_text, "årsak").lower()
    else:
        cause_text = text
    has_condition = any(term in cause_text for term in condition_terms)
    has_global_condition = any(term in text for term in condition_terms)
    has_evidence = any(term in cause_text for term in evidence_terms) or bool(re.search(r"\b\d+(?:[,.]\d+)?\s*(?:%|mm|cm|m2|m²)\b", cause_text))
    has_global_evidence = any(term in text for term in evidence_terms) or bool(re.search(r"\b\d+(?:[,.]\d+)?\s*(?:%|mm|cm|m2|m²)\b", text))
    has_non_age_cause = (
        bool(re.search(r"(arsak|årsak):\s*[^\n]{0,220}(fukt|lekk|råte|sprek|svikt|kondens|misfarging|mose|fall|manglende|hulrom|korros|utett|punktert|slukmansjett|mansjett|klemring|hull)", text))
        or bool(re.search(r"tg[23]\s+vurderes\s+da\s+[^\n]{0,220}(fukt|lekk|råte|sprek|svikt|kondens|misfarging|mose|fall|manglende|hulrom|korros|utett|punktert|slukmansjett|mansjett|klemring|hull)", text))
    )
    return has_condition or has_global_condition or has_non_age_cause or (has_evidence and has_condition) or (has_global_evidence and has_global_condition)


def _point_title_lookup_keys(point_title: object) -> List[str]:
    raw_title = _normalize_tg3_cost_text(str(point_title or "")).strip()
    if not raw_title:
        return []
    candidates = [raw_title]
    cleaned = re.sub(r"(?i)\bg[aå]\s+til\s+side\b", "", raw_title).strip(" |-:")
    if cleaned and cleaned not in candidates:
        candidates.append(cleaned)
    if ">" in cleaned:
        leaf = cleaned.split(">")[-1].strip(" |-:")
        if leaf and leaf not in candidates:
            candidates.append(leaf)
    unique: List[str] = []
    seen: set = set()
    for candidate in candidates:
        normalized = _normalize_tg3_cost_text(candidate).lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def _report_context_for_point(report_text: str, point_id: str, window: int = 900) -> str:
    normalized = _normalize_tg3_cost_text(report_text or "")
    target = _normalize_point_id(point_id)
    if not normalized or not target:
        return ""
    match = re.search(rf"(?im)\b{re.escape(target)}\b", normalized)
    if not match:
        return ""
    start = max(0, match.start() - 80)
    end = min(len(normalized), match.end() + window)
    return normalized[start:end]


def _report_context_for_point_title(report_text: str, point_title: object, window: int = 900) -> str:
    normalized = _normalize_tg3_cost_text(report_text or "")
    if not normalized:
        return ""
    for lookup_key in _point_title_lookup_keys(point_title):
        if len(lookup_key) < 4:
            continue
        match = re.search(re.escape(lookup_key), normalized.lower())
        if not match:
            continue
        start = max(0, match.start() - 80)
        end = min(len(normalized), match.end() + window)
        return normalized[start:end]
    return ""


def _report_context_for_snippet(report_text: str, snippet: object, window: int = 1200) -> str:
    normalized_report = _normalize_tg3_cost_text(report_text or "")
    normalized_snippet = _normalize_tg3_cost_text(str(snippet or "")).strip()
    if not normalized_report or not normalized_snippet:
        return ""

    candidates: List[str] = []
    if len(normalized_snippet) >= 24:
        candidates.append(normalized_snippet[:160].strip())
    for raw_line in normalized_snippet.splitlines():
        line = raw_line.strip().strip("\"'")
        if len(line) >= 24:
            candidates.append(line[:160].strip())

    seen: set = set()
    for candidate in candidates:
        key = candidate.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        match = re.search(re.escape(candidate), normalized_report, re.IGNORECASE)
        if not match:
            continue
        start = max(0, match.start() - 200)
        end = min(len(normalized_report), match.end() + window)
        return normalized_report[start:end]
    return ""


def _report_requires_vinterhage_ns3600_note(report_text: str) -> bool:
    normalized = _normalize_tg3_cost_text(report_text or "").lower()
    if not normalized or "vinterhage" not in normalized:
        return False
    has_vinterhage_building_section = bool(
        re.search(r"(?is)\bvinterhage\b.{0,220}\b(?:bruksareal|romfordeling|lovlighet|kommentar|1\.etasje|etasje)\b", normalized)
        or re.search(r"(?is)\b(?:bruksareal|romfordeling|lovlighet|kommentar)\b.{0,220}\bvinterhage\b", normalized)
    )
    if not has_vinterhage_building_section:
        return False

    exclusion_patterns = (
        r"(?is)\bvinterhage\b.{0,520}\bikke\b.{0,80}\b(?:tilstandsvurdert|vurdert|omfattet)\b.{0,320}\b(?:forskrift(?:en)?\s+til\s+avhendingslova|avhendingslova|ns\s*3600)\b",
        r"(?is)\b(?:bygget|bygningen|tilleggsbygg(?:et|ing(?:en|er))?)\b.{0,240}\bikke\b.{0,80}\b(?:tilstandsvurdert|vurdert|omfattet)\b.{0,320}\b(?:forskrift(?:en)?\s+til\s+avhendingslova|avhendingslova|ns\s*3600)\b",
        r"(?is)\b(?:ikke|ikkje)\b.{0,80}\b(?:tilstandsvurdert|vurdert|omfattet)\b.{0,320}\b(?:forskrift(?:en)?\s+til\s+avhendingslova|avhendingslova|ns\s*3600)\b",
        r"(?is)\btilleggsbygg(?:et|ing(?:en|er))?\b.{0,260}\bikke\b.{0,80}\btilstandsvurdert\b",
    )
    return any(re.search(pattern, normalized) for pattern in exclusion_patterns)


def _report_vinterhage_ns3600_excerpt(report_text: str) -> str:
    normalized = _normalize_tg3_cost_text(report_text or "")
    if not normalized:
        return ""
    patterns = (
        r"(?is)\bvinterhage\b.{0,520}\bikke\b.{0,80}\b(?:tilstandsvurdert|vurdert|omfattet)\b.{0,320}\b(?:forskrift(?:en)?\s+til\s+avhendingslova|avhendingslova|ns\s*3600)\b",
        r"(?is)\b(?:bygget|bygningen|tilleggsbygg(?:et|ing(?:en|er))?)\b.{0,240}\bikke\b.{0,80}\b(?:tilstandsvurdert|vurdert|omfattet)\b.{0,320}\b(?:forskrift(?:en)?\s+til\s+avhendingslova|avhendingslova|ns\s*3600)\b",
        r"(?is)\b(?:ikke|ikkje)\b.{0,80}\b(?:tilstandsvurdert|vurdert|omfattet)\b.{0,320}\b(?:forskrift(?:en)?\s+til\s+avhendingslova|avhendingslova|ns\s*3600)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return match.group(0).strip()
    return _report_excerpt(report_text, r"(?is)\bvinterhage\b.{0,420}")


def _report_point_has_concrete_non_age_support(
    report_text: str,
    point_id: str,
    point_title: object = "",
    window: int = 2200,
) -> bool:
    context = _report_context_for_point(report_text, point_id, window=window)
    if not context and point_title:
        context = _report_context_for_point_title(report_text, point_title, window=window)
    if not context:
        return False
    return _segment_has_concrete_non_age_support(context)



def _analysis_has_age_only_public_finding(analysis_output: Dict[str, object]) -> bool:
    for key in ("all_findings", "top_issues", "top_score_drivers", "score_drivers"):
        items = analysis_output.get(key) if isinstance(analysis_output, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and _is_age_only_candidate(item):
                return True
    return False


def _scrub_age_only_category_summary_without_finding(analysis_output: Dict[str, object]) -> None:
    if not isinstance(analysis_output, dict) or _analysis_has_age_only_public_finding(analysis_output):
        return
    breakdown = analysis_output.get("category_breakdown")
    if not isinstance(breakdown, list):
        return
    for entry in breakdown:
        if not isinstance(entry, dict):
            continue
        category = str(entry.get("category") or entry.get("category_id") or "").strip().upper()
        if not category.startswith("B"):
            continue
        summary = _normalize_tg3_cost_text(str(entry.get("summary") or ""))
        if re.search(r"(?i)hovedsakelig\s+.*alder|alder\s+uten\s+tilstrekkelig\s+faglig\s+begrunnelse|age_only", summary):
            entry["summary"] = "Ingen scoretrekk i denne kategorien."

def _drop_age_only_false_positives(
    report_text: str,
    analysis_output: Dict[str, object],
    detected_points: List[Dict[str, object]],
) -> None:
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list) or not all_findings:
        return

    segment_by_point: Dict[str, str] = {}
    title_by_point: Dict[str, str] = {}
    segment_by_title: Dict[str, str] = {}
    standard_version = _detect_ns_standard_version(report_text)
    for p in detected_points:
        if not isinstance(p, dict):
            continue
        point_id = str(p.get("point_id") or "").strip()
        point_title = str(p.get("title") or point_id).strip()
        combined_parts = [
            str(_get_exact_point_text(p) or "").strip(),
            str(p.get("effective_span_text") or "").strip(),
            str(p.get("linked_summary_text") or "").strip(),
            str(_get_effective_point_text(p) or "").strip(),
            _report_context_for_point(report_text, point_id),
            _report_context_for_point_title(report_text, point_title),
        ]
        combined = "\n".join(part for part in combined_parts if part)
        if point_id and combined:
            segment_by_point[point_id] = combined
            title_by_point[point_id] = point_title
        for lookup_key in _point_title_lookup_keys(point_title):
            if combined and lookup_key not in segment_by_title:
                segment_by_title[lookup_key] = combined

    def _is_supported(item: Dict[str, object]) -> bool:
        if not _is_age_only_candidate(item):
            return False
        evidence_candidates: List[str] = []
        point_id = str(
            item.get("exact_point_id")
            or _parse_runtime_point_ref_from_v16_finding(item)
            or _parse_point_id_from_v16_finding(item)
            or ""
        ).strip()
        segment_text = segment_by_point.get(point_id, "")
        point_title = str(item.get("exact_point_title") or item.get("title") or point_id).strip()
        if not segment_text:
            for lookup_key in _point_title_lookup_keys(point_title):
                segment_text = segment_by_title.get(lookup_key, "")
                if segment_text:
                    break
        if not segment_text:
            exact_point_text = str(item.get("exact_point_text") or "").strip()
            if exact_point_text:
                evidence_candidates.append(exact_point_text)
            for snippet in (item.get("evidence_snippets") or []):
                if isinstance(snippet, str) and snippet.strip():
                    evidence_candidates.append(snippet.strip())
            message = str(item.get("message") or "").strip()
            if message:
                evidence_candidates.append(message)
            for candidate in evidence_candidates:
                segment_text = _report_context_for_snippet(report_text, candidate)
                if segment_text:
                    break
        roof_blob = _normalize_tg3_cost_text(
            "\n".join(
                candidate for candidate in [
                    point_id,
                    point_title,
                    str(item.get("message") or ""),
                    str(item.get("title") or ""),
                    str(item.get("exact_point_text") or ""),
                    *evidence_candidates,
                ]
                if candidate
            )
        ).lower()
        if _age_only_item_only_refs_allowed_internal_pipe_service_life(item, segment_text):
            return True
        if (
            "taktekking" in roof_blob
            or "undertak, lekter og yttertekking" in roof_blob
        ) and _report_point_has_concrete_non_age_support(
            report_text,
            "4.2",
            "Undertak, lekter og yttertekking (taktekkingen)",
        ):
            return True
        if _segment_has_concrete_non_age_support(segment_text):
            return True
        if point_id == "4.2" and _report_point_has_concrete_non_age_support(
            report_text,
            point_id,
            title_by_point.get(point_id, point_title),
        ):
            return True
        if standard_version == "2025":
            return _point_allows_age_only_under_ns2025(title_by_point.get(point_id, point_title), segment_text)
        return False

    analysis_output["all_findings"] = [
        f for f in all_findings
        if not (isinstance(f, dict) and _is_supported(f))
    ]

    top_issues = analysis_output.get("top_issues")
    if isinstance(top_issues, list):
        analysis_output["top_issues"] = [
            item for item in top_issues
            if not (isinstance(item, dict) and _is_supported(item))
        ]

    top_score_drivers = analysis_output.get("top_score_drivers")
    if isinstance(top_score_drivers, list):
        analysis_output["top_score_drivers"] = [
            item for item in top_score_drivers
            if not (isinstance(item, dict) and _is_supported(item))
        ]

    _scrub_age_only_category_summary_without_finding(analysis_output)


def _drop_report_level_false_positives(report_text: str, analysis_output: Dict[str, object]) -> None:
    if not isinstance(analysis_output, dict):
        return
    should_keep_areal = _report_requires_areal_ns3940_2023(report_text)
    should_keep_egenerklaring = _report_requires_egenerklaring_missing(report_text)

    def _keep(item: object) -> bool:
        if not isinstance(item, dict):
            return True
        rule_id = str(item.get("rule_id") or item.get("finding_id") or "")
        blob = _normalize_tg3_cost_text(
            f"{rule_id} {item.get('title', '')} {item.get('message', '')} "
            f"{item.get('reason', '')} {item.get('standard_reference', '')}"
        ).lower()
        if (
            not should_keep_areal
            and (
                rule_id == "E_METHOD.areal_ns3940_2023"
                or "ns 3940" in blob
                or "ns3940" in blob
                or "arealmaling" in blob
                or "arealmåling" in blob
            )
        ):
            return False
        if (
            not should_keep_egenerklaring
            and (
                rule_id == "E_METHOD.egenerklaring_missing"
                or "egenerklaring_missing" in blob
                or "egenerklaering ikke levert" in blob
                or "egenerklæring ikke levert" in blob
            )
        ):
            return False
        return True

    all_findings = analysis_output.get("all_findings")
    if isinstance(all_findings, list):
        analysis_output["all_findings"] = [item for item in all_findings if _keep(item)]

    top_issues = analysis_output.get("top_issues")
    if isinstance(top_issues, list):
        analysis_output["top_issues"] = [item for item in top_issues if _keep(item)]

    top_score_drivers = analysis_output.get("top_score_drivers")
    if isinstance(top_score_drivers, list):
        analysis_output["top_score_drivers"] = [item for item in top_score_drivers if _keep(item)]


def _drop_known_client_false_positives(report_text: str, analysis_output: Dict[str, object]) -> None:
    if not isinstance(analysis_output, dict):
        return
    normalized_report = _normalize_tg3_cost_text(report_text or "").lower()
    should_keep_egenerklaring = _report_requires_egenerklaring_missing(report_text)
    boilerplate_egenerklaring_only = (
        "skal alltid legges frem for rapportansvarlig" in normalized_report
        and "vil dette komme tydelig frem" in normalized_report
        and "på en av de siste sidene" in normalized_report
        and "egenerklæringsskjema er ikke levert i forbindelse med oppdraget" not in normalized_report
        and "egenerklaeringsskjema er ikke levert i forbindelse med oppdraget" not in normalized_report
        and "er ikke levert i forbindelse med oppdraget" not in normalized_report
    )
    has_ns3940_2023_bra_breakdown = (
        bool(BRA_BREAKDOWN_RE.search(normalized_report))
        and bool(re.search(r"(?ix)\b(?:ns\s*3940|bra)\b", normalized_report))
    )
    def _is_false_positive(item: object) -> bool:
        if not isinstance(item, dict):
            return False
        point_id = _normalize_point_id(
            str(
                item.get("exact_point_id")
                or item.get("point_id")
                or _parse_runtime_point_ref_from_v16_finding(item)
                or _parse_point_id_from_v16_finding(item)
                or ""
            )
        )
        rule_id = str(item.get("rule_id") or item.get("finding_id") or "")
        blob = _normalize_tg3_cost_text(
            f"{rule_id} {item.get('title', '')} {item.get('message', '')} {item.get('reason', '')}"
        ).lower()
        if (
            (not should_keep_egenerklaring or boilerplate_egenerklaring_only)
            and (
                rule_id == "E_METHOD.egenerklaring_missing"
                or "egenerklaring_missing" in blob
                or "egenerklaering ikke levert" in blob
                or "egenerklæring ikke levert" in blob
            )
        ):
            return True
        if has_ns3940_2023_bra_breakdown and (
            rule_id == "E_METHOD.areal_ns3940_2023"
            or "arealmaling ikke i samsvar med ns 3940:2023" in blob
            or "arealmåling ikke i samsvar med ns 3940:2023" in blob
            or "ns 3940" in blob
            or "ns3940" in blob
        ):
            return True
        if point_id == "10.2" and (
            "wrong:limitation_as_risiko" in blob
            or "inspeksjonsbegrensning" in blob
            or "ikke synlig for inspeksjon" in blob
        ):
            return True
        if point_id == "2.1" and (
            "wrong:present_state_as_risiko" in blob
            or "risiko beskriver nåværende tilstand" in blob
            or "nåværende tap av funksjon" in blob
        ):
            return True
        if point_id == "4.2" and (
            "wrong:present_state_as_risiko" in blob
            or "risiko beskriver nåværende tilstand" in blob
            or "risiko beskriver nåværende effekt" in blob
            or "nåværende effekt" in blob
        ):
            return True
        if (
            point_id == "10.4"
            and (
                "age_only" in blob
                or "hovedsakelig med alder" in blob
                or "begrunnet kun med alder" in blob
            )
            and "valgt tilstandsgrad gis som følge av alder på varmepumpe" in normalized_report
        ):
            return True
        if (
            point_id == "4.2"
            and (
                "age_only" in blob
                or "hovedsakelig med alder" in blob
                or "begrunnet kun med alder" in blob
            )
            and _report_point_has_concrete_non_age_support(
                report_text,
                point_id,
                item.get("exact_point_title") or item.get("title") or "Undertak, lekter og yttertekking (taktekkingen)",
            )
        ):
            return True
        return False

    def _walk(value: object) -> object:
        if isinstance(value, list):
            return [_walk(item) for item in value if not _is_false_positive(item)]
        if isinstance(value, dict):
            for key, child in list(value.items()):
                value[key] = _walk(child)
            return value
        return value

    _walk(analysis_output)


def _force_required_public_findings(report_text: str, analysis_output: Dict[str, object]) -> None:
    if not isinstance(analysis_output, dict):
        return
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        all_findings = []
        analysis_output["all_findings"] = all_findings
    context = _extract_report_regime_context(report_text)
    report_date = context.get("report_date") or ""

    if _report_requires_egenerklaring_missing(report_text):
        excerpt = _report_excerpt(report_text, r"(?i)egenerkl")
        _append_unique_all_finding(
            analysis_output,
            {
                "finding_id": "E_METHOD_egenerklaring_missing_forced",
                "rule_id": "E_METHOD.egenerklaring_missing",
                "point_id": "",
                "exact_point_id": "",
                "exact_point_title": "Opplysninger fra eier / egenerklæring",
                "exact_point_text": excerpt,
                "category": "E",
                "severity": "info",
                "deduction_band": "Ikke scoretrekk",
                "title": "Egenerklæring ikke levert",
                "message": "Rapporten opplyser at egenerklæring ikke er levert. Dette skal ikke gi scoretrekk, men bør fremgå som en faglig merknad fordi analysegrunnlaget blir svakere når forhold bare eier kjenner til kan være utelatt.",
                "recommended_fix_text": "Legg inn en kort opplysning om at rapporten er ferdigstilt uten egenerklæring, og forklar hvilken metodisk usikkerhet og hvilket ansvar dette kan medføre for takstmannen.",
                "suggested_rewrite_text": "Egenerklæring er ikke levert i forbindelse med oppdraget. Dette er ikke et forskriftsavvik og skal ikke gi scoretrekk, men det svekker analysegrunnlaget fordi forhold bare eier kjenner til kan være utelatt. NS 3600:2018 pkt. 5 og 9 krever egenerklæring før analysen, og NS 3600:2025 pkt. 8 c) krever at den foreligger før rapporten ferdigstilles. Uten egenerklæring øker risikoen for at viktige opplysninger er oversett, og takstmannen eksponeres for ansvar.",
                "evidence_snippets": [excerpt] if excerpt else [],
                "gate_effect": {"blocks_96_gate": False, "caps_total_score_to": None},
            },
        )

    if _legality_rule_is_active("L-SE-01", report_date):
        excerpt = _report_l_se_01_excerpt(report_text)
        if excerpt:
            _append_unique_all_finding(
                analysis_output,
                {
                    "finding_id": "L_SE_01_forced_P09F_ELECTRICAL_INSTALLATION",
                    "rule_id": "L-SE-01",
                    "point_id": "P09F_ELECTRICAL_INSTALLATION",
                    "exact_point_id": "P09F_ELECTRICAL_INSTALLATION",
                    "exact_point_title": "Elektrisk anlegg og samsvarserklæring",
                    "exact_point_text": excerpt,
                    "category": "F",
                    "severity": "major",
                    "deduction_band": "Lavt trekk",
                    "title": "Manglende samsvarserklæring for elektrisk arbeid uten konsekvens for kjøper",
                    "message": "Rapporten opplyser om manglende samsvarserklæring for deler av det elektriske anlegget, men forklarer ikke den praktiske konsekvensen for kjøper.",
                    "recommended_fix_text": "Forklar hva manglende samsvarserklæring betyr for kjøper i praksis, for eksempel usikkerhet om forskriftsmessig utførelse og behov for videre kontroll eller dokumentasjon.",
                    "suggested_rewrite_text": "Det er ikke fremlagt samsvarserklæring for deler av det elektriske anlegget i tilbygg og garasje. Dette gir usikkerhet om arbeidet er forskriftsmessig utført, og kjøper bør påregne videre kontroll og mulig dokumentasjons- eller utbedringsbehov.",
                    "evidence_snippets": [excerpt] if excerpt else [],
                },
            )

    if _report_requires_l_bu_01(report_text, report_date):
        excerpt = _report_l_bu_01_excerpt(report_text)
        _append_unique_all_finding(
            analysis_output,
            {
                "finding_id": "L-BU-01",
                "rule_id": "L-BU-01",
                "point_id": "",
                "exact_point_id": "",
                "exact_point_title": "Lovlighet / bruksendring",
                "exact_point_text": excerpt,
                "category": "F",
                "severity": "major",
                "deduction_band": "Høyt trekk",
                "title": "Manglende byggemelding/bruksendring uten tydelig konsekvens",
                "message": "Manglende byggemelding eller bruksendring er omtalt uten at mulig søknadsplikt, kommunal oppfølging eller økonomisk risiko er forklart.",
                "recommended_fix_text": "Forklar hva manglende byggemelding eller bruksendring betyr for kjøper i praksis, for eksempel søknadsplikt, risiko for kommunal oppfølging, pålegg eller tilbakeføring.",
                "suggested_rewrite_text": "Boligen er ikke byggemeldt med kjeller, og søknad/bruksendring er ikke utført. Dette kan medføre søknadsplikt og risiko for kommunal oppfølging, pålegg eller tilbakeføring, og kjøper må påregne usikkerhet og mulige kostnader knyttet til avklaring.",
                "evidence_snippets": [excerpt] if excerpt else [],
                "gate_effect": {"blocks_96_gate": True, "caps_total_score_to": 95},
            },
        )

    if _report_requires_l_av_01(report_text, report_date):
        excerpt = _report_l_av_01_excerpt(report_text)
        _append_unique_all_finding(
            analysis_output,
            {
                "finding_id": "L-AV-01",
                "rule_id": "L-AV-01",
                "point_id": "",
                "exact_point_id": "",
                "exact_point_title": "Lovlighet / tegninger",
                "exact_point_text": excerpt,
                "category": "F",
                "severity": "major",
                "deduction_band": "Høyt trekk",
                "title": "Avvik fra godkjente tegninger uten konsekvens",
                "message": "Avvik fra godkjente eller byggemeldte tegninger er omtalt uten at mulig søknadsplikt, kommunal oppfølging eller økonomisk risiko er forklart.",
                "recommended_fix_text": "Forklar hva avvik fra godkjente eller byggemeldte tegninger betyr for kjøper i praksis, for eksempel søknadsplikt, kommunal oppfølging eller behov for avklaring.",
                "suggested_rewrite_text": "Byggemeldte tegninger stemmer ikke med dagens planløsning. Dette kan medføre behov for avklaring mot kommunen og mulig søknadsplikt, og kjøper må påregne usikkerhet og eventuelle kostnader dersom forholdet må rettes eller omsøkes.",
                "evidence_snippets": [excerpt] if excerpt else [],
                "gate_effect": {"blocks_96_gate": True, "caps_total_score_to": 95},
            },
        )


def _normalize_legal_finding_labels(analysis_output: Dict[str, object]) -> None:
    if not isinstance(analysis_output, dict):
        return
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return
    for finding in all_findings:
        if not isinstance(finding, dict):
            continue
        rule_or_id = str(finding.get("rule_id") or finding.get("finding_id") or "")
        if rule_or_id != "L-FA-01":
            continue
        source_blob = _normalize_tg3_cost_text(
            " ".join(
                [
                    str(finding.get("message") or ""),
                    " ".join(str(x or "") for x in finding.get("evidence_snippets", []) if isinstance(x, str))
                    if isinstance(finding.get("evidence_snippets"), list)
                    else "",
                ]
            )
        ).lower()
        if "ferdigattest" in source_blob:
            finding["rule_id"] = "L-FA-01"
            finding["finding_id"] = finding.get("finding_id") or "L-FA-01"
            continue
        if any(token in source_blob for token in ("byggemeld", "bruksendring", "ikke godkjent", "godkjent bruk")):
            finding["finding_id"] = "L-BU-01"
            finding["rule_id"] = "L-BU-01"
            finding["title"] = "Manglende byggemelding/bruksendring uten tydelig konsekvens"


def _sync_public_output_views(analysis_output: Dict[str, object]) -> None:
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        all_findings = []
        analysis_output["all_findings"] = all_findings

    for finding in all_findings:
        if not isinstance(finding, dict):
            continue
        finding.pop("public_visibility", None)
        if _is_tg3_missing_recommended_action_public_finding(finding):
            finding["suggested_rewrite_text"] = _clean_tg3_missing_recommended_action_text(finding)
        if _is_low_quality_public_suggested_rewrite_text(finding.get("suggested_rewrite_text")):
            finding["suggested_rewrite_text"] = ""

    visible_findings = [
        f for f in all_findings
        if isinstance(f, dict)
    ]
    scored_visible_findings = [f for f in visible_findings if _is_public_scored_finding(f)]
    score_driver_findings = [
        f for f in visible_findings
        if _is_reconciled_public_score_driver_finding(analysis_output, f)
    ]
    severity_rank = {"critical": 3, "major": 2, "minor": 1, "info": 0}

    def _finding_sort_key(item: Dict[str, object]) -> Tuple[int, int, int]:
        gate_rank = 1 if isinstance(item.get("gate_effect"), dict) and item.get("gate_effect", {}).get("blocks_96_gate") else 0
        band_rank = _PUBLIC_BAND_RANK.get(_public_band_for_item(item), 0)
        sev_rank = severity_rank.get(str(item.get("severity") or "").lower(), 0)
        return (gate_rank, band_rank, sev_rank)

    sorted_findings = sorted(visible_findings, key=_finding_sort_key, reverse=True)
    sorted_scored_findings = sorted(scored_visible_findings, key=_finding_sort_key, reverse=True)
    sorted_score_driver_findings = sorted(score_driver_findings, key=_finding_sort_key, reverse=True)

    rebuilt_top_issues: List[Dict[str, object]] = []
    has_authoritative_category_deductions = bool(_score_category_deduction_map(analysis_output))
    top_issue_source = (
        list(sorted_score_driver_findings)
        if has_authoritative_category_deductions
        else list(sorted_score_driver_findings or sorted_scored_findings or sorted_findings)
    )
    if len(top_issue_source) < 3:
        seen_source_ids = {str(item.get("finding_id") or id(item)) for item in top_issue_source if isinstance(item, dict)}
        for fallback_item in list(sorted_scored_findings) + list(sorted_findings):
            if not isinstance(fallback_item, dict):
                continue
            source_id = str(fallback_item.get("finding_id") or id(fallback_item))
            if source_id in seen_source_ids:
                continue
            seen_source_ids.add(source_id)
            top_issue_source.append(fallback_item)
            if len(top_issue_source) >= 3:
                break

    category_deductions = _score_category_deduction_map(analysis_output)
    dominant_category = ""
    if category_deductions:
        dominant_category = max(category_deductions.items(), key=lambda item: item[1])[0]
    if dominant_category and len(top_issue_source) >= 3:
        selected_categories = {_public_finding_category(item) for item in top_issue_source[:3] if isinstance(item, dict)}
        if dominant_category not in selected_categories:
            dominant_candidate = next(
                (
                    item
                    for item in list(sorted_score_driver_findings) + list(sorted_scored_findings) + list(sorted_findings)
                    if isinstance(item, dict) and _public_finding_category(item) == dominant_category
                ),
                None,
            )
            if dominant_candidate:
                replace_idx = min(
                    range(min(3, len(top_issue_source))),
                    key=lambda idx: (
                        category_deductions.get(_public_finding_category(top_issue_source[idx]), 0),
                        _finding_sort_key(top_issue_source[idx]),
                    ),
                )
                top_issue_source[replace_idx] = dominant_candidate

    seen_top_issue_ids: set = set()
    for finding in top_issue_source:
        if len(rebuilt_top_issues) >= 3:
            break
        if not isinstance(finding, dict):
            continue
        finding_id_key = str(finding.get("finding_id") or finding.get("rule_id") or id(finding))
        if finding_id_key in seen_top_issue_ids:
            continue
        seen_top_issue_ids.add(finding_id_key)
        suggested_rewrite_text = str(finding.get("suggested_rewrite_text") or "").strip()
        if _is_low_quality_public_suggested_rewrite_text(suggested_rewrite_text):
            suggested_rewrite_text = ""
        public_point_id = _public_point_reference(
            _parse_point_id_from_v16_finding(finding) or finding.get("point_id"),
            str(finding.get("rule_id") or finding.get("finding_id") or ""),
        )
        rebuilt_top_issues.append({
            "finding_id": finding.get("finding_id"),
            "rule_id": finding.get("rule_id"),
            "title": finding.get("title") or finding.get("message") or "Avvik",
            "message": finding.get("message") or finding.get("title") or "Se funn.",
            "category": finding.get("category") or _infer_category_from_rule_id(str(finding.get("rule_id") or finding.get("finding_id") or "")) or "",
            "severity": finding.get("severity") or "minor",
            "deduction_band": _public_band_for_item(finding),
            "recommended_fix_text": finding.get("recommended_fix_text") or "",
            "rewrite_strategy": finding.get("rewrite_strategy") or "",
            "suggested_rewrite_text": suggested_rewrite_text,
            "gate_effect": finding.get("gate_effect") if isinstance(finding.get("gate_effect"), dict) else {},
            "point_id": public_point_id,
        })
    analysis_output["top_issues"] = rebuilt_top_issues

    seen_improvements: set = set()
    rebuilt_how_to_improve: List[Dict[str, object]] = []
    improvement_source = list(sorted_score_driver_findings)
    if len(improvement_source) < 3:
        seen_source_ids = {str(item.get("finding_id") or id(item)) for item in improvement_source if isinstance(item, dict)}
        for fallback_item in list(sorted_scored_findings) + list(sorted_findings):
            if not isinstance(fallback_item, dict):
                continue
            source_id = str(fallback_item.get("finding_id") or id(fallback_item))
            if source_id in seen_source_ids:
                continue
            seen_source_ids.add(source_id)
            improvement_source.append(fallback_item)
            if len(improvement_source) >= 3:
                break
    for finding in improvement_source:
        if len(rebuilt_how_to_improve) >= 3:
            break
        title = str(finding.get("title") or finding.get("message") or "Forbedringspunkt").strip()
        recommended_fix_text = str(finding.get("recommended_fix_text") or "").strip()
        suggested_rewrite_text = str(finding.get("suggested_rewrite_text") or "").strip()
        if _is_low_quality_public_suggested_rewrite_text(suggested_rewrite_text):
            suggested_rewrite_text = ""
        if not recommended_fix_text and not suggested_rewrite_text:
            continue
        raw_point_id = _parse_point_id_from_v16_finding(finding) or str(finding.get("point_id") or "").strip()
        point_id = _public_point_reference(raw_point_id, str(finding.get("rule_id") or finding.get("finding_id") or ""))
        dedupe_key = (
            _normalize_point_id((raw_point_id or point_id) or "GLOBAL"),
            _normalize_tg3_cost_text(title).lower(),
            _normalize_tg3_cost_text(recommended_fix_text or suggested_rewrite_text).lower(),
        )
        if dedupe_key in seen_improvements:
            continue
        seen_improvements.add(dedupe_key)
        rebuilt_how_to_improve.append({
            "title": title,
            "category": finding.get("category") or _infer_category_from_rule_id(str(finding.get("rule_id") or finding.get("finding_id") or "")) or "",
            "point_id": point_id,
            "recommended_fix_text": recommended_fix_text or suggested_rewrite_text,
            "rewrite_strategy": finding.get("rewrite_strategy") or "",
            "suggested_rewrite_text": suggested_rewrite_text or recommended_fix_text,
            "deduction_band": _public_band_for_item(finding),
        })
    analysis_output["how_to_improve"] = rebuilt_how_to_improve


def _ensure_feedback_findings_cover_deductions(
    feedback_findings: List[Dict[str, object]],
    deduction_totals: Dict[str, int],
    finding_ids_by_point: Dict[str, List[str]],
    point_lookup: Dict[str, Dict[str, object]],
) -> None:
    if not isinstance(feedback_findings, list) or not isinstance(deduction_totals, dict):
        return
    existing_ids = {
        str(item.get("finding_id") or "")
        for item in feedback_findings
        if isinstance(item, dict)
    }
    for point_id, deduction in deduction_totals.items():
        norm_point_id = _normalize_point_id(str(point_id or ""))
        if not norm_point_id or int(deduction or 0) <= 0:
            continue
        if finding_ids_by_point.get(norm_point_id):
            continue
        point_meta = point_lookup.get(norm_point_id) or {}
        title = str(point_meta.get("title") or point_meta.get("heading") or norm_point_id).strip()
        excerpt = str(
            point_meta.get("effective_span_text")
            or point_meta.get("exact_span_text")
            or point_meta.get("excerpt")
            or point_meta.get("span_text")
            or ""
        ).strip()
        finding_id = f"f-synth-{norm_point_id}"
        if finding_id in existing_ids:
            continue
        message = f"Punkt {norm_point_id} ({title}) har scoretrekk, men manglet synlig funn etter filtrering."
        what_to_change = (
            f"Kontroller punktteksten for {title} og sørg for at avviket beskrives tydelig nok til å være synlig i funnlisten."
        )
        feedback_findings.append(
            {
                "finding_id": finding_id,
                "rule_id": "VISIBILITY_SYNC",
                "rule_family": "SYSTEM",
                "severity": "high" if int(deduction) >= 5 else "medium",
                "affects_96_gate": False,
                "point_id": norm_point_id,
                "point_key": point_meta.get("point_key") or norm_point_id,
                "arkat_section": "annet",
                "message": message,
                "what_to_change": what_to_change,
                "example_fix": {
                    "good_example": excerpt[:500] if excerpt else f"Skriv avviket i {title} tydelig og punktspesifikt."
                },
                "evidence": {
                    "page": int(point_meta.get("page_start") or 1),
                    "snippet": excerpt[:500] if excerpt else title,
                    "match": "Synthetic visibility safeguard from point source.",
                },
                "deduction": int(deduction),
            }
        )
        finding_ids_by_point.setdefault(norm_point_id, []).append(finding_id)
        existing_ids.add(finding_id)


def _sanitize_feedback_missing_tiltak_findings(
    feedback_findings: List[Dict[str, object]],
    point_tg_by_id: Optional[Dict[str, str]] = None,
) -> None:
    if not isinstance(feedback_findings, list):
        return
    # Content-preserving policy: do not rewrite rule/category/TG semantics in feedback.
    # Only dedupe truly identical items by source identity to avoid double-rendering.
    seen_keys: set = set()
    filtered: List[Dict[str, object]] = []
    for finding in feedback_findings:
        if not isinstance(finding, dict):
            filtered.append(finding)
            continue
        dedupe_key = (
            str(finding.get("source_finding_id") or "").strip(),
            str(finding.get("rule_id") or "").strip(),
            _normalize_point_id(str(finding.get("point_id") or finding.get("point_key") or "")),
            _normalize_tg3_cost_text(str(finding.get("message") or "")).lower(),
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        filtered.append(finding)
    feedback_findings[:] = filtered


def _deduction_band_from_numeric(deduction: float) -> str:
    """Convert numeric deduction to band: none (0), low (0.01-3), medium (3.01-6.99), high (7+)."""
    d = float(deduction or 0)
    if d <= 0:
        return "none"
    if d <= 3.0:
        return "low"
    if d <= 6.99:
        return "medium"
    return "high"


def _tg_rank(tg: str) -> int:
    tg_norm = str(tg or "").strip().upper()
    return {"TG0": 0, "TG1": 1, "TG2": 2, "TG3": 3}.get(tg_norm, -1)


def _polish_feedback_text(text: object) -> str:
    if not isinstance(text, str):
        return ""
    s = _strip_suspicious_cjk(text)
    s = " ".join(s.split())
    s = re.sub(r"\bmed\s+[nN]\s+kort\s+setning\b", "med en kort setning", s, flags=re.IGNORECASE)
    s = re.sub(r"\bmed n kort setning\b", "med en kort setning", s, flags=re.IGNORECASE)
    s = re.sub(r"\bmed n\b", "med en", s, flags=re.IGNORECASE)
    s = re.sub(r"\b n kort setning\b", " en kort setning", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([.?!]){2,}", r"\1", s)
    return s.strip()


def _polish_analysis_text_fields(payload: object) -> None:
    """Apply small Norwegian text fixups on analysis payload text fields."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                _polish_analysis_text_fields(value)
            elif isinstance(value, str):
                payload[key] = _polish_feedback_text(value)
    elif isinstance(payload, list):
        for idx, item in enumerate(payload):
            if isinstance(item, (dict, list)):
                _polish_analysis_text_fields(item)
            elif isinstance(item, str):
                payload[idx] = _polish_feedback_text(item)


def _remove_dead_public_visibility_fields(payload: object) -> None:
    if isinstance(payload, dict):
        payload.pop("public_visibility", None)
        for value in list(payload.values()):
            if isinstance(value, (dict, list)):
                _remove_dead_public_visibility_fields(value)
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, (dict, list)):
                _remove_dead_public_visibility_fields(item)


def _ensure_special_feedback_findings_visible(
    analysis_output: Dict[str, object],
    feedback_findings: List[Dict[str, object]],
) -> None:
    if not isinstance(feedback_findings, list):
        return
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return
    wanted_rules = {
        "L-SE-01",
        "E_METHOD.egenerklaring_missing",
        "E_METHOD.vinterhage_not_assessed_ns3600",
    }
    existing = {
        str(item.get("rule_id") or item.get("finding_id") or "")
        for item in feedback_findings
        if isinstance(item, dict)
    }
    for item in all_findings:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or item.get("finding_id") or "")
        if rule_id not in wanted_rules or rule_id in existing:
            continue
        raw_point_id = _normalize_point_id(str(item.get("exact_point_id") or item.get("point_id") or ""))
        public_point_id = _public_point_reference(raw_point_id, rule_id)
        deduction = {"Lavt trekk": 1, "Middels trekk": 3, "Høyt trekk": 5}.get(str(item.get("deduction_band") or "").strip(), 0)
        evidence_snippets = item.get("evidence_snippets") or []
        snippet = (
            str(item.get("exact_point_text") or "").strip()
            or (evidence_snippets[0] if evidence_snippets and isinstance(evidence_snippets[0], str) else "")
            or str(item.get("message") or item.get("title") or "Ikke tilgjengelig.")
        )
        customer_title, customer_message, customer_change, customer_example = _customer_text_from_source_finding(
            item,
            str(item.get("title") or item.get("message") or "Avvik"),
        )
        feedback_findings.append(
            {
                "finding_id": f"f-special-{rule_id.replace('.', '_')}",
                "source_finding_id": str(item.get("finding_id") or ""),
                "rule_id": rule_id,
                "rule_family": _derive_rule_family(rule_id) or "UNKNOWN",
                "severity": str(item.get("severity") or "minor"),
                "affects_96_gate": bool(isinstance(item.get("gate_effect"), dict) and item.get("gate_effect", {}).get("blocks_96_gate")),
                "point_id": public_point_id,
                "point_key": "",
                "arkat_section": "annet",
                "title": customer_title,
                "message": customer_message,
                "what_to_change": customer_change,
                "example_fix": {"good_example": customer_example},
                "evidence": {"page": 1, "snippet": snippet[:500] if snippet else "Ikke tilgjengelig.", "match": "Forced visible from all_findings."},
                "deduction": deduction,
                "deduction_band": str(item.get("deduction_band") or ""),
                "potential_deduction": _finding_potential_deduction(item) or deduction,
            }
        )
        existing.add(rule_id)


def _polish_feedback_findings(findings: List[Dict[str, object]]) -> None:
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        rule_id = str(finding.get("rule_id") or finding.get("finding_id") or "")
        public_point_id = _public_point_reference(str(finding.get("point_id") or ""), rule_id)
        finding["point_id"] = public_point_id
        if not public_point_id or _is_canonical_child_point_id(str(finding.get("point_key") or "")):
            finding["point_key"] = ""
        message = _polish_feedback_text(finding.get("message"))
        what_to_change = _polish_feedback_text(finding.get("what_to_change"))
        if message:
            finding["message"] = message
        if what_to_change:
            # Avoid stiff duplicate phrasing when message and action text are identical.
            if message and what_to_change.lower() == message.lower():
                what_to_change = f"Oppdater punktteksten slik at avviket lukkes: {message}"
            finding["what_to_change"] = what_to_change
        example_fix = finding.get("example_fix")
        if isinstance(example_fix, dict):
            good_example = _polish_feedback_text(example_fix.get("good_example"))
            if good_example:
                example_fix["good_example"] = good_example

def _reconcile_feedback_deduction_consistency(
    feedback_findings: List[Dict[str, object]],
    score_by_category: List[Dict[str, object]],
    top_score_drivers: List[Dict[str, object]],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """
    Keep output internally consistent without synthetic "balance" findings.
    """
    if not isinstance(top_score_drivers, list):
        top_score_drivers = []
    filtered_findings = [
        f for f in feedback_findings
        if not (
            isinstance(f, dict)
            and str(f.get("rule_id") or "").strip().upper() == "CATEGORY_BALANCE_ADJUSTMENT"
        )
    ]
    filtered_drivers = [
        d for d in top_score_drivers
        if not (
            isinstance(d, dict)
            and (
                any(
                    str(r).strip().upper() == "CATEGORY_BALANCE_ADJUSTMENT"
                    for r in (d.get("rule_refs") or [])
                    if isinstance(r, str)
                )
                or str(d.get("title") or "").strip().lower() == "konsistensjustering av trekk"
            )
        )
    ]
    return filtered_findings, filtered_drivers


def _detect_e3_p11_p12_presence(
    points: List[Dict[str, object]],
    points_before_whitelist: List[Dict[str, object]],
) -> Dict[str, bool]:
    """
    Hard fallback: if E3-specific headings are present anywhere in detected/raw headings,
    mark P11/P12 as present so UI never shows NOT_FOUND for these sections.
    """
    candidates: List[str] = []
    for src in (points, points_before_whitelist):
        if not isinstance(src, list):
            continue
        for p in src:
            if not isinstance(p, dict):
                continue
            for key in ("title", "heading", "excerpt", "native_label", "anchor_text", "span_text", "effective_span_text", "exact_span_text"):
                value = p.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())

    if not candidates:
        return {"P11": False, "P12": False}

    def _norm(s: str) -> str:
        t = _normalize_segment_title_for_canonical_match(s or "")
        return (
            t.replace("æ", "ae")
            .replace("ø", "o")
            .replace("å", "aa")
        )

    has_p11 = False
    has_p12 = False
    for text in candidates:
        n = _norm(text)
        if (
            "lovlighet og sikkerhet" in n
            or "lovlighet" in n
            or "godkjente tegninger" in n
            or "byggemeldte tegninger" in n
            or "ferdigattest" in n
            or "brukstillatelse" in n
            or "bruksendring" in n
        ):
            has_p11 = True
        if (
            "tilleggsopplysninger" in n
            or "anbefalte ytterligere unders" in n
            or "vaer oppmerksom" in n
            or "ver oppmerksom" in n
            or "egenerkl" in n
            or "planlosning" in n
            or "godkjente tegninger" in n
            or "ferdigattest" in n
            or "radon" in n
        ):
            has_p12 = True
    # E3 fallback: when supplementary/attention section exists and contains legality cues,
    # treat P11 as present even if explicit "Lovlighet" heading is absent.
    if not has_p11 and has_p12:
        for text in candidates:
            n = _norm(text)
            if (
                "tegninger" in n
                or "byggemeldt" in n
                or "ferdigattest" in n
                or "brukstillatelse" in n
                or "bruksendring" in n
            ):
                has_p11 = True
                break
    return {"P11": has_p11, "P12": has_p12}


def _normalize_segment_title_for_canonical_match(title: str) -> str:
    """
    Normalize for canonical matching:
    - keep Unicode text
    - NFKC normalize
    - trim + collapse whitespace
    - lowercase
    """
    if not title or not isinstance(title, str):
        return ""
    s = unicodedata.normalize("NFKC", title)
    s = " ".join(s.strip().split())
    return s.lower()


def _segment_matches_canonical(norm_title: str, cp: Dict, point_lookup: Dict[str, Dict]) -> bool:
    """Check if normalized title matches canonical point via match_any, aliases, or regex_any (including children)."""
    if not norm_title:
        return False

    def _pattern_match(pattern: str) -> bool:
        if not pattern or not isinstance(pattern, str):
            return False
        try:
            # Canonical patterns may include regex boundaries (\b...); use search, not match.
            return re.search(pattern, norm_title, flags=re.IGNORECASE | re.UNICODE) is not None
        except re.error:
            # Fallback for malformed regex-like values.
            fallback = pattern.replace("\\b", "").strip().lower()
            return bool(fallback and fallback in norm_title)

    # Collect all patterns from across different config schemas (match_any, aliases, regex_any)
    patterns = set()
    for field in ["match_any", "aliases", "regex_any"]:
        val = cp.get(field) or []
        if isinstance(val, list):
            patterns.update([str(p) for p in val if p])
        elif isinstance(val, str):
            patterns.add(val)
            
    # Also check exact ID and Title matches
    for field in ["canonical_id", "child_id", "title_nb", "label_nb"]:
        val = str(cp.get(field) or "").strip().lower()
        if val and val in norm_title:
            return True

    for p in patterns:
        if _pattern_match(p):
            return True

    # Nested check for child technical points/components
    for child in (cp.get("children") or []):
        if isinstance(child, dict) and _segment_matches_canonical(norm_title, child, {}):
            return True

    return False


_OVERVIEW_PARENT_FALLBACK_PATTERNS: Dict[str, Tuple[re.Pattern, ...]] = {
    "P06_ROOMS_BELOW_GRADE": (
        re.compile(r"(?i)\brom under terreng\b"),
        re.compile(r"(?i)\bunderetasje\b.{0,40}\b(?:fuktm[aå]ling|ventilasjon|drener|grunnmur|terreng)\b"),
        re.compile(r"(?i)\bbelow grade\b"),
    ),
    "P11_LAWFULNESS_AND_SAFETY": (
        re.compile(r"(?i)\blovlighet(?:\s+og\s+sikkerhet)?\b"),
        re.compile(r"(?i)\brøykvarsler"),
        re.compile(r"(?i)\bbranntekn"),
        re.compile(r"(?i)\belektrisk"),
        re.compile(r"(?i)\bsamsvarserkl"),
        re.compile(r"(?i)\bradon"),
        re.compile(r"(?i)\bbranncelle"),
        re.compile(r"(?i)\brømningsvei"),
    ),
    "P12_SUPPLEMENTARY_INFORMATION": (
        re.compile(r"(?i)\btilleggsopplysninger\b"),
        re.compile(r"(?i)\bdokumentasjon\b"),
        re.compile(r"(?i)\begenerkl"),
        re.compile(r"(?i)\bbyggetegninger?\b"),
        re.compile(r"(?i)\bnyere arbeider\b"),
        re.compile(r"(?i)\bundersøkelsesbegrens"),
        re.compile(r"(?i)\btgiu\b"),
        re.compile(r"(?i)\bv[æa]r oppmerksom p(?:[åa]|aa)\b"),
    ),
}


def _is_wet_room_context(text: str) -> bool:
    norm = _normalize_segment_title_for_canonical_match(text or "")
    if not norm:
        return False
    return bool(re.search(r"(?ix)\b(?:v[aå]trom|bad(?:erom)?|vaskerom|wc|toalett|dusj)\b", norm))


def _segment_matches_overview_parent(title: str, canonical_id: str) -> bool:
    norm_title = _normalize_segment_title_for_canonical_match(title or "")
    if not norm_title or not canonical_id:
        return False
    for rx in _OVERVIEW_PARENT_FALLBACK_PATTERNS.get(str(canonical_id), ()):
        if rx.search(norm_title):
            return True
    return False


def _point_overview_parent_candidates(point: Dict[str, object]) -> set:
    candidates: set = set()
    if not isinstance(point, dict):
        return candidates
    raw_point_id = _normalize_point_id(
        str(point.get("point_id") or point.get("native_label") or point.get("numeric_id") or "")
    )
    # Use heading-like labels only so unrelated body text does not roll findings into P11/P12.
    heading_like = _normalize_tg3_cost_text(
        "\n".join(
            [
                str(point.get("title") or ""),
                str(point.get("heading") or ""),
                str(point.get("excerpt") or ""),
                str(point.get("native_label") or ""),
            ]
        )
    )
    if raw_point_id.startswith("9.") and not _is_wet_room_context(heading_like):
        candidates.add("P06_ROOMS_BELOW_GRADE")
    if not heading_like:
        return candidates
    # Prevent leakage from basement wet-room points (P07) into P06.
    if re.search(r"(?ix)\b(?:rom\s+under\s+terreng|underetasje)\b", heading_like) and not _is_wet_room_context(heading_like):
        candidates.add("P06_ROOMS_BELOW_GRADE")
    for parent_id in _OVERVIEW_PARENT_FALLBACK_PATTERNS:
        if _segment_matches_overview_parent(heading_like, parent_id):
            candidates.add(parent_id)
    return candidates


def _all_finding_overview_parent_candidates(finding: Dict[str, object]) -> set:
    candidates: set = set()
    if not isinstance(finding, dict):
        return candidates
    for key in ("exact_point_id", "point_id"):
        raw_point_id = _normalize_point_id(str(finding.get(key) or ""))
        hint_blob = _normalize_tg3_cost_text(
            "\n".join(
                [
                    str(finding.get("exact_point_title") or ""),
                    str(finding.get("title") or ""),
                    str(finding.get("message") or ""),
                ]
            )
        )
        if raw_point_id.startswith("9.") and not _is_wet_room_context(hint_blob):
            candidates.add("P06_ROOMS_BELOW_GRADE")
    text_blob = _normalize_tg3_cost_text(
        "\n".join(
            [
                str(finding.get("exact_point_title") or ""),
                str(finding.get("title") or ""),
                str(finding.get("message") or ""),
            ]
        )
    )
    if not text_blob:
        return candidates
    if re.search(r"(?ix)\b(?:rom\s+under\s+terreng|underetasje)\b", text_blob) and not _is_wet_room_context(text_blob):
        candidates.add("P06_ROOMS_BELOW_GRADE")
    for parent_id in _OVERVIEW_PARENT_FALLBACK_PATTERNS:
        if _segment_matches_overview_parent(text_blob, parent_id):
            candidates.add(parent_id)
    return candidates


def _finding_declared_tg(finding: Dict[str, object]) -> str:
    if not isinstance(finding, dict):
        return ""
    blob = _normalize_tg3_cost_text(
        "\n".join(
            [
                str(finding.get("title") or ""),
                str(finding.get("message") or ""),
                str(finding.get("exact_point_title") or ""),
            ]
        )
    ).upper()
    matches = re.findall(r"\bTG(?:0|1|2|3|IU)\b", blob)
    if not matches:
        return ""
    return max(matches, key=_tg_rank)

def _map_segments_to_canonical(
    points: List[Dict[str, object]],
    canonical_points: List[Dict],
) -> Dict[str, Dict[str, object]]:
    """
    Map detected segments to canonical_id. For each canonical (display order), assign first matching segment.
    component_only children match to parent canonical via children match_any.
    """
    sorted_cp = sorted(
        [c for c in canonical_points if isinstance(c, dict) and c.get("canonical_id")],
        key=lambda c: int(c.get("display_order") or 999),
    )
    result: Dict[str, Dict[str, object]] = {}
    used_point_ids: set = set()

    def _candidate_score(seg: Dict[str, object]) -> Tuple[int, int]:
        """
        Prefer segments with explicit TG and detailed section pages over summary pages.
        Higher is better.
        """
        tg = str(seg.get("tg") or "").strip().upper()
        tg_known = 1 if tg in {"TG0", "TG1", "TG2", "TG3", "TGIU"} else 0
        page = int(seg.get("page_start") or 1)
        title = (seg.get("title") or seg.get("heading") or seg.get("native_label") or "").strip()
        detail_hint = 1 if ">" in title or len(title) > 20 else 0
        # Prioritize known TG first, then likely detail sections.
        return (tg_known * 100 + detail_hint * 10 + min(max(page, 1), 99), -page)

    for cp in sorted_cp:
        cid = cp.get("canonical_id") or ""
        if not cid:
            continue
        best_seg: Optional[Dict[str, object]] = None
        best_score: Optional[Tuple[int, int]] = None
        for seg in points:
            if not isinstance(seg, dict):
                continue
            point_id = seg.get("point_id") or seg.get("numeric_id") or seg.get("native_label") or ""
            if not point_id or point_id in used_point_ids:
                continue
            # Match canonical against heading/title, not body text.
            title = (seg.get("title") or seg.get("heading") or seg.get("native_label") or "").strip()
            norm_title = _normalize_segment_title_for_canonical_match(title)
            if not norm_title:
                continue
            if _segment_matches_canonical(norm_title, cp, {}) or _segment_matches_overview_parent(title, cid):
                score = _candidate_score(seg)
                if best_score is None or score > best_score:
                    best_seg = seg
                    best_score = score
        if isinstance(best_seg, dict):
            point_id = best_seg.get("point_id") or best_seg.get("numeric_id") or best_seg.get("native_label") or ""
            if point_id:
                used_point_ids.add(point_id)
            result[cid] = best_seg
    return result


def _emit_canonical_mapping_debug(
    report_id: Optional[str],
    points: List[Dict[str, object]],
    canonical_points: List[Dict],
    segment_map: Dict[str, Dict[str, object]],
    detected_points_payload: Optional[Dict[str, object]] = None,
) -> None:
    """Debug summary for canonical mapping outcomes (enabled only in analysis debug mode)."""
    if not (settings.ANALYSIS_DEBUG or settings.ANALYSIS_DEBUG_ONCE or settings.ANALYSIS_DEBUG_RUN_ID):
        return

    trace = {}
    if isinstance(detected_points_payload, dict):
        trace = detected_points_payload.get("segmentation_trace") or {}
    if not isinstance(trace, dict):
        trace = {}

    first_5_titles = []
    for p in points[:5]:
        if not isinstance(p, dict):
            continue
        t = (p.get("title") or p.get("heading") or p.get("native_label") or "").strip()
        if t:
            first_5_titles.append(t)

    found_points_count = len(segment_map)
    total_canonical = len([c for c in canonical_points if isinstance(c, dict) and c.get("canonical_id")])
    not_found_points_count = max(total_canonical - found_points_count, 0)

    sorted_cp = sorted(
        [c for c in canonical_points if isinstance(c, dict) and c.get("canonical_id")],
        key=lambda c: int(c.get("display_order") or 999),
    )
    p01_cp = next(
        (c for c in sorted_cp if str(c.get("canonical_id") or "").upper().startswith("P01_")),
        sorted_cp[0] if sorted_cp else {},
    )
    p01_canonical_id = p01_cp.get("canonical_id") if isinstance(p01_cp, dict) else None
    p01_matched_titles: List[str] = []
    if isinstance(p01_cp, dict) and p01_cp:
        for seg in points:
            if not isinstance(seg, dict):
                continue
            title = (seg.get("title") or seg.get("heading") or seg.get("native_label") or "").strip()
            norm_title = _normalize_segment_title_for_canonical_match(title)
            if title and _segment_matches_canonical(norm_title, p01_cp, {}):
                p01_matched_titles.append(title)
            if len(p01_matched_titles) >= 10:
                break

    logger.info(
        "canonical_mapping_debug report_id=%s segments_extracted_count=%s segments_after_filter_count=%s first_5_segment_titles=%s found_points_count=%s not_found_points_count=%s p01_canonical_id=%s p01_matched_segment_titles=%s",
        report_id or "unknown_report",
        trace.get("total_detected_before_stray", len(points)),
        trace.get("total_after_whitelist", len(points)),
        first_5_titles,
        found_points_count,
        not_found_points_count,
        p01_canonical_id,
        p01_matched_titles,
    )


def _build_points_overview_from_canonical(
    canonical_points: List[Dict],
    segment_map: Dict[str, Dict[str, object]],
    deduction_totals: Dict[str, int],
    finding_ids_by_point: Dict[str, List[str]],
    point_lookup: Dict[str, Dict[str, object]],
    requirement_tags: Optional[Dict[str, object]] = None,
    ui_overlay: Optional[Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    """
    Build points_overview from canonical list. Always all items in display_order.
    status: FOUND or NOT_FOUND_IN_REPORT.
    """
    sorted_canonical = sorted(
        [c for c in canonical_points if isinstance(c, dict) and c.get("canonical_id")],
        key=lambda c: int(c.get("display_order") or 999),
    )
    points_overview: List[Dict[str, object]] = []
    requirement_tags = requirement_tags if isinstance(requirement_tags, dict) else {}
    ui_overlay = ui_overlay if isinstance(ui_overlay, dict) else {}
    fallback_suffixes = (
        ui_overlay.get("requirement_suffix_policy", {}).get("fallback", {})
        if isinstance(ui_overlay.get("requirement_suffix_policy"), dict)
        else {}
    )
    not_found_text = (
        ui_overlay.get("not_found_policy", {}).get("ui_text_nb")
        if isinstance(ui_overlay.get("not_found_policy"), dict)
        else None
    )
    tg_missing_text = (
        ui_overlay.get("tg_display_policy", {}).get("not_present_text_nb")
        if isinstance(ui_overlay.get("tg_display_policy"), dict)
        else None
    )
    for idx, cp in enumerate(sorted_canonical):
        cid = cp.get("canonical_id") or ""
        title_nb = cp.get("title_nb") or ""
        requirement_tag = cp.get("requirement_tag") or ""
        req_meta = requirement_tags.get(requirement_tag, {}) if isinstance(requirement_tag, str) else {}
        suffix = ""
        if isinstance(req_meta, dict):
            suffix = str(req_meta.get("ui_suffix_nb") or "")
        if not suffix and isinstance(fallback_suffixes, dict) and isinstance(requirement_tag, str):
            suffix = str(fallback_suffixes.get(requirement_tag) or "")
        title_display = f"{title_nb}{suffix}" if suffix else title_nb
        display_index = idx + 1
        seg = segment_map.get(cid)
        if seg:
            point_id = seg.get("point_id") or seg.get("numeric_id") or seg.get("native_label") or ""
            deduction = int(deduction_totals.get(point_id, 0))
            deduction_band = _deduction_band_from_numeric(deduction)
            fids = finding_ids_by_point.get(point_id, [])
            component = None  # would need analysis_output for issues
            tg_value = seg.get("tg") or tg_missing_text or "UNKNOWN"
            summary = "OK – ingen endringer nødvendig."
            if deduction > 0 or _tg_rank(str(tg_value or "").upper()) >= _tg_rank("TG2"):
                summary = "Avvik funnet"
            points_overview.append({
                "display_index": display_index,
                "canonical_id": cid,
                "point_id": point_id,
                "title": title_nb,
                "title_display": title_display,
                "requirement_tag": requirement_tag,
                "status": "FOUND",
                "deduction_band": deduction_band,
                "deduction_total": deduction,
                "tg": tg_value,
                "summary": summary,
                "finding_ids": fids,
                "where": {"page": int(seg.get("page_start") or 1)},
            })
        else:
            points_overview.append({
                "display_index": display_index,
                "canonical_id": cid,
                "point_id": None,
                "title": title_nb,
                "title_display": title_display,
                "requirement_tag": requirement_tag,
                "status": "NOT_FOUND_IN_REPORT",
                "deduction_band": "none",
                "deduction_total": 0,
                "tg": tg_missing_text or "UNKNOWN",
                "summary": str(not_found_text or "Ikke funnet i rapport"),
                "finding_ids": [],
                "where": {},
            })
    return points_overview


def _force_parent_points_overview_scored_status(
    points_overview: List[Dict[str, object]],
    all_findings: List[Dict[str, object]],
) -> None:
    """
    Final safety guard for parent cards:
    if a scored finding references numeric point IDs (e.g. 1.1, 9.1.3),
    ensure the matching Pxx parent card cannot stay "OK".
    """
    if not isinstance(points_overview, list) or not isinstance(all_findings, list):
        return

    parent_by_num: Dict[str, Dict[str, object]] = {}
    for entry in points_overview:
        if not isinstance(entry, dict):
            continue
        canonical_id = str(entry.get("canonical_id") or "")
        m = re.match(r"^P(\d{2})_", canonical_id)
        if m:
            parent_by_num[m.group(1)] = entry

    if not parent_by_num:
        return

    for finding in all_findings:
        if not isinstance(finding, dict) or not _is_public_scored_finding(finding):
            continue
        finding_id = str(finding.get("finding_id") or finding.get("rule_id") or "").strip()
        blob = _normalize_tg3_cost_text(
            " ".join(
                [
                    str(finding.get("exact_point_id") or ""),
                    str(finding.get("point_id") or ""),
                    str(finding.get("exact_point_title") or ""),
                    str(finding.get("title") or ""),
                    str(finding.get("message") or ""),
                    str(finding.get("reason") or ""),
                    str(finding.get("exact_point_text") or ""),
                ]
            )
        )
        target_nums: set = set()
        for pid in _extract_numeric_point_ids_from_text(blob):
            first = pid.split(".")[0]
            if first.isdigit():
                target_nums.add(f"{int(first):02d}")
        for num in target_nums:
            parent = parent_by_num.get(num)
            if not isinstance(parent, dict):
                continue
            if str(parent.get("status") or "") == "NOT_FOUND_IN_REPORT":
                continue
            parent["summary"] = "Avvik funnet"
            if str(parent.get("deduction_band") or "").strip() in {"", "none", "Ikke scoretrekk"}:
                parent["deduction_band"] = "low"
            existing_ids = parent.get("finding_ids")
            if isinstance(existing_ids, list):
                if finding_id and finding_id not in existing_ids:
                    existing_ids.append(finding_id)
            elif finding_id:
                parent["finding_ids"] = [finding_id]


def _build_feedback_v11(
    analysis_output: Dict[str, object],
    detected_points_payload: Dict[str, object],
    report_id: Optional[str],
    document_hash: Optional[str],
    report_text: str = "",
) -> Dict[str, object]:
    points = detected_points_payload.get("points", []) if isinstance(detected_points_payload, dict) else []
    canonical_points: List[Dict[str, object]] = []
    # Precompute mapping + allowed IDs so fallback can be gated on real linkage.
    pre_mapping_cfg = get_points_overview_mapping_config()
    pre_child_mappings = pre_mapping_cfg.get("child_mappings") or []
    pre_mapping_points: List[Dict[str, object]] = []
    for m in pre_child_mappings:
        if isinstance(m, dict):
            m_copy = dict(m)
            if "child_id" in m_copy:
                m_copy["canonical_id"] = m_copy["child_id"]
            pre_mapping_points.append(m_copy)
    pre_allowed_point_ids: set = set()
    for point in points:
        if not isinstance(point, dict):
            continue
        for key in (
            point.get("point_id"),
            point.get("numeric_id"),
            point.get("point_key"),
            point.get("native_label"),
            point.get("title"),
        ):
            if isinstance(key, str) and key:
                pre_allowed_point_ids.add(key)

    all_findings = analysis_output.get("all_findings")
    scored_all_findings = [
        finding for finding in all_findings
        if isinstance(finding, dict) and _is_public_scored_finding(finding)
    ] if isinstance(all_findings, list) else []
    if (
        isinstance(all_findings, list)
        and len(all_findings) > 0
        and (not analysis_output.get("findings") or len(scored_all_findings) > 0)
        and _can_use_all_findings_fallback(all_findings, pre_mapping_points, pre_allowed_point_ids)
    ):
        return _build_feedback_v11_from_all_findings(
            analysis_output,
            detected_points_payload,
            report_id=report_id,
            document_hash=document_hash,
        )
    points_before_whitelist = (
        detected_points_payload.get("points_before_whitelist", [])
        if isinstance(detected_points_payload, dict)
        else []
    )
    presence_points: List[Dict[str, object]] = []
    if isinstance(points, list):
        presence_points.extend([p for p in points if isinstance(p, dict)])
    if isinstance(points_before_whitelist, list):
        seen_presence_ids = {
            str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
            for p in presence_points
            if isinstance(p, dict)
        }
        for p in points_before_whitelist:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
            if pid and pid in seen_presence_ids:
                continue
            presence_points.append(p)

    # V3.9 Architecture: Align raw segments with canonical IDs
    mapping_cfg = get_points_overview_mapping_config()
    child_mappings = mapping_cfg.get("child_mappings") or []
    mapping_points = []
    if child_mappings and points:
        for m in child_mappings:
            if isinstance(m, dict):
                m_copy = dict(m)
                if "child_id" in m_copy:
                    m_copy["canonical_id"] = m_copy["child_id"]
                mapping_points.append(m_copy)

        # Use existing matcher on both whitelist points and pre-whitelist points
        # so strict whitelist drops can still be recovered during canonical roll-up.
        mapping_input_points: List[Dict[str, object]] = []
        if isinstance(points, list):
            mapping_input_points.extend([dict(p) for p in points if isinstance(p, dict)])
        if isinstance(points_before_whitelist, list):
            seen_ids = {
                str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                for p in mapping_input_points
                if isinstance(p, dict)
            }
            for p in points_before_whitelist:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                if pid and pid in seen_ids:
                    continue
                mapping_input_points.append(dict(p))

        segment_map = _map_segments_to_canonical(mapping_input_points, mapping_points)
        for canon_id, seg in segment_map.items():
            # Force the segment ID to match the canonical child ID so roll-up logic finds it
            seg["point_id"] = canon_id

    allowed_point_ids = set()
    point_lookup: Dict[str, Dict[str, object]] = {}
    for point in points:
        if not isinstance(point, dict):
            continue
        # Support full titles as lookup keys (LLM often returns the title)
        pid_keys = [
            point.get("point_id"),
            point.get("numeric_id"),
            point.get("point_key"),
            point.get("native_label"),
            point.get("title")
        ]
        for key in pid_keys:
            if isinstance(key, str) and key:
                allowed_point_ids.add(key)
                point_lookup.setdefault(key, point)

    # Recover canonical children inferred from pre-whitelist mapping (without exposing raw point IDs).
    if mapping_points:
        mapping_input_points: List[Dict[str, object]] = []
        if isinstance(points, list):
            mapping_input_points.extend([dict(p) for p in points if isinstance(p, dict)])
        if isinstance(points_before_whitelist, list):
            seen_ids = {
                str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                for p in mapping_input_points
                if isinstance(p, dict)
            }
            for p in points_before_whitelist:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                if pid and pid in seen_ids:
                    continue
                mapping_input_points.append(dict(p))
        pre_segment_map = _map_segments_to_canonical(mapping_input_points, mapping_points)
        for canon_id, seg in pre_segment_map.items():
            if canon_id:
                allowed_point_ids.add(canon_id)
                point_lookup.setdefault(canon_id, seg)

    # V3.9 Architecture: Force findings to use canonical IDs if they match aliases/regex
    all_findings_source = (analysis_output.get("findings") or []) + (analysis_output.get("all_findings") or [])
    for comp in all_findings_source:
        if not isinstance(comp, dict): continue
        raw_cid = str(comp.get("component_id") or comp.get("point_id") or "")
        if not raw_cid: continue
        norm_cid = _normalize_segment_title_for_canonical_match(raw_cid)
        for m in mapping_points:
            if _segment_matches_canonical(norm_cid, m, {}):
                target_id = m.get("canonical_id")
                if target_id:
                    if "component_id" in comp: comp["component_id"] = target_id
                    if "point_id" in comp: comp["point_id"] = target_id
                    # Ensure this canonical ID is allowed if not already
                    allowed_point_ids.add(target_id)
                break

    score_total = analysis_output.get("score_total", 0)
    score_by_category = analysis_output.get("score_by_category", [])
    if not isinstance(score_by_category, list):
        score_by_category = []
    if not isinstance(score_by_category, list):
        score_by_category = []
    top_score_drivers = analysis_output.get("top_score_drivers", [])
    legality_rule_meta = _get_legality_rule_meta()
    blocked_by: List[str] = []

    feedback_findings: List[Dict[str, object]] = []
    finding_ids_by_point: Dict[str, List[str]] = {}
    deduction_totals: Dict[str, int] = {}

    def _build_feedback_evidence(
        evidence_items: List[Dict[str, object]],
        point_meta: Dict[str, object],
        fallback_text: str,
    ) -> Dict[str, object]:
        evidence = None
        if evidence_items:
            item = evidence_items[0]
            if isinstance(item, dict):
                evidence = {
                    "page": int(item.get("page", 1) or 1),
                    "snippet": item.get("snippet") or item.get("text") or "",
                    "match": item.get("match_explain") or "Derived from evidence.",
                }
        if not evidence or not evidence.get("snippet"):
            evidence = {
                "page": int(point_meta.get("page_start", 1) or 1),
                "snippet": point_meta.get("excerpt") or fallback_text or "",
                "match": "Derived from point header excerpt.",
            }
        if not evidence.get("snippet"):
            evidence["snippet"] = "Ikke tilgjengelig."
        return evidence

    used_deductions_by_point: Dict[str, set] = {}
    for component in analysis_output.get("findings", []):
        if not isinstance(component, dict):
            continue
        point_id = component.get("component_id") or ""
        if not point_id:
            continue
        if point_id not in allowed_point_ids:
            continue
        deductions = component.get("deductions", []) if isinstance(component.get("deductions"), list) else []
        deduction_totals[point_id] = sum(
            int(d.get("points", 0))
            for d in deductions
            if isinstance(d, dict) and not _is_report_level_rule(str(d.get("rule_id") or ""))
        )
        used_deductions_by_point[point_id] = set()
        issues = component.get("issues", []) if isinstance(component.get("issues"), list) else []
        point_meta = point_lookup.get(point_id) or {}
        if not isinstance(point_meta, dict):
            point_meta = {}
        for issue_idx, issue in enumerate(issues):
            if not isinstance(issue, dict):
                continue
            rule_refs = issue.get("rule_refs", []) if isinstance(issue.get("rule_refs"), list) else []
            rule_id = rule_refs[0] if rule_refs else "unknown"
            internal_point_id = point_id
            feedback_point_id = _public_point_reference(internal_point_id, rule_id)
            raw_severity = issue.get("severity", "medium")
            severity = _normalize_issue_severity(str(raw_severity))
            evidence_items = issue.get("evidence", []) if isinstance(issue.get("evidence"), list) else []
            evidence = _build_feedback_evidence(
                evidence_items,
                point_meta if isinstance(point_meta, dict) else {},
                issue.get("details") or issue.get("summary") or "",
            )

            finding_id = f"f-{point_id}-{issue_idx + 1:03d}"
            point_key = point_meta.get("point_key") if isinstance(point_meta, dict) else None
            rule_family = _derive_rule_family(rule_id)
            affects_96_gate = bool(legality_rule_meta.get(rule_id, {}).get("blocks_96_gate"))
            if affects_96_gate and rule_id not in blocked_by:
                blocked_by.append(rule_id)
            arkat_section = "annet"
            example_fix = issue.get("details") or issue.get("summary") or "Se forbedringsforslag."
            if rule_family == "LEGALITY":
                arkat_example = _build_legality_arkat_example(rule_id)
                if arkat_example:
                    arkat_section = "anbefalt_tiltak"
                    example_fix = arkat_example
            clean_issue_example_fix = _clean_feedback_example_text(
                issue.get("suggested_rewrite_text"),
                example_fix,
            )
            deduction_points = 0
            matched_idx = None
            for idx, deduction in enumerate(deductions):
                if idx in used_deductions_by_point[point_id]:
                    continue
                if isinstance(deduction, dict) and deduction.get("rule_id") == rule_id:
                    matched_idx = idx
                    deduction_points = int(deduction.get("points", 0) or 0)
                    break
            if matched_idx is not None:
                used_deductions_by_point[point_id].add(matched_idx)

            feedback_findings.append(
                {
                    "finding_id": finding_id,
                    "rule_id": rule_id,
                    "rule_family": rule_family,
                    "severity": severity,
                    "affects_96_gate": affects_96_gate,
                    "point_id": feedback_point_id,
                    "point_key": point_key or internal_point_id or feedback_point_id,
                    "arkat_section": arkat_section,
                    "message": issue.get("summary") or "Avvik",
                    "what_to_change": issue.get("details") or issue.get("summary") or "Se forbedringsforslag.",
                    "example_fix": {
                        "good_example": clean_issue_example_fix,
                    },
                    "evidence": evidence,
                    "deduction": deduction_points,
                }
            )
            if internal_point_id:
                finding_ids_by_point.setdefault(internal_point_id, []).append(finding_id)

        for deduction_idx, deduction in enumerate(deductions):
            if deduction_idx in used_deductions_by_point[point_id]:
                continue
            if not isinstance(deduction, dict):
                continue
            rule_id = deduction.get("rule_id") or "unknown"
            internal_point_id = point_id
            feedback_point_id = _public_point_reference(internal_point_id, str(rule_id))
            rule_family = _derive_rule_family(str(rule_id))
            affects_96_gate = bool(legality_rule_meta.get(rule_id, {}).get("blocks_96_gate"))
            if affects_96_gate and rule_id not in blocked_by:
                blocked_by.append(rule_id)
            raw_severity = deduction.get("severity") or ("critical" if affects_96_gate else "medium")
            severity = _normalize_issue_severity(str(raw_severity))
            evidence_items = deduction.get("evidence", []) if isinstance(deduction.get("evidence"), list) else []
            evidence = _build_feedback_evidence(
                evidence_items,
                point_meta if isinstance(point_meta, dict) else {},
                deduction.get("reason") or "",
            )
            clean_deduction_example_fix = _clean_feedback_example_text(
                deduction.get("suggested_rewrite_text"),
                deduction.get("reason") or "",
            )
            finding_id = f"f-{point_id}-d{deduction_idx + 1:03d}"
            point_key = point_meta.get("point_key") if isinstance(point_meta, dict) else None
            feedback_findings.append(
                {
                    "finding_id": finding_id,
                    "rule_id": rule_id,
                    "rule_family": rule_family,
                    "severity": severity,
                    "affects_96_gate": affects_96_gate,
                    "point_id": feedback_point_id,
                    "point_key": point_key or internal_point_id or feedback_point_id,
                    "arkat_section": "annet",
                    "message": deduction.get("reason") or "Trekk registrert.",
                    "what_to_change": deduction.get("reason") or "Oppdater punktet for å fjerne trekket.",
                    "example_fix": {
                        "good_example": clean_deduction_example_fix,
                    },
                    "evidence": evidence,
                    "deduction": int(deduction.get("points", 0) or 0),
                }
            )
            if internal_point_id:
                finding_ids_by_point.setdefault(internal_point_id, []).append(finding_id)

    _ensure_feedback_findings_cover_deductions(
        feedback_findings,
        deduction_totals,
        finding_ids_by_point,
        point_lookup,
    )

    existing_feedback_keys = {
        (
            _normalize_point_id(str(item.get("point_id") or "")),
            str(item.get("rule_id") or ""),
        )
        for item in feedback_findings
        if isinstance(item, dict)
    }
    for idx, item in enumerate(analysis_output.get("all_findings") or []):
        if not isinstance(item, dict):
            continue
        point_id = _normalize_point_id(
            str(
                item.get("exact_point_id")
                or item.get("point_id")
                or _parse_runtime_point_ref_from_v16_finding(item)
                or _parse_point_id_from_v16_finding(item)
                or ""
            )
        )
        rule_id = str(item.get("rule_id") or item.get("finding_id") or "")
        internal_point_id = point_id
        feedback_point_id = _public_point_reference(internal_point_id, rule_id)
        if (internal_point_id and internal_point_id not in allowed_point_ids) or not rule_id:
            continue
        if (feedback_point_id, rule_id) in existing_feedback_keys:
            continue
        is_visible_info_finding = rule_id in {
            "E_METHOD.egenerklaring_missing",
            "E_METHOD.vinterhage_not_assessed_ns3600",
        }
        if _is_mechanical_arkat_public_finding(item):
            continue
        if not (_is_public_scored_finding(item) or is_visible_info_finding):
            continue
        point_meta = point_lookup.get(point_id) or {}
        evidence_snippets = item.get("evidence_snippets") or []
        snippet = (
            str(item.get("exact_point_text") or "").strip()
            or (evidence_snippets[0] if evidence_snippets and isinstance(evidence_snippets[0], str) else "")
            or str(item.get("message") or item.get("title") or "Ikke tilgjengelig.")
        )
        deduction_points = {"Lavt trekk": 1, "Middels trekk": 3, "Høyt trekk": 5}.get(
            str(item.get("deduction_band") or "").strip(),
            0,
        )
        feedback_id = f"f-backstop-{point_id}-{idx + 1:03d}"
        clean_example = _clean_feedback_example_text(
            item.get("suggested_rewrite_text"),
            item.get("recommended_fix_text") or item.get("message") or "",
        )
        if not clean_example:
            clean_example = _build_source_grounded_rewrite_fallback(item)
        feedback_findings.append(
            {
                "finding_id": feedback_id,
                "rule_id": rule_id,
                "rule_family": _derive_rule_family(rule_id) or "UNKNOWN",
                "severity": "high" if item.get("severity") == "major" else "medium" if item.get("severity") == "minor" else "low",
                "affects_96_gate": bool(isinstance(item.get("gate_effect"), dict) and item.get("gate_effect", {}).get("blocks_96_gate")),
                "point_id": feedback_point_id,
                "point_key": point_meta.get("point_key") or internal_point_id or feedback_point_id,
                "arkat_section": "annet",
                "message": item.get("title") or item.get("message") or "Avvik",
                "what_to_change": item.get("recommended_fix_text") or item.get("message") or "Se forbedringsforslag.",
                "example_fix": {"good_example": clean_example},
                "evidence": {
                    "page": int(point_meta.get("page_start", 1) or 1),
                    "snippet": snippet[:500] if snippet else "Ikke tilgjengelig.",
                    "match": "From exact point source." if item.get("exact_point_text") else "From all_findings.",
                },
                "deduction": deduction_points,
            }
        )
        if internal_point_id:
            finding_ids_by_point.setdefault(internal_point_id, []).append(feedback_id)
            deduction_totals[internal_point_id] = deduction_totals.get(internal_point_id, 0) + deduction_points
        existing_feedback_keys.add((feedback_point_id, rule_id))

    mode, dedupe_key, sorted_points = _sort_points(points)
    ordering_note = "Sortert numerisk (parent før child)." if mode == "NUMERIC" else "Sortert etter dokumentrekkefølge."

    mapping_cfg = get_points_overview_mapping_config()
    canonical_cfg = get_canonical_points_v30()
    
    parent_cards = mapping_cfg.get("parent_cards") or canonical_cfg.get("parents") or []
    parent_cards = _sorted_parent_cards(parent_cards)
    child_mappings = mapping_cfg.get("child_mappings") or []
    
    if not child_mappings and parent_cards and any("children" in p for p in parent_cards):
        for p in parent_cards:
            pid = p.get("canonical_id")
            for c in p.get("children", []):
                child_mappings.append({
                    "child_id": c.get("child_id") or c.get("id"),
                    "parent_id": pid
                })
                
    ui_overlay_cfg = get_ui_overlay_config()
    
    if parent_cards and child_mappings:
        # 12-parent UI model roll-up logic
        migration_map = get_migration_map().get("old_parent_to_new_parent", {})
        
        # Build mapping and apply migration at the same time
        child_to_parent = {}
        for m in child_mappings:
            if "child_id" in m and "parent_id" in m:
                pid = m["parent_id"]
                # Apply migration
                mapped_pid = migration_map.get(pid, pid)
                child_to_parent[m["child_id"]] = mapped_pid
        
        parent_deductions = {p["canonical_id"]: 0 for p in parent_cards}
        parent_finding_ids = {p["canonical_id"]: [] for p in parent_cards}
        parent_worst_tg = {p["canonical_id"]: "" for p in parent_cards}
        parent_found_status = {p["canonical_id"]: "NOT_FOUND_IN_REPORT" for p in parent_cards}
        parent_where = {p["canonical_id"]: {} for p in parent_cards}
        parent_has_scored_signal = {p["canonical_id"]: False for p in parent_cards}
        report_has_p12 = bool(re.search(r"(?ix)\b(?:tilleggsopplysninger|v[æa]r\s+oppmerksom\s+p[åa]|anbefalte?\s+ytterligere\s+unders[øo]kelser)\b", _normalize_tg3_cost_text(report_text or "")))

        # Roll up values from all detected points (mark FOUND even if no findings)
        for point_id in allowed_point_ids:
            if parent_id := child_to_parent.get(point_id):
                if parent_id in parent_found_status:
                    parent_found_status[parent_id] = "FOUND"
                    point_meta = point_lookup.get(point_id)
                    if point_meta and not parent_where[parent_id]:
                        parent_where[parent_id] = {"page": int(point_meta.get("page_start", 1))}
        for point in presence_points:
            if not isinstance(point, dict):
                continue
            for parent_id in _point_overview_parent_candidates(point):
                if parent_id in parent_found_status:
                    parent_found_status[parent_id] = "FOUND"
                    tg = str(point.get("tg") or "").upper()
                    if tg != "TGIU" and _tg_rank(tg) > _tg_rank(parent_worst_tg[parent_id]):
                        parent_worst_tg[parent_id] = tg
                    if not parent_where[parent_id]:
                        parent_where[parent_id] = {"page": int(point.get("page_start") or 1)}
        # E3 heading fallback: some P11/P12 sections are detected as headings (not canonical child IDs).
        # If whitelist preserved such a heading with explicit parent hint, mark parent as FOUND.
        for point in presence_points:
            if not isinstance(point, dict):
                continue
            hinted_parent = str(point.get("e3_parent_hint") or "").strip().upper()
            if hinted_parent not in {"P11", "P12"}:
                continue
            target_parent = (
                "P11_LAWFULNESS_AND_SAFETY"
                if hinted_parent == "P11"
                else "P12_SUPPLEMENTARY_INFORMATION"
            )
            if target_parent in parent_found_status:
                parent_found_status[target_parent] = "FOUND"
                if not parent_where[target_parent]:
                    parent_where[target_parent] = {"page": int(point.get("page_start") or 1)}

        # Roll up values from all processed points WITH findings
        for point_id, deduction in deduction_totals.items():
            if point_id in parent_deductions:
                parent_deductions[point_id] += deduction
                parent_finding_ids[point_id].extend(finding_ids_by_point.get(point_id, []))
                parent_found_status[point_id] = "FOUND"
                point_meta = point_lookup.get(point_id)
                if point_meta:
                    tg = str(point_meta.get("tg") or "").upper()
                    if tg != "TGIU" and _tg_rank(tg) > _tg_rank(parent_worst_tg[point_id]):
                        parent_worst_tg[point_id] = tg
                    if not parent_where[point_id] or point_meta.get("page_start", 999) < parent_where[point_id].get("page", 999):
                        parent_where[point_id] = {"page": int(point_meta.get("page_start", 1))}
            parent_id = child_to_parent.get(point_id)
            if parent_id in parent_deductions:
                parent_deductions[parent_id] += deduction
                parent_finding_ids[parent_id].extend(finding_ids_by_point.get(point_id, []))
                parent_found_status[parent_id] = "FOUND"
                point_meta = point_lookup.get(point_id)
                if point_meta:
                    tg = str(point_meta.get("tg") or "").upper()
                    if tg != "TGIU" and _tg_rank(tg) > _tg_rank(parent_worst_tg[parent_id]):
                        parent_worst_tg[parent_id] = tg
                    if not parent_where[parent_id] or point_meta.get("page_start", 999) < parent_where[parent_id].get("page", 999):
                        parent_where[parent_id] = {"page": int(point_meta.get("page_start", 1))}
            point_meta = point_lookup.get(point_id)
            if isinstance(point_meta, dict):
                for inferred_parent in _point_overview_parent_candidates(point_meta):
                    if inferred_parent == parent_id:
                        continue
                    if (
                        inferred_parent in parent_deductions
                        and not child_to_parent.get(point_id)
                    ):
                        parent_deductions[inferred_parent] += deduction
                        parent_finding_ids[inferred_parent].extend(finding_ids_by_point.get(point_id, []))
                        parent_found_status[inferred_parent] = "FOUND"
                        tg = str(point_meta.get("tg") or "").upper()
                        if tg != "TGIU" and _tg_rank(tg) > _tg_rank(parent_worst_tg[inferred_parent]):
                            parent_worst_tg[inferred_parent] = tg
                        if not parent_where[inferred_parent] or point_meta.get("page_start", 999) < parent_where[inferred_parent].get("page", 999):
                            parent_where[inferred_parent] = {"page": int(point_meta.get("page_start", 1))}

        for finding in all_findings_source:
            if not isinstance(finding, dict) or not _is_public_scored_finding(finding):
                continue
            finding_id = str(finding.get("finding_id") or finding.get("rule_id") or "")
            finding_tg = _finding_declared_tg(finding)
            explicit_point_id = _normalize_point_id(
                str(
                    finding.get("exact_point_id")
                    or finding.get("point_id")
                    or _parse_runtime_point_ref_from_v16_finding(finding)
                    or _parse_point_id_from_v16_finding(finding)
                    or ""
                )
            )
            explicit_parent_id = child_to_parent.get(explicit_point_id) or (
                explicit_point_id if explicit_point_id in parent_found_status else ""
            )
            if explicit_parent_id in parent_found_status:
                parent_found_status[explicit_parent_id] = "FOUND"
                parent_has_scored_signal[explicit_parent_id] = True
                if finding_id:
                    parent_finding_ids[explicit_parent_id].append(finding_id)
                if finding_tg and finding_tg != "TGIU" and _tg_rank(finding_tg) > _tg_rank(parent_worst_tg[explicit_parent_id]):
                    parent_worst_tg[explicit_parent_id] = finding_tg
            # Numeric fallback: when finding text carries punktnummer (e.g. "1.1")
            # but strict parent matching misses, still map scored signal to Pxx parent.
            blob_for_point_refs = _normalize_tg3_cost_text(
                " ".join(
                    [
                        str(finding.get("point_id") or ""),
                        str(finding.get("exact_point_id") or ""),
                        str(finding.get("exact_point_title") or ""),
                        str(finding.get("title") or ""),
                        str(finding.get("message") or ""),
                        str(finding.get("reason") or ""),
                        str(finding.get("exact_point_text") or ""),
                    ]
                )
            )
            numeric_parent_candidates: set = set()
            for num_pid in _extract_numeric_point_ids_from_text(blob_for_point_refs):
                first_part = num_pid.split(".")[0]
                if not first_part.isdigit():
                    continue
                p_prefix = f"P{int(first_part):02d}_"
                parent_match = next((k for k in parent_found_status.keys() if str(k).startswith(p_prefix)), "")
                if parent_match:
                    numeric_parent_candidates.add(parent_match)
            for parent_id in numeric_parent_candidates:
                parent_found_status[parent_id] = "FOUND"
                parent_has_scored_signal[parent_id] = True
                if finding_id:
                    parent_finding_ids[parent_id].append(finding_id)
                if finding_tg and finding_tg != "TGIU" and _tg_rank(finding_tg) > _tg_rank(parent_worst_tg[parent_id]):
                    parent_worst_tg[parent_id] = finding_tg
            for parent_id in _all_finding_overview_parent_candidates(finding):
                if parent_id not in parent_found_status:
                    continue
                parent_found_status[parent_id] = "FOUND"
                parent_has_scored_signal[parent_id] = True
                if finding_id:
                    parent_finding_ids[parent_id].append(finding_id)
                if finding_tg and finding_tg != "TGIU" and _tg_rank(finding_tg) > _tg_rank(parent_worst_tg[parent_id]):
                    parent_worst_tg[parent_id] = finding_tg

        not_found_text = ui_overlay_cfg.get("not_found_policy", {}).get("ui_text_nb", "Ikke funnet i rapport")
        points_overview = []
        for idx, p in enumerate(parent_cards):
            pid = p["canonical_id"]
            if pid == "P12_SUPPLEMENTARY_INFORMATION" and report_has_p12:
                parent_found_status[pid] = "FOUND"
            status = parent_found_status[pid]
            deduction = parent_deductions[pid]
            tg = parent_worst_tg[pid] if status == "FOUND" else "UNKNOWN"
            if status == "FOUND" and _tg_rank(tg) < 0:
                tg = "N/A"
            # Special rule: HMS/Lovlighet (LOVPAALAGT_AK_IKKE_TG) category never shows TG
            if p.get("requirement_tag") in ("LOVPAALAGT_AK_IKKE_TG", "LOVLIGHET_OG_SIKKERHET"):
                tg = "N/A" if status == "FOUND" else tg
            
            # Status should be neutral for NOT_FOUND_IN_REPORT
            if status == "NOT_FOUND_IN_REPORT":
                parent_summary = "Ikke vurdert i rapport" 
                tg = "N/A"
            else:
                parent_summary = "Avvik funnet" if deduction > 0 or parent_has_scored_signal[pid] or _tg_rank(tg) >= _tg_rank("TG2") else "OK"

            # Reconstruct children for this parent
            children_for_parent = []
            for child_id, parent_val in child_to_parent.items():
                if parent_val == pid:
                    child_status = "FOUND" if child_id in allowed_point_ids else "NOT_FOUND_IN_REPORT"
                    child_deduction = deduction_totals.get(child_id, 0)
                    child_meta = point_lookup.get(child_id, {})
                    child_tg = str(child_meta.get("tg", "")).upper() if child_status == "FOUND" else "N/A"
                    if child_status == "FOUND" and _tg_rank(child_tg) < 0:
                        child_tg = "N/A"
                    if p.get("requirement_tag") in ("LOVPAALAGT_AK_IKKE_TG", "LOVLIGHET_OG_SIKKERHET"):
                        child_tg = "N/A"
                        
                    children_for_parent.append({
                        "point_id": child_id,
                        "title": _resolve_child_title(child_id, child_meta),
                        "status": child_status,
                        "deduction_band": _deduction_band_from_numeric(child_deduction),
                        "deduction_total": child_deduction,
                        "tg": child_tg,
                        "finding_ids": finding_ids_by_point.get(child_id, []),
                        "where": {"page": child_meta.get("page_start", 1)} if child_meta else {}
                    })

            if pid == "P06_ROOMS_BELOW_GRADE" and status != "NOT_FOUND_IN_REPORT":
                found_child_tgs = [
                    str(child.get("tg") or "").upper()
                    for child in children_for_parent
                    if str(child.get("status") or "") == "FOUND"
                ]
                worst_child_tg = max(found_child_tgs, key=_tg_rank, default="")
                if _tg_rank(worst_child_tg) >= 0:
                    tg = worst_child_tg
                if _tg_rank(tg) >= _tg_rank("TG2"):
                    parent_summary = "Avvik funnet"

            points_overview.append({
                "display_index": idx + 1,
                "canonical_id": pid,
                "point_id": None,
                "title": p.get("title_nb") or p.get("label_nb", "Ukjent"),
                "title_display": p.get("title_nb") or p.get("label_nb", "Ukjent"),
                "requirement_tag": p.get("requirement_tag", "IKKE_LOVPAALAGT"),
                "status": status,
                "deduction_band": _deduction_band_from_numeric(deduction),
                "deduction_total": deduction,
                "tg": tg,
                "summary": parent_summary,
                "finding_ids": list(set(parent_finding_ids[pid])),
                "where": parent_where[pid],
                "children": children_for_parent
            })

        # Keep strict canonical parent order (display_order from parent_cards).
        ordering_note = "12-parent roll-up architecture v3.9 (canonical display_order)."
    elif (
        (canonical_cfg := get_canonical_points_v30()) 
        and (canonical_points := canonical_cfg.get("canonical_points") or canonical_cfg.get("points"))
    ):
        canonical_requirement_tags = canonical_cfg.get("requirement_tags") if isinstance(canonical_cfg, dict) else {}
        mapping_points: List[Dict[str, object]] = []
        if isinstance(points, list):
            mapping_points.extend([dict(p) for p in points if isinstance(p, dict)])
        if isinstance(points_before_whitelist, list):
            seen_ids = {
                str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                for p in mapping_points
                if isinstance(p, dict)
            }
            for p in points_before_whitelist:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                if pid and pid in seen_ids:
                    continue
                mapping_points.append(dict(p))
        segment_map = _map_segments_to_canonical(mapping_points, canonical_points)
        _emit_canonical_mapping_debug(
            report_id=report_id,
            points=mapping_points,
            canonical_points=canonical_points,
            segment_map=segment_map,
            detected_points_payload=detected_points_payload if isinstance(detected_points_payload, dict) else None,
        )
        points_overview = _build_points_overview_from_canonical(
            canonical_points,
            segment_map,
            deduction_totals,
            finding_ids_by_point,
            point_lookup,
            requirement_tags=canonical_requirement_tags if isinstance(canonical_requirement_tags, dict) else {},
            ui_overlay=ui_overlay_cfg if isinstance(ui_overlay_cfg, dict) else {},
        )
        ordering_note = "Fast liste fra canonical_points (display_order)."
    else:
        skip_parent_same_title = _compute_parent_child_same_title_skips(sorted_points)
        points_overview = []
        display_index = 1
        for point in sorted_points:
            if not isinstance(point, dict):
                continue
            kind = point.get("kind")
            if isinstance(kind, str) and kind not in ("point", "subpoint"):
                continue
            point_id = point.get("point_id") or point.get("numeric_id") or point.get("native_label") or ""
            if point_id in skip_parent_same_title:
                continue
            if _is_non_bygningsdel_point(point):
                continue
            point_key = point.get("point_key") or point_id
            component = next(
                (
                    c
                    for c in analysis_output.get("findings", [])
                    if isinstance(c, dict) and c.get("component_id") == point_id
                ),
                None,
            )
            issues = (component.get("issues", []) if isinstance(component, dict) else []) or []
            deduction_total = int(deduction_totals.get(point_id, 0))
            has_issues = bool(issues)
            status = "ok"
            if deduction_total > 0:
                status = "deduction"
            elif has_issues:
                status = "improve"
            summary = "OK – ingen endringer nødvendig."
            if status == "improve":
                summary = (issues[0].get("summary") if issues else "") or "Mindre forbedringer anbefales."
            elif status == "deduction":
                summary = (issues[0].get("summary") if issues else "") or "Trekk er registrert for punktet."

            tg_value = point.get("tg") or (component.get("tg") if isinstance(component, dict) else "") or "UNKNOWN"
            where = {
                "page": int(point.get("page_start") or 1),
            }
            if point.get("anchor_text"):
                where["anchor_text"] = point.get("anchor_text")
            if point.get("bbox"):
                where["bbox"] = point.get("bbox")
            parent_id = None
            if "." in str(point_id):
                parts = str(point_id).split(".")
                if len(parts) >= 2:
                    parent_id = ".".join(parts[:-1])
            title_display = point.get("title") or "Ukjent"
            if point.get("instance_label"):
                title_display = f"{title_display} – {point.get('instance_label')}"
            points_overview.append(
                {
                    "display_index": display_index,
                    "point_id": point_id,
                    "point_key": point_key,
                    "parent_id": parent_id,
                    "native_label": point.get("native_label") or point_id or point_key or "Ukjent",
                    "numeric_id": point.get("numeric_id") or (_numeric_id_for_point(point) or None),
                    "native_path": point.get("native_path"),
                    "title": point.get("title") or "Ukjent",
                    "title_display": title_display,
                    "tg": tg_value,
                    "status": status,
                    "summary": summary,
                    "deduction_total": max(deduction_total, 0),
                    "finding_ids": finding_ids_by_point.get(point_id, []),
                    "where": where,
                    "legal_status": point.get("legal_status"),
                    "required_by_forskrift": point.get("required_by_forskrift", True),
                    "ui_badge": point.get("ui_badge"),
                    "instance_label": point.get("instance_label"),
                }
            )
            display_index += 1

    score_by_category = _ensure_score_by_category(score_by_category)
    blocked_96 = bool(blocked_by)
    score_caps = analysis_output.get("score_caps", []) if isinstance(analysis_output, dict) else []
    if isinstance(score_caps, list):
        for cap_idx, cap in enumerate(score_caps):
            if not isinstance(cap, dict):
                continue
            points_deducted = int(cap.get("points_deducted", 0) or 0)
            if points_deducted <= 0:
                continue
            rule_ids = cap.get("rule_ids", [])
            if not isinstance(rule_ids, list):
                rule_ids = []
            rule_ids_text = ", ".join([str(rid) for rid in rule_ids if rid])
            message = (
                f"Scoretak er aktivert ({cap.get('max_total_score')})."
                + (f" Utløst av: {rule_ids_text}." if rule_ids_text else "")
            )
            feedback_findings.append(
                {
                    "finding_id": f"f-global-cap-{cap_idx + 1:03d}",
                    "rule_id": "SCORE_CAP",
                    "rule_family": "SYSTEM",
                    "severity": "high",
                    "affects_96_gate": False,
                    "point_id": "",
                    "point_key": "",
                    "arkat_section": "annet",
                    "message": message,
                    "what_to_change": "Utbedre forholdet som utløser scoretaket for å heve totalscoren.",
                    "example_fix": {
                        "good_example": "Fjern lovlighetsmangelen som utløser scoretaket.",
                    },
                    "evidence": {
                        "page": 1,
                        "snippet": "Ikke tilgjengelig.",
                        "match": "Derived from score cap metadata.",
                    },
                    "deduction": points_deducted,
                }
            )
    if blocked_96:
        blocked_text = ", ".join(blocked_by) if blocked_by else "ukjent regel"
        feedback_findings.append(
            {
                "finding_id": "f-global-gate-001",
                "rule_id": "GATE_96",
                "rule_family": "SYSTEM",
                "severity": "critical",
                "affects_96_gate": True,
                "point_id": "",
                "point_key": "",
                "arkat_section": "annet",
                "message": f"96%-gate er blokkert. Utløst av: {blocked_text}.",
                "what_to_change": "Fjern forholdet som blokkerer 96%-gaten for å kunne nå 96%+.",
                "example_fix": {
                    "good_example": "Utbedre lovlighetsmangel eller regelbrudd som utløser 96%-gaten.",
                },
                "evidence": {
                    "page": 1,
                    "snippet": "Ikke tilgjengelig.",
                    "match": "Derived from gate metadata.",
                },
                "deduction": 0,
            }
        )

    feedback_findings, top_score_drivers = _reconcile_feedback_deduction_consistency(
        feedback_findings,
        score_by_category,
        top_score_drivers if isinstance(top_score_drivers, list) else [],
    )
    _force_parent_points_overview_scored_status(
        points_overview,
        [f for f in all_findings_source if isinstance(f, dict)],
    )
    _ensure_special_feedback_findings_visible(analysis_output, feedback_findings)
    _sanitize_feedback_missing_tiltak_findings(
        feedback_findings,
        {
            _normalize_point_id(str(pid)): _normalize_tg_label(point.get("tg") or point.get("tg_grade"))
            for pid, point in point_lookup.items()
            if isinstance(pid, str) and isinstance(point, dict)
        },
    )
    _polish_feedback_findings(feedback_findings)
    return {
        "version": "v1.1",
        "report_id": str(report_id) if report_id else "unknown_report",
        "document_hash": document_hash or "unknown_hash",
        "ordering": {
            "mode": mode,
            "dedupe_key": dedupe_key,
            "source": "canonical_points" if canonical_points else "detected_points",
            "note": ordering_note,
        },
        "score": {
            "total": score_total,
            "category_deductions": [
                {
                    "category": item.get("category_id", ""),
                    "deduction": item.get("deduction", 0),
                    "max_deduction": item.get("max_deduction", 0),
                }
                for item in score_by_category
                if isinstance(item, dict)
            ],
            "top_drivers": [
                {
                    "rule_id": (driver.get("rule_refs") or ["unknown_rule"])[0] or "unknown_rule",
                    "deduction": driver.get("deduction_points", 0),
                    "message": driver.get("reason") or driver.get("title") or "Trekkgrunnlag",
                }
                for driver in top_score_drivers
                if isinstance(driver, dict)
            ],
        },
        "gate": {
            "active": True,
            "blocked_96": blocked_96,
            "blocked_by": blocked_by,
        },
        "points_overview": points_overview,
        "findings": feedback_findings,
    }


def _build_feedback_v11_from_all_findings(
    analysis_output: Dict[str, object],
    detected_points_payload: Dict[str, object],
    report_id: Optional[str],
    document_hash: Optional[str],
) -> Dict[str, object]:
    """Build feedback v1.1 from v1.6 all_findings when legacy findings is empty (admin/user consistency)."""
    points = detected_points_payload.get("points", []) if isinstance(detected_points_payload, dict) else []
    points_before_whitelist = (
        detected_points_payload.get("points_before_whitelist", [])
        if isinstance(detected_points_payload, dict)
        else []
    )
    presence_points: List[Dict[str, object]] = []
    if isinstance(points, list):
        presence_points.extend([p for p in points if isinstance(p, dict)])
    if isinstance(points_before_whitelist, list):
        seen_presence_ids = {
            str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
            for p in presence_points
            if isinstance(p, dict)
        }
        for p in points_before_whitelist:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
            if pid and pid in seen_presence_ids:
                continue
            presence_points.append(p)

    # V3.9 Architecture: Align raw segments with canonical IDs
    mapping_cfg = get_points_overview_mapping_config()
    child_mappings = mapping_cfg.get("child_mappings") or []
    mapping_points = []
    if child_mappings and points:
        for m in child_mappings:
            if isinstance(m, dict):
                m_copy = dict(m)
                if "child_id" in m_copy:
                    m_copy["canonical_id"] = m_copy["child_id"]
                mapping_points.append(m_copy)

        # Use existing matcher on both whitelist points and pre-whitelist points
        # so strict whitelist drops can still be recovered during canonical roll-up.
        mapping_input_points: List[Dict[str, object]] = []
        if isinstance(points, list):
            mapping_input_points.extend([dict(p) for p in points if isinstance(p, dict)])
        if isinstance(points_before_whitelist, list):
            seen_ids = {
                str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                for p in mapping_input_points
                if isinstance(p, dict)
            }
            for p in points_before_whitelist:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                if pid and pid in seen_ids:
                    continue
                mapping_input_points.append(dict(p))

        segment_map = _map_segments_to_canonical(mapping_input_points, mapping_points)
        for canon_id, seg in segment_map.items():
            # Force the segment ID to match the canonical child ID so roll-up logic finds it
            seg["point_id"] = canon_id

    allowed_point_ids = set()
    point_lookup: Dict[str, Dict[str, object]] = {}
    for point in points:
        if not isinstance(point, dict):
            continue
        pid_keys = [
            point.get("point_id"),
            point.get("numeric_id"),
            point.get("native_label"),
            point.get("title")
        ]
        for key in pid_keys:
            if isinstance(key, str) and key:
                allowed_point_ids.add(key)
                point_lookup.setdefault(key, point)

    # Recover canonical children inferred from pre-whitelist mapping (without exposing raw point IDs).
    if mapping_points:
        mapping_input_points: List[Dict[str, object]] = []
        if isinstance(points, list):
            mapping_input_points.extend([dict(p) for p in points if isinstance(p, dict)])
        if isinstance(points_before_whitelist, list):
            seen_ids = {
                str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                for p in mapping_input_points
                if isinstance(p, dict)
            }
            for p in points_before_whitelist:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                if pid and pid in seen_ids:
                    continue
                mapping_input_points.append(dict(p))
        pre_segment_map = _map_segments_to_canonical(mapping_input_points, mapping_points)
        for canon_id, seg in pre_segment_map.items():
            if canon_id:
                allowed_point_ids.add(canon_id)
                point_lookup.setdefault(canon_id, seg)

    all_findings = analysis_output.get("all_findings") or []
    # V3.9 Architecture: Force findings to use canonical IDs if they match aliases/regex
    for comp in all_findings:
        if not isinstance(comp, dict): continue
        raw_cid = str(comp.get("component_id") or comp.get("point_id") or "")
        if not raw_cid: continue
        norm_cid = _normalize_segment_title_for_canonical_match(raw_cid)
        for m in mapping_points:
            if _segment_matches_canonical(norm_cid, m, {}):
                target_id = m.get("canonical_id")
                if target_id:
                    if "component_id" in comp: comp["component_id"] = target_id
                    if "point_id" in comp: comp["point_id"] = target_id
                    allowed_point_ids.add(target_id)
                break
    score_total = analysis_output.get("score_total") or analysis_output.get("trygghetsscore") or 0
    score_by_category = analysis_output.get("score_by_category") or []
    if not isinstance(score_by_category, list):
        score_by_category = []
    top_score_drivers = analysis_output.get("top_score_drivers") or []
    top_issues = analysis_output.get("top_issues") or []
    if not top_score_drivers and isinstance(top_issues, list):
        top_score_drivers = [
            {
                "rule_refs": [t.get("category", "unknown")],
                "deduction_points": 100 - (t.get("gate_effect") or {}).get("caps_total_score_to", 0) if (t.get("gate_effect") or {}).get("caps_total_score_to") else 0,
                "reason": t.get("message") or t.get("title", ""),
                "title": t.get("title", ""),
            }
            for t in top_issues[:5]
        ]
    gate = analysis_output.get("gate") or {}
    blocked_by: List[str] = list(gate.get("blocked_by") or [])
    band_to_deduction = {"Høyt trekk": 5, "Middels trekk": 3, "Lavt trekk": 1, "Ikke scoretrekk": 0}
    feedback_findings: List[Dict[str, object]] = []
    finding_ids_by_point: Dict[str, List[str]] = {}
    deduction_totals: Dict[str, int] = {}
    point_worst_band: Dict[str, str] = {}
    linked_tg3_count = 0
    for idx, f in enumerate(all_findings):
        if not isinstance(f, dict):
            continue
        is_tg3 = _is_tg3_related_finding(f)
        point_id = str(f.get("exact_point_id") or _parse_runtime_point_ref_from_v16_finding(f) or "").strip()
        mapping_title_candidates: List[str] = []
        for key in ("exact_point_title", "title"):
            value = f.get(key)
            if isinstance(value, str) and value.strip():
                mapping_title_candidates.append(value)
        resolved_canonical_point_id = _resolve_canonical_child_point_id(
            point_id,
            mapping_title_candidates,
            point_lookup,
            mapping_points,
        )
        if resolved_canonical_point_id and _is_canonical_child_point_id(resolved_canonical_point_id):
            point_id = resolved_canonical_point_id
        point_is_linked = bool(
            point_id
            and point_id in allowed_point_ids
            and (_is_canonical_child_point_id(point_id) or _is_scoring_eligible_point_id(point_id))
        )
        is_report_level = _is_report_level_rule(str(f.get("rule_id") or f.get("finding_id") or ""))
        if is_tg3 and not point_is_linked:
            # Never apply TG3 deductions without clear segment linkage.
            continue
        if not point_is_linked and not is_report_level:
            # Tight fallback: unresolved findings are ignored instead of becoming GLOBAL.
            continue
        if is_tg3:
            linked_tg3_count += 1
        rid = f.get("rule_id") or f.get("finding_id") or f"v16-{idx}"
        internal_point_id = point_id
        public_point_id = _public_point_reference(internal_point_id, str(f.get("rule_id") or f.get("finding_id") or ""))
        if isinstance(f.get("gate_effect"), dict) and f.get("gate_effect", {}).get("blocks_96_gate") and rid not in blocked_by:
            blocked_by.append(rid)
        band = (f.get("deduction_band") or "").strip()
        explicit_deduction = f.get("points", f.get("deduction_points", f.get("deduction")))
        try:
            deduction_pts = int(explicit_deduction) if explicit_deduction is not None else band_to_deduction.get(band, 0)
        except (TypeError, ValueError):
            deduction_pts = band_to_deduction.get(band, 0)
        if internal_point_id and not is_report_level:
            deduction_totals[internal_point_id] = deduction_totals.get(internal_point_id, 0) + deduction_pts
            existing_band = point_worst_band.get(internal_point_id, "Ikke scoretrekk")
            if _PUBLIC_BAND_RANK.get(band, 0) > _PUBLIC_BAND_RANK.get(existing_band, 0):
                point_worst_band[internal_point_id] = band
        source_finding_id = str(f.get("finding_id") or "").strip()
        finding_id = source_finding_id or f"unknown-{idx + 1:03d}"
        snips = f.get("evidence_snippets") or []
        snippet = (
            str(f.get("exact_point_text") or "").strip()
            or (snips[0] if snips and isinstance(snips[0], str) else "")
            or (f.get("message") or "Ingen utdrag.")
        )
        customer_title, customer_message, customer_change, customer_example = _customer_text_from_source_finding(
            f,
            str(f.get("title") or f.get("message") or "Avvik"),
        )
        potential_deduction = _finding_potential_deduction(f)
        evidence_page = 1
        page_meta = point_lookup.get(internal_point_id) or point_lookup.get(str(f.get("point_id") or "")) or point_lookup.get(str(f.get("exact_point_id") or "")) or {}
        if isinstance(page_meta, dict):
            evidence_page = int(page_meta.get("page_start") or page_meta.get("page") or 1)
        feedback_findings.append({
            "finding_id": finding_id,
            "source_finding_id": source_finding_id,
            "rule_id": rid,
            "rule_family": _derive_rule_family(str(rid)) or "UNKNOWN",
            "category": str(f.get("category") or _infer_category_from_rule_id(str(rid)) or ""),
            "is_regulatory_breach": bool(f.get("is_regulatory_breach")),
            "severity": str(f.get("severity") or ""),
            "affects_96_gate": bool(isinstance(f.get("gate_effect"), dict) and f.get("gate_effect", {}).get("blocks_96_gate")),
            "point_id": public_point_id,
            "point_key": point_lookup.get(internal_point_id, {}).get("point_key") or internal_point_id or public_point_id,
            "arkat_section": "annet",
            "title": customer_title,
            "message": customer_message,
            "what_to_change": customer_change,
            "example_fix": {"good_example": customer_example},
            "evidence": {"page": evidence_page, "snippet": snippet[:500] if snippet else "Ikke tilgjengelig.", "match": "From exact point source." if f.get("exact_point_text") else "From all_findings."},
            "deduction": deduction_pts,
            "deduction_band": str(f.get("deduction_band") or ""),
            "potential_deduction": int(potential_deduction) if potential_deduction is not None else 0,
        })
        if internal_point_id:
            finding_ids_by_point.setdefault(internal_point_id, []).append(finding_id)

    _ensure_feedback_findings_cover_deductions(
        feedback_findings,
        deduction_totals,
        finding_ids_by_point,
        point_lookup,
    )

    if linked_tg3_count == 0:
        if isinstance(top_issues, list):
            top_issues = [
                t for t in top_issues
                if not (isinstance(t, dict) and _is_tg3_related_finding(t))
            ]
        if isinstance(top_score_drivers, list):
            top_score_drivers = [
                d for d in top_score_drivers
                if not (isinstance(d, dict) and _is_tg3_related_finding(d))
            ]
    mode, dedupe_key, sorted_points = _sort_points(points)
    ordering_note = "Sortert numerisk (parent før child)." if mode == "NUMERIC" else "Sortert etter dokumentrekkefølge."

    mapping_cfg = get_points_overview_mapping_config()
    canonical_cfg = get_canonical_points_v30()
    
    parent_cards = mapping_cfg.get("parent_cards") or canonical_cfg.get("parents") or []
    parent_cards = _sorted_parent_cards(parent_cards)
    child_mappings = mapping_cfg.get("child_mappings") or []
    
    if not child_mappings and parent_cards and any("children" in p for p in parent_cards):
        for p in parent_cards:
            pid = p.get("canonical_id")
            for c in p.get("children", []):
                child_mappings.append({
                    "child_id": c.get("child_id") or c.get("id"),
                    "parent_id": pid
                })
                
    ui_overlay_cfg = get_ui_overlay_config()
    
    if parent_cards and child_mappings:
        # 12-parent UI model roll-up logic (mode A)
        migration_map = get_migration_map().get("old_parent_to_new_parent", {})
        child_to_parent = {}
        for m in child_mappings:
            if "child_id" in m and "parent_id" in m:
                pid = m["parent_id"]
                child_to_parent[m["child_id"]] = migration_map.get(pid, pid)
        
        parent_deductions = {p["canonical_id"]: 0 for p in parent_cards}
        parent_worst_band = {p["canonical_id"]: "none" for p in parent_cards}
        parent_finding_ids = {p["canonical_id"]: [] for p in parent_cards}
        parent_worst_tg = {p["canonical_id"]: "" for p in parent_cards}
        parent_found_status = {p["canonical_id"]: "NOT_FOUND_IN_REPORT" for p in parent_cards}
        parent_where = {p["canonical_id"]: {} for p in parent_cards}
        parent_has_scored_signal = {p["canonical_id"]: False for p in parent_cards}
        e3_presence = _detect_e3_p11_p12_presence(points, points_before_whitelist)
        # This helper does not receive raw report_text, so keep the P12 fallback
        # scoped to already-detected point data available here.
        report_has_p12 = bool(e3_presence.get("P12"))
        
        # Roll up values from all detected points (mark FOUND even if no findings)
        for point_id in allowed_point_ids:
            if parent_id := child_to_parent.get(point_id):
                if parent_id in parent_found_status:
                    parent_found_status[parent_id] = "FOUND"
                    point_meta = point_lookup.get(point_id)
                    if point_meta and not parent_where[parent_id]:
                        parent_where[parent_id] = {"page": int(point_meta.get("page_start", 1))}
        for point in presence_points:
            if not isinstance(point, dict):
                continue
            for parent_id in _point_overview_parent_candidates(point):
                if parent_id in parent_found_status:
                    parent_found_status[parent_id] = "FOUND"
                    tg = str(point.get("tg") or "").upper()
                    if tg != "TGIU" and _tg_rank(tg) > _tg_rank(parent_worst_tg[parent_id]):
                        parent_worst_tg[parent_id] = tg
                    if not parent_where[parent_id]:
                        parent_where[parent_id] = {"page": int(point.get("page_start") or 1)}
        # E3 heading fallback: ensure P11/P12 become FOUND when preserved heading exists
        # but no canonical child ID was linked for that section.
        for point in presence_points:
            if not isinstance(point, dict):
                continue
            hinted_parent = str(point.get("e3_parent_hint") or "").strip().upper()
            if hinted_parent not in {"P11", "P12"}:
                continue
            target_parent = (
                "P11_LAWFULNESS_AND_SAFETY"
                if hinted_parent == "P11"
                else "P12_SUPPLEMENTARY_INFORMATION"
            )
            if target_parent in parent_found_status:
                parent_found_status[target_parent] = "FOUND"
                if not parent_where[target_parent]:
                    parent_where[target_parent] = {"page": int(point.get("page_start") or 1)}

        # Roll up values from all processed points WITH findings
        for point_id, deduction in deduction_totals.items():
            if point_id in parent_deductions:
                parent_deductions[point_id] += deduction
                parent_finding_ids[point_id].extend(finding_ids_by_point.get(point_id, []))
                parent_found_status[point_id] = "FOUND"
                point_meta = point_lookup.get(point_id)
                if point_meta:
                    point_band = _overview_band_from_public_band(point_worst_band.get(point_id, "Ikke scoretrekk"))
                    if _PUBLIC_BAND_RANK.get(point_worst_band.get(point_id, "Ikke scoretrekk"), 0) > _PUBLIC_BAND_RANK.get(
                        {"none": "Ikke scoretrekk", "low": "Lavt trekk", "medium": "Middels trekk", "high": "Høyt trekk"}.get(parent_worst_band[point_id], "Ikke scoretrekk"),
                        0,
                    ):
                        parent_worst_band[point_id] = point_band
                    tg = str(point_meta.get("tg") or "").upper()
                    if tg != "TGIU" and _tg_rank(tg) > _tg_rank(parent_worst_tg[point_id]):
                        parent_worst_tg[point_id] = tg
                    if not parent_where[point_id] or point_meta.get("page_start", 999) < parent_where[point_id].get("page", 999):
                        parent_where[point_id] = {"page": int(point_meta.get("page_start", 1))}
            parent_id = child_to_parent.get(point_id)
            if parent_id in parent_deductions:
                parent_deductions[parent_id] += deduction
                point_band = _overview_band_from_public_band(point_worst_band.get(point_id, "Ikke scoretrekk"))
                if _PUBLIC_BAND_RANK.get(point_worst_band.get(point_id, "Ikke scoretrekk"), 0) > _PUBLIC_BAND_RANK.get(
                    {"none": "Ikke scoretrekk", "low": "Lavt trekk", "medium": "Middels trekk", "high": "Høyt trekk"}.get(parent_worst_band[parent_id], "Ikke scoretrekk"),
                    0,
                ):
                    parent_worst_band[parent_id] = point_band
                parent_finding_ids[parent_id].extend(finding_ids_by_point.get(point_id, []))
                parent_found_status[parent_id] = "FOUND"
                point_meta = point_lookup.get(point_id)
                if point_meta:
                    tg = str(point_meta.get("tg") or "").upper()
                    if tg != "TGIU" and _tg_rank(tg) > _tg_rank(parent_worst_tg[parent_id]):
                        parent_worst_tg[parent_id] = tg
                    if not parent_where[parent_id] or point_meta.get("page_start", 999) < parent_where[parent_id].get("page", 999):
                        parent_where[parent_id] = {"page": int(point_meta.get("page_start", 1))}
            point_meta = point_lookup.get(point_id)
            if isinstance(point_meta, dict):
                for inferred_parent in _point_overview_parent_candidates(point_meta):
                    if inferred_parent == parent_id:
                        continue
                    if (
                        inferred_parent in parent_deductions
                        and not child_to_parent.get(point_id)
                    ):
                        parent_deductions[inferred_parent] += deduction
                        parent_finding_ids[inferred_parent].extend(finding_ids_by_point.get(point_id, []))
                        parent_found_status[inferred_parent] = "FOUND"
                        point_band = _overview_band_from_public_band(point_worst_band.get(point_id, "Ikke scoretrekk"))
                        if _PUBLIC_BAND_RANK.get(point_worst_band.get(point_id, "Ikke scoretrekk"), 0) > _PUBLIC_BAND_RANK.get(
                            {"none": "Ikke scoretrekk", "low": "Lavt trekk", "medium": "Middels trekk", "high": "Høyt trekk"}.get(parent_worst_band[inferred_parent], "Ikke scoretrekk"),
                            0,
                        ):
                            parent_worst_band[inferred_parent] = point_band
                        tg = str(point_meta.get("tg") or "").upper()
                        if tg != "TGIU" and _tg_rank(tg) > _tg_rank(parent_worst_tg[inferred_parent]):
                            parent_worst_tg[inferred_parent] = tg
                        if not parent_where[inferred_parent] or point_meta.get("page_start", 999) < parent_where[inferred_parent].get("page", 999):
                            parent_where[inferred_parent] = {"page": int(point_meta.get("page_start", 1))}

        for finding in all_findings:
            if not isinstance(finding, dict) or not _is_public_scored_finding(finding):
                continue
            finding_id = str(finding.get("finding_id") or finding.get("rule_id") or "")
            finding_tg = _finding_declared_tg(finding)
            explicit_point_id = _normalize_point_id(
                str(
                    finding.get("exact_point_id")
                    or finding.get("point_id")
                    or _parse_runtime_point_ref_from_v16_finding(finding)
                    or _parse_point_id_from_v16_finding(finding)
                    or ""
                )
            )
            explicit_parent_id = child_to_parent.get(explicit_point_id) or (
                explicit_point_id if explicit_point_id in parent_found_status else ""
            )
            if explicit_parent_id in parent_found_status:
                parent_found_status[explicit_parent_id] = "FOUND"
                parent_has_scored_signal[explicit_parent_id] = True
                if finding_id:
                    parent_finding_ids[explicit_parent_id].append(finding_id)
                if finding_tg and finding_tg != "TGIU" and _tg_rank(finding_tg) > _tg_rank(parent_worst_tg[explicit_parent_id]):
                    parent_worst_tg[explicit_parent_id] = finding_tg
            # Numeric fallback: when finding text carries punktnummer (e.g. "1.1")
            # but strict parent matching misses, still map scored signal to Pxx parent.
            blob_for_point_refs = _normalize_tg3_cost_text(
                " ".join(
                    [
                        str(finding.get("point_id") or ""),
                        str(finding.get("exact_point_id") or ""),
                        str(finding.get("exact_point_title") or ""),
                        str(finding.get("title") or ""),
                        str(finding.get("message") or ""),
                        str(finding.get("reason") or ""),
                        str(finding.get("exact_point_text") or ""),
                    ]
                )
            )
            numeric_parent_candidates: set = set()
            for num_pid in _extract_numeric_point_ids_from_text(blob_for_point_refs):
                first_part = num_pid.split(".")[0]
                if not first_part.isdigit():
                    continue
                p_prefix = f"P{int(first_part):02d}_"
                parent_match = next((k for k in parent_found_status.keys() if str(k).startswith(p_prefix)), "")
                if parent_match:
                    numeric_parent_candidates.add(parent_match)
            for parent_id in numeric_parent_candidates:
                parent_found_status[parent_id] = "FOUND"
                parent_has_scored_signal[parent_id] = True
                if finding_id:
                    parent_finding_ids[parent_id].append(finding_id)
                if finding_tg and finding_tg != "TGIU" and _tg_rank(finding_tg) > _tg_rank(parent_worst_tg[parent_id]):
                    parent_worst_tg[parent_id] = finding_tg
            for parent_id in _all_finding_overview_parent_candidates(finding):
                if parent_id not in parent_found_status:
                    continue
                parent_found_status[parent_id] = "FOUND"
                parent_has_scored_signal[parent_id] = True
                if finding_id:
                    parent_finding_ids[parent_id].append(finding_id)
                if finding_tg and finding_tg != "TGIU" and _tg_rank(finding_tg) > _tg_rank(parent_worst_tg[parent_id]):
                    parent_worst_tg[parent_id] = finding_tg

        not_found_text = ui_overlay_cfg.get("not_found_policy", {}).get("ui_text_nb", "Ikke funnet i rapport")
        points_overview = []
        for idx, p in enumerate(parent_cards):
            pid = p["canonical_id"]
            if pid == "P11_LAWFULNESS_AND_SAFETY" and e3_presence.get("P11"):
                parent_found_status[pid] = "FOUND"
            if pid == "P12_SUPPLEMENTARY_INFORMATION" and (e3_presence.get("P12") or report_has_p12):
                parent_found_status[pid] = "FOUND"
            status = parent_found_status[pid]
            deduction = parent_deductions[pid]
            tg = parent_worst_tg[pid] if status == "FOUND" else "UNKNOWN"
            if status == "FOUND" and _tg_rank(tg) < 0:
                tg = "N/A"
            if p.get("requirement_tag") in ("LOVPAALAGT_AK_IKKE_TG", "LOVLIGHET_OG_SIKKERHET"):
                tg = "N/A" if status == "FOUND" else tg
            
            if status == "NOT_FOUND_IN_REPORT":
                parent_summary = "Ikke vurdert i rapport" 
                tg = "N/A"
            else:
                parent_summary = "Avvik funnet" if deduction > 0 or parent_has_scored_signal[pid] or _tg_rank(tg) >= _tg_rank("TG2") else "OK"

            children_for_parent = []
            for child_id, parent_val in child_to_parent.items():
                if parent_val == pid:
                    child_status = "FOUND" if child_id in allowed_point_ids else "NOT_FOUND_IN_REPORT"
                    child_deduction = deduction_totals.get(child_id, 0)
                    child_meta = point_lookup.get(child_id, {})
                    child_tg = str(child_meta.get("tg", "")).upper() if child_status == "FOUND" else "N/A"
                    if child_status == "FOUND" and _tg_rank(child_tg) < 0:
                        child_tg = "N/A"
                    if p.get("requirement_tag") in ("LOVPAALAGT_AK_IKKE_TG", "LOVLIGHET_OG_SIKKERHET"):
                        child_tg = "N/A"
                        
                    children_for_parent.append({
                        "point_id": child_id,
                        "title": _resolve_child_title(child_id, child_meta),
                        "status": child_status,
                        "deduction_band": _overview_band_from_public_band(point_worst_band.get(child_id, "Ikke scoretrekk")),
                        "deduction_total": child_deduction,
                        "tg": child_tg,
                        "finding_ids": finding_ids_by_point.get(child_id, []),
                        "where": {"page": child_meta.get("page_start", 1)} if child_meta else {}
                    })

            if pid == "P06_ROOMS_BELOW_GRADE" and status != "NOT_FOUND_IN_REPORT":
                found_child_tgs = [
                    str(child.get("tg") or "").upper()
                    for child in children_for_parent
                    if str(child.get("status") or "") == "FOUND"
                ]
                worst_child_tg = max(found_child_tgs, key=_tg_rank, default="")
                if _tg_rank(worst_child_tg) >= 0:
                    tg = worst_child_tg
                if _tg_rank(tg) >= _tg_rank("TG2"):
                    parent_summary = "Avvik funnet"

            points_overview.append({
                "display_index": idx + 1,
                "canonical_id": pid,
                "point_id": None,
                "title": p.get("title_nb") or p.get("label_nb", "Ukjent"),
                "title_display": p.get("title_nb") or p.get("label_nb", "Ukjent"),
                "requirement_tag": p.get("requirement_tag", "IKKE_LOVPAALAGT"),
                "status": status,
                "deduction_band": parent_worst_band[pid] if deduction > 0 else "none",
                "deduction_total": deduction,
                "tg": tg,
                "summary": parent_summary,
                "finding_ids": list(set(parent_finding_ids[pid])),
                "where": parent_where[pid],
                "children": children_for_parent
            })

        # Keep strict canonical parent order (display_order from parent_cards).
        ordering_note = "12-parent roll-up architecture v3.9 (canonical display_order)."
    elif (
        (canonical_cfg := get_canonical_points_v30()) 
        and (canonical_points := canonical_cfg.get("canonical_points") or canonical_cfg.get("points"))
    ):
        mapping_points: List[Dict[str, object]] = []
        if isinstance(points, list):
            mapping_points.extend([dict(p) for p in points if isinstance(p, dict)])
        if isinstance(points_before_whitelist, list):
            seen_ids = {
                str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                for p in mapping_points
                if isinstance(p, dict)
            }
            for p in points_before_whitelist:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or "")
                if pid and pid in seen_ids:
                    continue
                mapping_points.append(dict(p))
        segment_map = _map_segments_to_canonical(mapping_points, canonical_points)
        _emit_canonical_mapping_debug(
            report_id=report_id,
            points=mapping_points,
            canonical_points=canonical_points,
            segment_map=segment_map,
            detected_points_payload=detected_points_payload if isinstance(detected_points_payload, dict) else None,
        )
        points_overview = _build_points_overview_from_canonical(
            canonical_points,
            segment_map,
            deduction_totals,
            finding_ids_by_point,
            point_lookup,
            requirement_tags=canonical_requirement_tags if isinstance(canonical_requirement_tags, dict) else {},
            ui_overlay=ui_overlay_cfg if isinstance(ui_overlay_cfg, dict) else {},
        )
        ordering_note = "Fast liste fra canonical_points (display_order)."
    else:
        skip_parent_same_title = _compute_parent_child_same_title_skips(sorted_points)
        points_overview = []
        display_index = 1
        for point in sorted_points:
            if not isinstance(point, dict):
                continue
            kind = point.get("kind")
            if isinstance(kind, str) and kind not in ("point", "subpoint"):
                continue
            point_id = point.get("point_id") or point.get("numeric_id") or point.get("native_label") or ""
            if point_id in skip_parent_same_title:
                continue
            if _is_non_bygningsdel_point(point):
                continue
            point_key = point.get("point_key") or point_id
            deduction_total = int(deduction_totals.get(point_id, 0))
            fids = finding_ids_by_point.get(point_id, [])
            status = "deduction" if deduction_total > 0 else ("improve" if fids else "ok")
            first_msg = next(
                (x.get("title") or x.get("message", "") for x in all_findings if isinstance(x, dict) and _parse_point_id_from_v16_finding(x) == point_id),
                "",
            )
            summary = first_msg or ("Trekk registrert for punktet." if status == "deduction" else "OK – ingen endringer nødvendig.")
            if status == "ok":
                summary = "OK – ingen endringer nødvendig."
            parent_id = None
            if "." in str(point_id) and len(str(point_id).split(".")) >= 2:
                parent_id = ".".join(str(point_id).split(".")[:-1])
            title_display = point.get("title") or "Ukjent"
            if point.get("instance_label"):
                title_display = f"{title_display} – {point.get('instance_label')}"
            points_overview.append({
                "display_index": display_index,
                "point_id": point_id,
                "point_key": point_key,
                "parent_id": parent_id,
                "native_label": point.get("native_label") or point_id or point_key or "Ukjent",
                "numeric_id": point.get("numeric_id"),
                "native_path": point.get("native_path"),
                "title": point.get("title") or "Ukjent",
                "title_display": title_display,
                "tg": point.get("tg") or "UNKNOWN",
                "status": status,
                "summary": summary,
                "deduction_total": max(deduction_total, 0),
                "finding_ids": fids,
                "where": {"page": int(point.get("page_start") or 1)},
                "legal_status": point.get("legal_status"),
                "required_by_forskrift": point.get("required_by_forskrift", True),
                "ui_badge": point.get("ui_badge"),
                "instance_label": point.get("instance_label"),
            })
            display_index += 1
    feedback_findings, top_score_drivers = _reconcile_feedback_deduction_consistency(
        feedback_findings,
        score_by_category,
        top_score_drivers if isinstance(top_score_drivers, list) else [],
    )
    _force_parent_points_overview_scored_status(
        points_overview,
        [f for f in all_findings if isinstance(f, dict)],
    )
    _ensure_special_feedback_findings_visible(analysis_output, feedback_findings)
    _sanitize_feedback_missing_tiltak_findings(
        feedback_findings,
        {
            _normalize_point_id(str(pid)): _normalize_tg_label(point.get("tg") or point.get("tg_grade"))
            for pid, point in point_lookup.items()
            if isinstance(pid, str) and isinstance(point, dict)
        },
    )
    _polish_feedback_findings(feedback_findings)
    return {
        "version": "v1.1",
        "report_id": str(report_id) if report_id else "unknown_report",
        "document_hash": document_hash or "unknown_hash",
        "ordering": {"mode": mode, "dedupe_key": dedupe_key, "source": "detected_points", "note": ordering_note},
        "score": {
            "total": score_total,
            "now": [
                {"category": item.get("category_id", ""), "deduction": item.get("deduction", 0), "max_deduction": item.get("max_deduction", 0)}
                for item in score_by_category if isinstance(item, dict)
            ],
            "top_drivers": [
                {"rule_id": (d.get("rule_refs") or ["unknown_rule"])[0] or "unknown_rule", "deduction": d.get("deduction_points", 0), "message": d.get("reason") or d.get("title", "Trekkgrunnlag")}
                for d in top_score_drivers if isinstance(d, dict)
            ],
        },
        "gate": {"active": True, "blocked_96": bool(blocked_by), "blocked_by": blocked_by},
        "points_overview": points_overview,
        "findings": feedback_findings,
    }


def _sanitize_feedback_v11_legacy_consequence_unclear(
    payload: Dict[str, object],
    analysis_output: Dict[str, object],
) -> Dict[str, object]:
    if not isinstance(payload, dict):
        return payload
    missing_points = _dommer_b_missing_consequence_points(analysis_output)
    if not missing_points:
        return payload
    removed_ids = set()
    findings = payload.get("findings")
    if isinstance(findings, list):
        kept = []
        for item in findings:
            point_id = _arkat_item_point_id(item) if isinstance(item, dict) else ""
            if _is_legacy_consequence_unclear_item(item) and point_id in missing_points:
                if isinstance(item, dict):
                    finding_id = str(item.get("finding_id") or "").strip()
                    rule_id = str(item.get("rule_id") or "").strip()
                    if finding_id:
                        removed_ids.add(finding_id)
                    if rule_id:
                        removed_ids.add(rule_id)
                continue
            kept.append(item)
        payload["findings"] = kept

    if removed_ids:
        overview = payload.get("points_overview")
        if isinstance(overview, list):
            for point in overview:
                if not isinstance(point, dict):
                    continue
                finding_ids = point.get("finding_ids")
                if isinstance(finding_ids, list):
                    point["finding_ids"] = [item for item in finding_ids if str(item or "") not in removed_ids]
    return payload


_FREMTIND_STYLE_P_CODE_RE = re.compile(r"(?i)(?<![a-z0-9])p\d{2}[a-zæøå]?(?:[_\-.]?[a-z0-9æøå]+)+(?![a-z0-9])")


def _neutralize_bmtf_p_code(value: str, prefix: str = "bmtf") -> str:
    normalized = re.sub(r"[_\-.]+", "-", str(value or "").lower()).strip("-")
    safe_prefix = re.sub(r"[^a-z0-9-]+", "-", str(prefix or "point").lower()).strip("-") or "point"
    if not normalized:
        return f"{safe_prefix}-point"
    if "-" not in normalized:
        normalized = re.sub(r"(?i)^(p\d{2}[a-z]?)(?=[a-z])", r"\1-", normalized, count=1)
    parts = [part for part in normalized.split("-") if part]
    if parts and re.fullmatch(r"p\d{2}[a-z]?", parts[0], flags=re.IGNORECASE):
        parts = parts[1:]
    label = "-".join(parts) or "point"
    return f"{safe_prefix}-{label}"


def _sanitize_bmtf_feedback_v11_p_codes(payload: Dict[str, object], report_text: str) -> Dict[str, object]:
    if not isinstance(payload, dict):
        return payload
    report_blob = _normalize_report_text_for_analysis(report_text or "")[:120000]
    is_fremtind_public_payload = (
        _report_text_suggests_compressed_mixed_format(report_blob)
        or "fremtind" in report_blob.lower()
        or bool(re.search(r"(?i)\bP\d{2}[A-Z]_[A-Z0-9_]+\b", report_blob))
    )
    replacement_prefix = "fremtind" if is_fremtind_public_payload else "bmtf"

    def _clean_value(key: str, value: object) -> object:
        if not isinstance(value, str):
            return value
        return _FREMTIND_STYLE_P_CODE_RE.sub(lambda match: _neutralize_bmtf_p_code(match.group(0), replacement_prefix), value)

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if isinstance(value, str):
                    node[key] = _clean_value(str(key), value)
                else:
                    _walk(value)
        elif isinstance(node, list):
            for idx, item in enumerate(list(node)):
                if isinstance(item, str):
                    node[idx] = _clean_value("", item)
                else:
                    _walk(item)

    _walk(payload)
    return payload


_INTERNAL_CANONICAL_P_CODE_RE = re.compile(r"\bP\d{2}[A-Z]_[A-Z0-9_]+\b")
_INTERNAL_CANONICAL_SLUG_RE = re.compile(r"\b(?:fremtind|bmtf)-[a-z0-9]+(?:-[a-z0-9]+)+\b", re.IGNORECASE)


def _clean_pdf_cid_artifacts(value: str) -> str:
    return re.sub(r"\(cid:\d+\)", "", str(value or "")).replace("  ", " ").strip()


def _point_public_label_lookup(detected_points_payload: Dict[str, object]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    points: List[object] = []
    if isinstance(detected_points_payload, dict):
        for key in ("points", "points_before_whitelist"):
            value = detected_points_payload.get(key)
            if isinstance(value, list):
                points.extend(value)
    for point in points:
        if not isinstance(point, dict):
            continue
        title = _clean_pdf_cid_artifacts(str(point.get("title") or point.get("excerpt") or ""))
        native = _normalize_point_id(str(point.get("native_label") or point.get("numeric_id") or ""))
        label = title or (native if native and not native.startswith("900") else "") or "punktet"
        for key in ("point_id", "canonical_point_id", "point_key", "native_label", "numeric_id"):
            raw = point.get(key)
            if not isinstance(raw, str) or not raw.strip():
                continue
            normalized = _normalize_point_id(raw)
            if normalized and normalized not in lookup:
                lookup[normalized] = label
            raw_clean = raw.strip()
            if raw_clean and raw_clean not in lookup:
                lookup[raw_clean] = label
    return lookup


def _slugify_public_finding_id_part(value: str) -> str:
    text = _clean_pdf_cid_artifacts(value).lower()
    text = re.sub(r"[^a-z0-9æøå]+", "_", text).strip("_")
    return text or "punkt"


def _public_label_for_internal_ref(ref: str, label_lookup: Dict[str, str]) -> str:
    normalized = _normalize_point_id(ref)
    label = label_lookup.get(normalized) or label_lookup.get(str(ref or "").strip()) or ""
    return label or "punktet"


def _sanitize_customer_text_internal_ids(payload: object, detected_points_payload: Dict[str, object]) -> None:
    label_lookup = _point_public_label_lookup(detected_points_payload)
    text_keys = {"message", "title", "what_to_change", "good_example", "bad_example", "summary", "snippet"}
    id_keys = {"point_id", "point_key", "canonical_id"}

    def _clean_text(value: str) -> str:
        text = str(value or "")
        text = _INTERNAL_CANONICAL_P_CODE_RE.sub(lambda m: _public_label_for_internal_ref(m.group(0), label_lookup), text)
        text = _INTERNAL_CANONICAL_SLUG_RE.sub("punktet", text)
        return _clean_pdf_cid_artifacts(text)

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if isinstance(value, str) and str(key) in text_keys:
                    node[key] = _clean_text(value)
                elif isinstance(value, str) and str(key) in id_keys:
                    cleaned = _clean_text(value)
                    candidate = _normalize_point_id(cleaned)
                    if str(key) == "point_id" and re.fullmatch(r"overview-\d{2}(?:-child-\d{2})?", cleaned):
                        node[key] = cleaned
                    elif _is_numeric_point_id(candidate):
                        node[key] = candidate
                    else:
                        node[key] = ""
                elif isinstance(value, (dict, list)):
                    _walk(value)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    _walk(item)

    _walk(payload)


@lru_cache(maxsize=1)
def _governed_rule_deductions() -> Dict[str, int]:
    governed: Dict[str, int] = {}
    try:
        scoring = json.loads(get_scoring_model_text())
    except json.JSONDecodeError:
        scoring = {}
    rule_catalog = scoring.get("rule_catalog") if isinstance(scoring, dict) else None
    if isinstance(rule_catalog, list):
        for item in rule_catalog:
            if not isinstance(item, dict):
                continue
            rid = str(item.get("id") or "").strip()
            if not rid:
                continue
            try:
                governed[rid] = int(item.get("deduction") or 0)
            except (TypeError, ValueError):
                governed[rid] = 0
    return governed


@lru_cache(maxsize=1)
def _arkat_semantic_rule_points() -> Dict[str, int]:
    governed: Dict[str, int] = {}
    try:
        mapping = json.loads(get_arkat_error_deduction_mapping_text())
    except Exception:
        return governed
    deductions = mapping.get("deductions") if isinstance(mapping, dict) else {}
    if not isinstance(deductions, dict):
        return governed
    for field, field_rules in deductions.items():
        if not isinstance(field, str) or not isinstance(field_rules, dict):
            continue
        field_upper = field.upper()
        for error_type, rule_meta in field_rules.items():
            if not isinstance(error_type, str) or not isinstance(rule_meta, dict):
                continue
            try:
                points = int(rule_meta.get("points") or 0)
            except (TypeError, ValueError):
                points = 0
            governed[f"A_ARKAT_SEMANTIC.{field_upper}.{error_type}"] = points
            if error_type == f"MISSING ({field})":
                governed[f"A_ARKAT_SEMANTIC.{field_upper}.MISSING_{field_upper}"] = points
    return governed


def _arkat_semantic_rule_is_governed(rule_id: str) -> bool:
    rid = str(rule_id or "").strip()
    if rid in _arkat_semantic_rule_points():
        return True
    prefix = "A_ARKAT_SEMANTIC."
    if not rid.startswith(prefix):
        return False
    parts = rid.split(".")
    if len(parts) < 3:
        return False
    field = parts[1].strip().lower()
    error_type = parts[2].strip()
    try:
        mapping = json.loads(get_arkat_error_deduction_mapping_text())
    except Exception:
        return False
    deductions = mapping.get("deductions") if isinstance(mapping, dict) else {}
    field_rules = deductions.get(field) if isinstance(deductions, dict) else {}
    if not isinstance(field_rules, dict):
        return False
    if error_type in field_rules:
        return True
    missing_alias = f"MISSING ({field})"
    if error_type.startswith("MISSING_") and missing_alias in field_rules:
        return True
    for candidate_rules in deductions.values() if isinstance(deductions, dict) else []:
        if isinstance(candidate_rules, dict) and error_type in candidate_rules:
            return True
    return False


def _invalid_arkat_field_bindings(findings: List[object]) -> List[Dict[str, str]]:
    try:
        mapping = json.loads(get_arkat_error_deduction_mapping_text())
    except Exception:
        return []
    deductions = mapping.get("deductions") if isinstance(mapping, dict) else {}
    if not isinstance(deductions, dict):
        return []
    invalid: List[Dict[str, str]] = []
    prefix = "A_ARKAT_SEMANTIC."
    for item in findings:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("rule_id") or "").strip()
        if not rid.startswith(prefix):
            continue
        parts = rid.split(".")
        if len(parts) < 3:
            continue
        field = parts[1].strip().lower()
        error_type = parts[2].strip()
        field_rules = deductions.get(field)
        if not isinstance(field_rules, dict):
            invalid.append({"rule_id": rid, "field": field, "error_type": error_type})
            continue
        missing_alias = f"MISSING ({field})"
        valid = error_type in field_rules or (error_type.startswith("MISSING_") and missing_alias in field_rules)
        if not valid:
            invalid.append({"rule_id": rid, "field": field, "error_type": error_type})
    return invalid


def _finding_potential_deduction(item: Dict[str, object]) -> Optional[int]:
    for key in ("potential_deduction", "points", "deduction_points", "deduction"):
        value = item.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    governed = _governed_rule_deductions()
    rule_id = str(item.get("rule_id") or "").strip()
    if rule_id in governed:
        return governed[rule_id]
    arkat_points = _arkat_semantic_rule_points()
    if rule_id in arkat_points:
        return arkat_points[rule_id]
    band = str(item.get("deduction_band") or "").strip()
    return {"Høyt trekk": 5, "Middels trekk": 3, "Lavt trekk": 1, "Ikke scoretrekk": 0}.get(band)


def _public_safe_finding_id(value: str, label_lookup: Dict[str, str]) -> str:
    raw = _clean_pdf_cid_artifacts(str(value or ""))
    if not raw:
        return ""
    text = raw
    for ref, label in sorted(label_lookup.items(), key=lambda item: len(str(item[0])), reverse=True):
        ref_text = str(ref or "")
        if not ref_text or not _INTERNAL_CANONICAL_P_CODE_RE.fullmatch(ref_text):
            continue
        if ref_text in text:
            text = text.replace(ref_text, _slugify_public_finding_id_part(label))
    text = _INTERNAL_CANONICAL_P_CODE_RE.sub(
        lambda m: _slugify_public_finding_id_part(_public_label_for_internal_ref(m.group(0), label_lookup)),
        text,
    )
    text = re.sub(
        r"(?i)(?<![A-Za-z0-9])(?:fremtind|bmtf)-[a-z0-9]+(?:-[a-z0-9]+)+",
        "punkt",
        text,
    )
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_.-")
    return text or "finding"


def _point_id_from_arkat_finding_id(finding_id: str) -> str:
    """Extract canonical point_id encoded in an ARKAT finding_id.

    Pattern: ``A_ARKAT_{POINT_ID}_{FIELD}_{ERROR_TYPE}``
    Returns the point_id segment, e.g. ``P10B_FLOORS`` from
    ``A_ARKAT_P10B_FLOORS_KONSEKVENS_MISSING_KONSEKVENS``, or "" if not matched.
    """
    m = re.match(r"^A_ARKAT_([A-Za-z0-9]+(?:_[A-Za-z0-9]+)*)_(AARSAK|RISIKO|KONSEKVENS|ANBEFALT_TILTAK)_", finding_id or "")
    if m:
        return m.group(1)
    return ""


def _normalize_incomplete_source_finding_ids(
    analysis_output: Dict[str, object],
    payload: Dict[str, object],
    detected_points_payload: Dict[str, object],
) -> None:
    label_lookup = _point_public_label_lookup(detected_points_payload)
    old_to_new: Dict[str, str] = {}
    seen: Dict[str, int] = {}
    all_findings = analysis_output.get("all_findings")
    if isinstance(all_findings, list):
        for idx, item in enumerate(all_findings):
            if not isinstance(item, dict):
                continue
            old_id = str(item.get("finding_id") or item.get("rule_id") or f"finding_{idx + 1}").strip()
            new_id = _public_safe_finding_id(old_id, label_lookup) or f"finding_{idx + 1}"
            count = seen.get(new_id, 0)
            seen[new_id] = count + 1
            if count:
                new_id = f"{new_id}_{count + 1}"
            old_to_new[old_id] = new_id
            item["finding_id"] = new_id
            # Backfill empty point_id from ARKAT finding_id pattern when possible
            if not str(item.get("point_id") or "").strip() and not str(item.get("exact_point_id") or "").strip():
                derived = _point_id_from_arkat_finding_id(old_id)
                if derived:
                    item["point_id"] = derived
                    item["exact_point_id"] = derived

    def _rewrite_item(item: Dict[str, object]) -> None:
        for key in ("finding_id", "source_finding_id"):
            raw = str(item.get(key) or "").strip()
            if raw in old_to_new:
                item[key] = old_to_new[raw]
            elif raw:
                item[key] = _public_safe_finding_id(raw, label_lookup)

    findings = payload.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if isinstance(item, dict):
                _rewrite_item(item)
    overview = payload.get("points_overview")
    if isinstance(overview, list):
        for entry in overview:
            if not isinstance(entry, dict):
                continue
            fids = entry.get("finding_ids")
            if isinstance(fids, list):
                entry["finding_ids"] = [old_to_new.get(str(fid), _public_safe_finding_id(str(fid), label_lookup)) for fid in fids]
            children = entry.get("children")
            if isinstance(children, list):
                for child in children:
                    if not isinstance(child, dict):
                        continue
                    child_fids = child.get("finding_ids")
                    if isinstance(child_fids, list):
                        child["finding_ids"] = [old_to_new.get(str(fid), _public_safe_finding_id(str(fid), label_lookup)) for fid in child_fids]


def _normalize_incomplete_feedback_finding(item: Dict[str, object]) -> None:
    potential = _finding_potential_deduction(item)
    item["preliminary"] = True
    item["verified"] = False
    item["verification_status"] = "unverified_incomplete_analysis"
    item["deduction_valid"] = False
    item["deduction"] = None
    item["potential_deduction"] = int(potential) if potential is not None else 0
    item["deduction_band"] = "Ikke vurdert"
    item["affects_96_gate"] = False
    item.pop("public_visibility", None)
    gate_effect = item.get("gate_effect")
    if isinstance(gate_effect, dict):
        gate_effect["blocks_96_gate"] = False
        gate_effect.pop("caps_total_score_to", None)
    else:
        item["gate_effect"] = {"blocks_96_gate": False}


def _clean_customer_point_title(value: object) -> str:
    text = _clean_pdf_cid_artifacts(str(value or ""))
    text = re.sub(r"\bTG\s*[0-3IU]+\)?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[:\-]+\s*$", "", text).strip(" :;-")
    text = re.sub(r"\s{2,}", " ", text)
    return text or "punktet"


def _arkat_customer_feedback_text(finding: Dict[str, object], point_label: str) -> Tuple[str, str, str, str]:
    rule_id = str(finding.get("rule_id") or "")
    parts = rule_id.split(".")
    field = parts[1].lower() if len(parts) > 2 else ""
    error_type = parts[2] if len(parts) > 2 else ""
    definite_field_label = {
        "aarsak": "årsaksbeskrivelsen",
        "risiko": "risikovurderingen",
        "konsekvens": "konsekvensbeskrivelsen",
        "anbefalt_tiltak": "tiltaksbeskrivelsen",
    }.get(field, "beskrivelsen")
    indefinite_field_label = {
        "aarsak": "årsaksbeskrivelse",
        "risiko": "risikovurdering",
        "konsekvens": "konsekvensbeskrivelse",
        "anbefalt_tiltak": "tiltaksbeskrivelse",
    }.get(field, "beskrivelsen")
    title = f"{point_label}: forbedre {definite_field_label}"
    if error_type.startswith("MISSING"):
        message = f"Punktet mangler en tydelig {indefinite_field_label}."
        change = f"Legg inn en konkret {indefinite_field_label} som er knyttet til avviket i dette punktet."
    elif "PRESENT_STATE_AS_RISIKO" in error_type:
        message = "Teksten beskriver hovedsakelig dagens tilstand, men forklarer ikke tydelig hva som kan utvikle seg videre."
        change = "Beskriv hvilken videre skadeutvikling eller risiko kjøper bør være oppmerksom på."
    elif "LIMITATION_AS_KONSEKVENS" in error_type:
        message = "Teksten forklarer kontroll eller observasjon, men ikke tydelig hvilken praktisk følge avviket har."
        change = "Beskriv hva avviket kan bety for bruk, skadeutvikling, vedlikehold eller behov for videre oppfølging."
    elif "OBSERVATION_AS_AARSAK" in error_type:
        message = "Teksten beskriver observasjonen, men forklarer ikke tydelig hvorfor avviket har oppstått."
        change = "Legg til en kort faglig forklaring på årsaken til avviket."
    elif "CONSEQUENCE_AS_RISIKO" in error_type:
        message = "Teksten beskriver konsekvensen, men bør også forklare hva som kan utvikle seg videre."
        change = "Legg til en kort risikovurdering som beskriver mulig videre utvikling."
    elif "LIMITATION_USED_AS_RISK_SUBSTITUTE" in error_type:
        message = "Teksten beskriver en undersøkelsesbegrensning, men ikke en konkret risiko."
        change = "Forklar hvilken risiko begrensningen eller avviket gir for kjøper."
    else:
        message = f"{definite_field_label.capitalize()} bør gjøres tydeligere for kjøper."
        change = f"Oppdater {definite_field_label} med konkret og punktspesifikk informasjon."
    example = f"{point_label}: Beskriv avviket med konkret årsak, mulig videre utvikling, praktisk betydning og anbefalt oppfølging der dette er relevant."
    return title, message, change, example


def _customer_text_from_source_finding(finding: Dict[str, object], fallback_title: str) -> Tuple[str, str, str, str]:
    point_title = _clean_customer_point_title(finding.get("exact_point_title") or fallback_title)
    point_id = _normalize_point_id(str(finding.get("exact_point_id") or finding.get("point_id") or ""))
    point_label = f"Punkt {point_id} {point_title}".strip() if _is_numeric_point_id(point_id) else point_title
    rule_id = str(finding.get("rule_id") or "")
    if rule_id.startswith("A_ARKAT_SEMANTIC."):
        return _arkat_customer_feedback_text(finding, point_label)
    title = _clean_pdf_cid_artifacts(str(finding.get("title") or fallback_title or "Avvik"))
    message = _clean_pdf_cid_artifacts(str(finding.get("message") or title))
    change = _clean_pdf_cid_artifacts(str(finding.get("recommended_fix_text") or message or "Oppdater punktteksten."))
    example = _clean_pdf_cid_artifacts(str(finding.get("suggested_rewrite_text") or change or message))
    return title, message, change, example


def _feedback_item_from_internal_finding(
    finding: Dict[str, object],
    idx: int,
    detected_points_payload: Dict[str, object],
) -> Dict[str, object]:
    point_id = _arkat_item_point_id(finding)
    label_lookup = _point_public_label_lookup(detected_points_payload)
    public_point = _public_point_reference(point_id, str(finding.get("rule_id") or finding.get("finding_id") or ""))
    if not public_point and point_id:
        public_point = point_id if _is_numeric_point_id(point_id) else ""
    snippet_source = (
        str(finding.get("exact_point_text") or "").strip()
        or next((s for s in finding.get("evidence_snippets") or [] if isinstance(s, str) and s.strip()), "")
        or str(finding.get("message") or finding.get("title") or "")
    )
    title = str(finding.get("title") or finding.get("message") or "Foreløpig funn").strip()
    message = str(finding.get("message") or title).strip()
    if point_id and not public_point:
        label = _public_label_for_internal_ref(point_id, label_lookup)
        title = title.replace(point_id, label)
        message = message.replace(point_id, label)
    source_finding_id = str(finding.get("finding_id") or "").strip()
    customer_title, customer_message, customer_change, customer_example = _customer_text_from_source_finding(finding, title)
    page = 1
    for point in (detected_points_payload.get("points") or []) if isinstance(detected_points_payload, dict) else []:
        if not isinstance(point, dict):
            continue
        candidate_ids = {
            _normalize_point_id(str(point.get("point_id") or "")),
            _normalize_point_id(str(point.get("numeric_id") or "")),
            _normalize_point_id(str(point.get("native_label") or "")),
        }
        if point_id and point_id in candidate_ids:
            page = int(point.get("page_start") or 1)
            break
    item = {
        "finding_id": source_finding_id or f"unknown-{idx}",
        "source_finding_id": source_finding_id,
        "rule_id": str(finding.get("rule_id") or finding.get("finding_id") or f"unknown-{idx}"),
        "rule_family": _derive_rule_family(str(finding.get("rule_id") or finding.get("finding_id") or "")) or "UNKNOWN",
        "category": str(finding.get("category") or _infer_category_from_rule_id(str(finding.get("rule_id") or "")) or ""),
        "severity": str(finding.get("severity") or "medium"),
        "is_regulatory_breach": bool(finding.get("is_regulatory_breach")),
        "point_id": public_point,
        "point_key": public_point or point_id,
        "arkat_section": "annet",
        "title": customer_title,
        "message": customer_message,
        "what_to_change": customer_change,
        "example_fix": {"good_example": customer_example},
        "evidence": {"page": page, "snippet": snippet_source[:500] if snippet_source else "Ikke tilgjengelig.", "match": "From all_findings."},
    }
    _normalize_incomplete_feedback_finding(item)
    return item


def _sync_feedback_pass_through_from_sources(
    payload: Dict[str, object],
    analysis_output: Dict[str, object],
) -> None:
    findings = payload.get("findings")
    all_findings = analysis_output.get("all_findings")
    if not isinstance(findings, list) or not isinstance(all_findings, list):
        return
    source_by_id: Dict[str, Dict[str, object]] = {}
    for source in all_findings:
        if not isinstance(source, dict):
            continue
        for key in ("finding_id", "rule_id"):
            source_id = str(source.get(key) or "").strip()
            if source_id:
                source_by_id.setdefault(source_id, source)
    for item in findings:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_finding_id") or item.get("finding_id") or "").strip()
        source = source_by_id.get(source_id)
        if not isinstance(source, dict):
            continue
        source_rule = str(source.get("rule_id") or source.get("finding_id") or "").strip()
        source_category = str(source.get("category") or _infer_category_from_rule_id(source_rule) or "").strip()
        source_severity = str(source.get("severity") or "").strip()
        potential = _finding_potential_deduction(source)
        item["source_finding_id"] = str(source.get("finding_id") or source_id)
        item["finding_id"] = item["source_finding_id"]
        item["rule_id"] = source_rule
        item["category"] = source_category
        item["severity"] = source_severity
        item["potential_deduction"] = int(potential) if potential is not None else 0
        source_point_id = _normalize_point_id(str(source.get("point_id") or source.get("exact_point_id") or ""))
        if _is_numeric_point_id(source_point_id):
            item["point_id"] = source_point_id
            item["point_key"] = source_point_id
        if item.get("deduction_valid") is False:
            item["deduction"] = None
            item["deduction_band"] = "Ikke vurdert"
        else:
            item["deduction_band"] = str(source.get("deduction_band") or item.get("deduction_band") or "")


def _ensure_incomplete_feedback_traceability(
    payload: Dict[str, object],
    analysis_output: Dict[str, object],
    detected_points_payload: Dict[str, object],
) -> None:
    findings = payload.get("findings")
    if not isinstance(findings, list):
        findings = []
        payload["findings"] = findings
    present_source_ids = {
        str(item.get("source_finding_id") or item.get("rule_id") or item.get("finding_id") or "")
        for item in findings
        if isinstance(item, dict)
    }
    present_rule_ids = {
        str(item.get("rule_id") or "")
        for item in findings
        if isinstance(item, dict)
    }
    present_rule_points = {
        (
            str(item.get("rule_id") or ""),
            _normalize_point_id(str(item.get("point_key") or item.get("point_id") or "")),
        )
        for item in findings
        if isinstance(item, dict)
    }
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return

    def _id_variants(value: str) -> set:
        raw = str(value or "")
        variants = {raw}
        for prefix in ("bmtf", "fremtind", "punkt"):
            variants.add(_FREMTIND_STYLE_P_CODE_RE.sub(lambda match: _neutralize_bmtf_p_code(match.group(0), prefix), raw))
        return {item for item in variants if item}

    for idx, finding in enumerate(all_findings):
        if not isinstance(finding, dict):
            continue
        source_id = str(finding.get("finding_id") or "")
        rule_id = str(finding.get("rule_id") or finding.get("finding_id") or "")
        point_id = _arkat_item_point_id(finding)
        rule_point = (rule_id, point_id)
        if (
            (source_id and bool(_id_variants(source_id) & present_source_ids))
            or (rule_id and rule_id in present_rule_ids and rule_point in present_rule_points)
            or (not source_id and rule_id in present_rule_ids)
        ):
            continue
        item = _feedback_item_from_internal_finding(finding, len(findings), detected_points_payload)
        findings.append(item)
        present_source_ids.add(source_id or item["finding_id"])
        present_rule_ids.add(rule_id)
        present_rule_points.add((rule_id, _normalize_point_id(str(item.get("point_key") or item.get("point_id") or ""))))


def _normalize_incomplete_points_overview(payload: Dict[str, object], detected_points_payload: Dict[str, object]) -> None:
    overview = payload.get("points_overview")
    if not isinstance(overview, list):
        overview = []
        payload["points_overview"] = overview
    if isinstance(detected_points_payload, dict):
        points = detected_points_payload.get("points")
        feedback_findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
        finding_ids_by_point: Dict[str, List[str]] = {}
        for finding in feedback_findings:
            if not isinstance(finding, dict):
                continue
            finding_id = str(finding.get("finding_id") or finding.get("source_finding_id") or finding.get("rule_id") or "").strip()
            point_id = _normalize_point_id(str(finding.get("source_point_id") or finding.get("point_id") or finding.get("point_key") or ""))
            if finding_id and point_id:
                finding_ids_by_point.setdefault(point_id, []).append(finding_id)
        if isinstance(points, list) and points:
            overview.clear()
            for idx, point in enumerate(points):
                if not isinstance(point, dict):
                    continue
                pid = str(point.get("point_id") or point.get("numeric_id") or point.get("native_label") or "")
                norm_pid = _normalize_point_id(pid)
                fids = finding_ids_by_point.get(norm_pid, [])
                overview.append({
                    "display_index": idx + 1,
                    "point_id": _public_point_reference(pid, "") or (norm_pid if _is_numeric_point_id(norm_pid) else f"overview-{idx + 1:02d}"),
                    "point_key": pid,
                    "title": _clean_pdf_cid_artifacts(str(point.get("title") or point.get("excerpt") or "Ukjent")),
                    "tg": point.get("tg") or "UNKNOWN",
                    "status": "incomplete_analysis",
                    "summary": "Ikke fullstendig kontrollert fordi analysen ble avbrutt.",
                    "deduction_total": None,
                    "potential_deduction_total": sum(
                        int(_finding_potential_deduction(finding) or 0)
                        for finding in feedback_findings
                        if isinstance(finding, dict)
                        and str(finding.get("finding_id") or finding.get("source_finding_id") or finding.get("rule_id") or "") in set(fids)
                    ),
                    "deduction_valid": False,
                    "deduction_band": "Ikke vurdert",
                    "finding_ids": fids,
                    "where": {"page": int(point.get("page_start") or 1)},
                    "children": [],
                })
    for entry in overview:
        if not isinstance(entry, dict):
            continue
        display_index = int(entry.get("display_index") or (overview.index(entry) + 1))
        raw_entry_id = _normalize_point_id(str(entry.get("point_id") or entry.get("canonical_id") or entry.get("point_key") or ""))
        if not raw_entry_id or _is_canonical_child_point_id(raw_entry_id):
            entry["point_id"] = f"overview-{display_index:02d}"
        elif _is_numeric_point_id(raw_entry_id):
            entry["point_id"] = raw_entry_id
        entry["status"] = "incomplete_analysis"
        entry["summary"] = "Ikke fullstendig kontrollert fordi analysen ble avbrutt."
        entry["deduction_total"] = None
        entry["potential_deduction_total"] = int(entry.get("potential_deduction_total") or 0)
        entry["deduction_valid"] = False
        entry["deduction_band"] = "Ikke vurdert"
        children = entry.get("children")
        if isinstance(children, list):
            child_tgs = [
                str(child.get("tg") or "").strip().upper()
                for child in children
                if isinstance(child, dict)
                and str(child.get("status") or "").lower() != "not_found_in_report"
                and str(child.get("tg") or "").strip().upper() in {"TG0", "TG1", "TG2", "TG3", "TGIU"}
            ]
            if child_tgs:
                entry["tg"] = max(child_tgs, key=_tg_rank)
            for child_idx, child in enumerate(children, start=1):
                if not isinstance(child, dict):
                    continue
                child["deduction_total"] = None
                child["potential_deduction_total"] = int(child.get("potential_deduction_total") or 0)
                child["deduction_valid"] = False
                child["deduction_band"] = "Ikke vurdert"
                raw_child_id = _normalize_point_id(str(child.get("point_id") or child.get("canonical_id") or child.get("point_key") or ""))
                if not raw_child_id or _is_canonical_child_point_id(raw_child_id):
                    child["point_id"] = f"{entry['point_id']}-child-{child_idx:02d}"
                elif _is_numeric_point_id(raw_child_id):
                    child["point_id"] = raw_child_id
            entry["children"] = []


def _validate_incomplete_policy_invariants(
    analysis_output: Dict[str, object],
    feedback_payload: Dict[str, object],
    detected_points_payload: Dict[str, object],
) -> List[Dict[str, object]]:
    findings = analysis_output.get("all_findings") if isinstance(analysis_output, dict) else []
    feedback_findings = feedback_payload.get("findings") if isinstance(feedback_payload, dict) else []
    detected_points = detected_points_payload.get("points") if isinstance(detected_points_payload, dict) else []
    findings = findings if isinstance(findings, list) else []
    feedback_findings = feedback_findings if isinstance(feedback_findings, list) else []
    detected_points = detected_points if isinstance(detected_points, list) else []

    def _rule_governance_evidence(rule_id: str) -> Dict[str, object]:
        rid = str(rule_id or "").strip()
        if not rid:
            return {}
        if rid in _governed_rule_deductions():
            return {
                "rule_id": rid,
                "defining_file_id": "rag_scoring_model_validert",
                "derivation": "rule_catalog.id",
                "deduction": _governed_rule_deductions().get(rid),
            }
        if _arkat_semantic_rule_is_governed(rid):
            parts = rid.split(".")
            field = parts[1].strip().lower() if len(parts) > 2 else ""
            error_type = parts[2].strip() if len(parts) > 2 else ""
            deduction = _arkat_semantic_rule_points().get(rid)
            return {
                "rule_id": rid,
                "defining_file_id": "arkat_semantic_rules",
                "deduction_file_id": "arkat_error_to_deduction_mapping",
                "derivation": "A_ARKAT_SEMANTIC field/error_type mapping",
                "field": field,
                "error_type": error_type,
                "deduction": deduction,
            }
        return {"rule_id": rid, "defining_file_id": "", "derivation": "not_found"}

    def _customer_text_values(node: object) -> List[str]:
        values: List[str] = []
        if isinstance(node, dict):
            for value in node.values():
                if isinstance(value, str):
                    values.append(value)
                elif isinstance(value, (dict, list)):
                    values.extend(_customer_text_values(value))
        elif isinstance(node, list):
            for item in node:
                values.extend(_customer_text_values(item))
        return values

    def _customer_message_values(node: object) -> List[str]:
        values: List[str] = []
        customer_keys = {
            "title",
            "message",
            "what_to_change",
            "good_example",
            "summary",
            "limited_analysis_warning",
            "inactive_reason",
        }
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and str(key) in customer_keys:
                    values.append(value)
                elif isinstance(value, (dict, list)):
                    values.extend(_customer_message_values(value))
        elif isinstance(node, list):
            for item in node:
                values.extend(_customer_message_values(item))
        return values

    feedback_text_blob = "\n".join(_customer_text_values(feedback_payload))
    customer_message_blob = "\n".join(_customer_message_values(feedback_payload))
    feedback_source_ids = {
        str(item.get("source_finding_id") or item.get("rule_id") or item.get("finding_id") or "")
        for item in feedback_findings
        if isinstance(item, dict)
    }
    def _id_variants(value: str) -> set:
        raw = str(value or "")
        variants = {raw}
        for prefix in ("bmtf", "fremtind", "punkt"):
            variants.add(_FREMTIND_STYLE_P_CODE_RE.sub(lambda match: _neutralize_bmtf_p_code(match.group(0), prefix), raw))
        return {item for item in variants if item}

    missing = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("finding_id") or item.get("rule_id") or "")
        if source_id and not (_id_variants(source_id) & feedback_source_ids):
            rule_id = str(item.get("rule_id") or "")
            if rule_id not in feedback_source_ids:
                missing.append(source_id)
    gate = analysis_output.get("gate") if isinstance(analysis_output.get("gate"), dict) else {}
    active_gate_findings = [
        str(item.get("finding_id") or item.get("rule_id") or "")
        for item in findings
        if isinstance(item, dict) and isinstance(item.get("gate_effect"), dict) and item["gate_effect"].get("blocks_96_gate")
    ]
    duplicate_sections = []
    point_resolution_evidence = []
    section_seen: Dict[tuple, str] = {}
    label_lookup = _point_public_label_lookup(detected_points_payload)
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        title_key = _normalize_tg3_cost_text(str(point.get("title") or point.get("excerpt") or "")).lower()
        page_key = str(point.get("page_start") or "")
        canonical_key = _normalize_point_id(str(point.get("canonical_point_id") or point.get("point_id") or ""))
        if not title_key or not page_key:
            continue
        key = (canonical_key, title_key, page_key)
        point_id = str(point.get("point_id") or point.get("numeric_id") or point.get("native_label") or "")
        point_resolution_evidence.append({
            "point_id": _public_label_for_internal_ref(point_id, label_lookup),
            "internal_point_id_present": bool(_normalize_point_id(point_id)),
            "title": _clean_pdf_cid_artifacts(str(point.get("title") or "")),
            "page": page_key,
            "tg": str(point.get("tg") or point.get("tg_grade") or ""),
            "section_key": "|".join(str(part) for part in (_public_label_for_internal_ref(canonical_key or point_id, label_lookup), title_key, page_key)),
        })
        previous = section_seen.get(key)
        if previous and previous != point_id:
            duplicate_sections.append({
                "first": _public_label_for_internal_ref(previous, label_lookup),
                "duplicate": _public_label_for_internal_ref(point_id, label_lookup),
                "title": _clean_pdf_cid_artifacts(str(point.get("title") or "")),
                "page": page_key,
            })
        else:
            section_seen[key] = point_id
    governed = _governed_rule_deductions()
    ungoverned = []
    governed_rule_evidence: Dict[str, Dict[str, object]] = {}
    fired_rule_items: List[Dict[str, object]] = []
    fired_rule_items.extend(item for item in findings if isinstance(item, dict))
    fired_rule_items.extend(item for item in feedback_findings if isinstance(item, dict))
    for item in fired_rule_items:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("rule_id") or "").strip()
        if not rid:
            continue
        governed_rule_evidence[rid] = _rule_governance_evidence(rid)
        if rid not in governed and not _arkat_semantic_rule_is_governed(rid):
            ungoverned.append(rid)
    invalid_field_bindings = _invalid_arkat_field_bindings(findings)
    definite_deductions = [
        str(item.get("finding_id") or item.get("rule_id") or "")
        for item in feedback_findings
        if isinstance(item, dict) and item.get("deduction_valid") is False and item.get("deduction") is not None
    ]
    invalid_bands = [
        str(row.get("deduction_band"))
        for row in (analysis_output.get("category_breakdown") or [])
        if isinstance(row, dict) and str(row.get("deduction_band") or "") in {"Ikke scoretrekk", "Ingen scoretrekk"}
    ]
    manifest = (
        feedback_payload.get("runtime_manifest")
        or analysis_output.get("runtime_manifest")
        or (analysis_output.get("meta") or {}).get("runtime_manifest")
    )
    policy_invariant_ids: List[str] = []
    try:
        policy_path = Path(__file__).resolve().parents[3] / "files" / "validert_incomplete_analysis_policy_v1_5.json"
        policy_payload = json.loads(policy_path.read_text(encoding="utf-8"))
        policy_rows = ((policy_payload.get("regression_invariants") or {}).get("invariants") or [])
        if isinstance(policy_rows, list):
            policy_invariant_ids = [
                str(row.get("id") or "")
                for row in policy_rows
                if isinstance(row, dict) and str(row.get("id") or "")
            ]
    except Exception:
        policy_invariant_ids = []
    internal_id_leaks = bool(_INTERNAL_CANONICAL_P_CODE_RE.search(feedback_text_blob) or _INTERNAL_CANONICAL_SLUG_RE.search(feedback_text_blob))
    overview = feedback_payload.get("points_overview") if isinstance(feedback_payload.get("points_overview"), list) else []
    overview_missing_ids = [
        str(entry.get("title") or entry.get("display_index") or "")
        for entry in overview
        if isinstance(entry, dict) and not str(entry.get("point_id") or "").strip()
    ]
    overview_child_missing_ids: List[str] = []
    for entry in overview:
        if not isinstance(entry, dict):
            continue
        children = entry.get("children")
        if not isinstance(children, list):
            continue
        for child in children:
            if isinstance(child, dict) and not str(child.get("point_id") or "").strip():
                overview_child_missing_ids.append(str(child.get("title") or entry.get("title") or "child"))

    source_by_id = {
        str(item.get("finding_id") or ""): item
        for item in findings
        if isinstance(item, dict) and str(item.get("finding_id") or "").strip()
    }
    mapping_mismatch = []
    passthrough_mismatch = []
    evidence_page_mismatch = []
    for item in feedback_findings:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_finding_id") or "").strip()
        if not source_id:
            continue
        source = source_by_id.get(source_id)
        if not isinstance(source, dict):
            continue
        feedback_rule = str(item.get("rule_id") or "").strip()
        source_rule = str(source.get("rule_id") or source.get("finding_id") or "").strip()
        feedback_category = str(item.get("category") or "").strip().upper()
        source_category = str(source.get("category") or _infer_category_from_rule_id(source_rule) or "").strip().upper()
        feedback_severity = str(item.get("severity") or "").strip()
        source_severity = str(source.get("severity") or "").strip()
        feedback_potential = _finding_potential_deduction(item)
        source_potential = _finding_potential_deduction(source)
        feedback_point_id = _normalize_point_id(str(item.get("point_id") or ""))
        source_point_id = _normalize_point_id(str(source.get("point_id") or source.get("exact_point_id") or ""))
        expected_page = None
        for point in detected_points:
            if not isinstance(point, dict):
                continue
            point_ids = {
                _normalize_point_id(str(point.get("point_id") or "")),
                _normalize_point_id(str(point.get("numeric_id") or "")),
                _normalize_point_id(str(point.get("native_label") or "")),
            }
            if source_point_id and source_point_id in point_ids:
                expected_page = int(point.get("page_start") or 1)
                break
        feedback_evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        feedback_page = feedback_evidence.get("page") if isinstance(feedback_evidence, dict) else None
        if feedback_rule != source_rule or (feedback_category and source_category and feedback_category != source_category):
            mapping_mismatch.append({
                "source_finding_id": source_id,
                "feedback_rule_id": feedback_rule,
                "source_rule_id": source_rule,
                "feedback_category": feedback_category,
                "source_category": source_category,
            })
        if feedback_severity != source_severity or feedback_potential != source_potential or (
            _is_numeric_point_id(source_point_id) and feedback_point_id != source_point_id
        ):
            passthrough_mismatch.append({
                "source_finding_id": source_id,
                "feedback_severity": feedback_severity,
                "source_severity": source_severity,
                "feedback_potential_deduction": feedback_potential,
                "source_potential_deduction": source_potential,
                "feedback_point_id": feedback_point_id,
                "source_point_id": source_point_id,
            })
        if expected_page is not None and int(feedback_page or 0) != int(expected_page):
            evidence_page_mismatch.append({
                "source_finding_id": source_id,
                "feedback_page": feedback_page,
                "expected_page": expected_page,
                "point_id": source_point_id,
            })

    customer_jargon_hits = sorted(
        set(
            match.group(0)
            for match in re.finditer(
                r"(?i)\b(?:WRONG|MISSING|aarsak|anbefalt_tiltak)\b",
                customer_message_blob,
            )
        )
    )
    missing_internal_point_ids = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        if _is_report_level_rule(str(item.get("rule_id") or item.get("finding_id") or "")):
            continue
        point_id = _normalize_point_id(str(item.get("exact_point_id") or item.get("point_id") or ""))
        if not point_id:
            missing_internal_point_ids.append(str(item.get("finding_id") or item.get("rule_id") or "unknown"))
    for point in detected_points:
        if isinstance(point, dict) and not _normalize_point_id(str(point.get("point_id") or "")):
            missing_internal_point_ids.append(str(point.get("title") or point.get("excerpt") or "detected_point"))

    pipeline_meta = analysis_output.get("arkat_semantic_pipeline") if isinstance(analysis_output.get("arkat_semantic_pipeline"), dict) else {}
    format_basis = pipeline_meta.get("classification_basis") if isinstance(pipeline_meta.get("classification_basis"), list) else []
    report_format = str(pipeline_meta.get("report_format") or "")
    extraction_method = str(pipeline_meta.get("extraction_method_used") or "")
    fremtind_point_outputs = [
        str(point.get("point_id") or "")
        for point in detected_points
        if isinstance(point, dict)
        and (
            _is_canonical_child_point_id(str(point.get("point_id") or ""))
            or str(point.get("tg_source") or "") == "fremtind_summary"
        )
    ]
    non_fremtind_format = any("befar" in str(item).lower() for item in format_basis) or report_format in {"semi_structured", "unlabeled_prose"}
    format_binding_ok = bool(report_format and extraction_method and format_basis) and not (non_fremtind_format and fremtind_point_outputs)
    source_primary_conclusions = (
        analysis_output.get("source_primary_tg_conclusions")
        if isinstance(analysis_output.get("source_primary_tg_conclusions"), list)
        else []
    )
    evaluated_ids = {
        _normalize_point_id(str(point.get("point_id") or ""))
        for point in (pipeline_meta.get("points") or [])
        if isinstance(point, dict)
    } if isinstance(pipeline_meta.get("points"), list) else set()
    detected_by_id: Dict[str, List[Dict[str, object]]] = {}
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        point_id = _normalize_point_id(str(point.get("point_id") or point.get("numeric_id") or point.get("native_label") or ""))
        if point_id:
            detected_by_id.setdefault(point_id, []).append(point)
    inv13_evidence = []
    inv13_violations = []
    for source in source_primary_conclusions:
        if not isinstance(source, dict):
            continue
        point_id = _normalize_point_id(str(source.get("point_id") or ""))
        source_tg = _normalize_tg_label(source.get("tg") or "")
        matches = [
            point for point in detected_by_id.get(point_id, [])
            if _normalize_tg_label(point.get("tg") or point.get("tg_grade") or "") == source_tg
        ]
        evidence = {
            "source_point_id": point_id,
            "source_title": source.get("title"),
            "source_tg": source_tg,
            "source_page": source.get("page"),
            "source_span_hash": source.get("span_hash"),
            "resolved_detected_count": len(matches),
            "resolved_point_ids": [_normalize_point_id(str(point.get("point_id") or "")) for point in matches],
            "evaluated": point_id in evaluated_ids,
        }
        inv13_evidence.append(evidence)
        if len(matches) != 1 or point_id not in evaluated_ids:
            inv13_violations.append(evidence)
    expected_tg_points_from_source = [
        _normalize_point_id(str(item.get("point_id") or ""))
        for item in source_primary_conclusions
        if isinstance(item, dict) and _normalize_point_id(str(item.get("point_id") or ""))
    ]
    pipeline_expected = [
        _normalize_point_id(str(item or ""))
        for item in (pipeline_meta.get("expected_tg_points") or [])
        if _normalize_point_id(str(item or ""))
    ] if isinstance(pipeline_meta.get("expected_tg_points"), list) else []
    inv13_expected_mismatch = sorted(set(expected_tg_points_from_source) - set(pipeline_expected))
    inv13_ok = (
        bool(source_primary_conclusions)
        and not inv13_violations
        and not inv13_expected_mismatch
    ) if non_fremtind_format else True
    single_id_mismatch = []
    for item in feedback_findings:
        if not isinstance(item, dict):
            continue
        finding_id = str(item.get("finding_id") or "")
        source_id = str(item.get("source_finding_id") or "")
        if not source_id or source_id not in source_by_id or finding_id != source_id:
            single_id_mismatch.append({
                "finding_id": finding_id,
                "source_finding_id": source_id,
                "source_exists": source_id in source_by_id,
            })
    inv14_evidence = []
    inv14_violations = []
    method_text_re = re.compile(r"(?i)\b(?:Hvordan kontrollen er utført|Formålet er å avdekke|Det vurderes også forhold)\b")
    pipeline_points = pipeline_meta.get("points") if isinstance(pipeline_meta.get("points"), list) else []
    for point in pipeline_points:
        if not isinstance(point, dict):
            continue
        binding = point.get("arkat_field_binding_evidence") if isinstance(point.get("arkat_field_binding_evidence"), dict) else {}
        if not binding:
            continue
        fields = point.get("extracted_fields") if isinstance(point.get("extracted_fields"), dict) else {}
        row = {"point_id": point.get("point_id"), "title": point.get("title"), "fields": {}}
        for field_name, evidence_rows in binding.items():
            value = str(fields.get(field_name) or "")
            present = not _is_semantically_missing_text(_normalize_tg3_cost_text, value)
            has_method_text = bool(method_text_re.search(value))
            row["fields"][field_name] = {
                "present": present,
                "has_method_text": has_method_text,
                "binding": [
                    {
                        "subsection_heading": ev.get("subsection_heading"),
                        "offset": ev.get("offset"),
                        "length_chars": ev.get("length_chars"),
                        "preview": ev.get("preview"),
                    }
                    for ev in evidence_rows
                    if isinstance(ev, dict)
                ][:10],
            }
            if not present or has_method_text:
                inv14_violations.append({"point_id": point.get("point_id"), "field": field_name, "present": present, "has_method_text": has_method_text})
        inv14_evidence.append(row)
    inv14_ok = bool(inv14_evidence) and not inv14_violations

    meta = analysis_output.get("meta") if isinstance(analysis_output.get("meta"), dict) else {}
    pipeline_detection = pipeline_meta.get("ns_version_detection") if isinstance(pipeline_meta.get("ns_version_detection"), dict) else {}
    meta_report_date = str(meta.get("report_date") or analysis_output.get("report_date") or "").strip()
    pipeline_report_date = str(pipeline_meta.get("report_date") or "").strip()
    meta_ns_version = str(meta.get("ns_version") or meta.get("ns_standard_version") or analysis_output.get("ns_version") or "").strip()
    pipeline_ns_version = str(pipeline_meta.get("ns_version") or "").strip()

    def _normalize_ns_version(v: str) -> str:
        """Normalise NS version strings for comparison: 'NS 3600:2025' == 'NS3600:2025'."""
        return v.upper().replace(" ", "")

    point_ns_mismatch = [
        {
            "point_id": point.get("point_id"),
            "point_ns_version": point.get("ns_version"),
            "pipeline_ns_version": pipeline_ns_version,
        }
        for point in pipeline_points
        if isinstance(point, dict)
        and pipeline_ns_version
        and _normalize_ns_version(str(point.get("ns_version") or "").strip()) != _normalize_ns_version(pipeline_ns_version)
    ]
    inv16_violations = []
    if not meta_report_date or not pipeline_report_date or meta_report_date != pipeline_report_date:
        inv16_violations.append({
            "type": "report_date_mismatch",
            "meta_report_date": meta_report_date,
            "pipeline_report_date": pipeline_report_date,
        })
    if not meta_ns_version or not pipeline_ns_version or _normalize_ns_version(meta_ns_version) != _normalize_ns_version(pipeline_ns_version):
        inv16_violations.append({
            "type": "ns_version_mismatch",
            "meta_ns_version": meta_ns_version,
            "pipeline_ns_version": pipeline_ns_version,
        })
    detection_source = str(pipeline_detection.get("source") or "")
    detection_detail = str(pipeline_detection.get("detail") or "")
    if detection_source != "report_text" or detection_detail not in {"ns3600_2018", "ns3600_2025"}:
        inv16_violations.append({
            "type": "detection_source_not_report_text",
            "source": detection_source,
            "detail": detection_detail,
        })
    inv16_violations.extend(point_ns_mismatch[:20])
    inv16_ok = not inv16_violations

    overview_finding_assignments = []
    feedback_finding_point_by_id: Dict[str, str] = {}
    for finding in feedback_findings:
        if not isinstance(finding, dict):
            continue
        fid = str(finding.get("finding_id") or finding.get("source_finding_id") or finding.get("rule_id") or "").strip()
        point_id = _normalize_point_id(str(finding.get("source_point_id") or finding.get("point_id") or finding.get("point_key") or ""))
        if fid and point_id:
            feedback_finding_point_by_id[fid] = point_id
    for entry in overview:
        if not isinstance(entry, dict):
            continue
        for fid in entry.get("finding_ids") or []:
            overview_finding_assignments.append({
                "overview_point_id": entry.get("point_id"),
                "title": entry.get("title"),
                "finding_id": fid,
                "expected_point_id": feedback_finding_point_by_id.get(str(fid) or ""),
            })
        for child in entry.get("children") or []:
            if not isinstance(child, dict):
                continue
            for fid in child.get("finding_ids") or []:
                overview_finding_assignments.append({
                    "overview_point_id": child.get("point_id"),
                    "title": child.get("title"),
                    "finding_id": fid,
                    "expected_point_id": feedback_finding_point_by_id.get(str(fid) or ""),
                })
    inv17_mismatches = [
        row for row in overview_finding_assignments
        if _normalize_point_id(str(row.get("overview_point_id") or "")) != _normalize_point_id(str(row.get("expected_point_id") or ""))
    ]
    inv17_ok = not inv17_mismatches

    executed_invariant_ids = [
        "INV-01_finding_traceability",
        "INV-02_points_overview_completeness",
        "INV-03_gate_score_coherence",
        "INV-04_no_ungoverned_rules",
        "INV-05_taxonomy_field_binding",
        "INV-06_no_internal_id_leakage",
        "INV-07_no_duplicate_physical_points",
        "INV-08_preliminary_consistency",
        "INV-09_score_band_consistency",
        "INV-10_manifest_presence",
        "INV-11_single_id_scheme",
        "INV-12_format_extraction_binding",
        "INV-13_source_anchored_tg_coverage",
        "INV-14_arkat_subsection_binding",
        "INV-15_customer_message_language",
        "INV-16_regime_consistency",
        "INV-17_overview_finding_assignment",
    ]
    runner_set_matches_policy = bool(policy_invariant_ids) and executed_invariant_ids == policy_invariant_ids

    checks = [
        ("INV-01_finding_traceability", not missing and not mapping_mismatch and not passthrough_mismatch and not evidence_page_mismatch, {"missing": missing[:20], "missing_count": len(missing), "mapping_mismatch": mapping_mismatch[:20], "passthrough_mismatch": passthrough_mismatch[:20], "evidence_page_mismatch": evidence_page_mismatch[:20]}),
        ("INV-02_points_overview_completeness", (bool(overview) or not detected_points) and not overview_missing_ids and not overview_child_missing_ids, {"points_overview_count": len(overview), "detected_points_count": len(detected_points), "overview_missing_ids": overview_missing_ids[:20], "overview_child_missing_ids": overview_child_missing_ids[:20]}),
        ("INV-03_gate_score_coherence", not bool(gate.get("active")) and not bool(gate.get("blocked_96")) and not active_gate_findings, {"active_gate_findings": active_gate_findings[:20]}),
        ("INV-04_no_ungoverned_rules", not ungoverned and all(item.get("defining_file_id") for item in governed_rule_evidence.values()), {"ungoverned_rule_ids": sorted(set(ungoverned))[:20], "governed_rule_evidence": [governed_rule_evidence[key] for key in sorted(governed_rule_evidence.keys())]}),
        ("INV-05_taxonomy_field_binding", not invalid_field_bindings, {"invalid_bindings": invalid_field_bindings[:20], "invalid_count": len(invalid_field_bindings)}),
        ("INV-06_no_internal_id_leakage", not internal_id_leaks and not missing_internal_point_ids, {"missing_internal_point_ids": missing_internal_point_ids[:20]}),
        ("INV-07_no_duplicate_physical_points", not duplicate_sections, {"duplicates": duplicate_sections[:20], "duplicate_count": len(duplicate_sections), "point_resolution_evidence": point_resolution_evidence[:80]}),
        ("INV-08_preliminary_consistency", not definite_deductions and all(isinstance(item, dict) and item.get("preliminary") is True and item.get("verification_status") == "unverified_incomplete_analysis" for item in feedback_findings), {"definite_deductions": definite_deductions[:20]}),
        ("INV-09_score_band_consistency", analysis_output.get("score_total") is None and not invalid_bands, {"invalid_bands": invalid_bands}),
        ("INV-10_manifest_presence", isinstance(manifest, dict) and bool(manifest.get("loaded")) and runner_set_matches_policy, {"executed_invariant_ids": executed_invariant_ids, "policy_invariant_ids": policy_invariant_ids, "runner_set_matches_policy": runner_set_matches_policy}),
        ("INV-11_single_id_scheme", not single_id_mismatch, {"single_id_mismatch": single_id_mismatch[:20]}),
        ("INV-12_format_extraction_binding", format_binding_ok, {"report_format": report_format, "extraction_method_used": extraction_method, "classification_basis": format_basis, "fremtind_point_outputs": fremtind_point_outputs[:20]}),
        ("INV-13_source_anchored_tg_coverage", inv13_ok, {"expected_tg_points_from_source": expected_tg_points_from_source, "pipeline_expected_tg_points": pipeline_expected, "missing_from_pipeline_expected": inv13_expected_mismatch, "violations": inv13_violations[:20], "primary_tg_evidence": inv13_evidence[:80], "summary_duplicate_markers_linked": []}),
        ("INV-14_arkat_subsection_binding", inv14_ok, {"violations": inv14_violations[:20], "field_binding_evidence": inv14_evidence[:80]}),
        ("INV-15_customer_message_language", not customer_jargon_hits, {"customer_jargon_hits": customer_jargon_hits, "checked_field_classes": ["title", "message", "what_to_change", "example_fix.good_example", "summary"]}),
        ("INV-16_regime_consistency", inv16_ok, {"violations": inv16_violations[:20], "meta_report_date": meta_report_date, "pipeline_report_date": pipeline_report_date, "meta_ns_version": meta_ns_version, "pipeline_ns_version": pipeline_ns_version, "ns_version_detection": pipeline_detection, "point_ns_versions": sorted({str(point.get("ns_version") or "") for point in pipeline_points if isinstance(point, dict)})}),
        ("INV-17_overview_finding_assignment", inv17_ok, {"strategy": "source-point finding_ids in incomplete points_overview", "overview_finding_assignments": overview_finding_assignments[:20], "mismatches": inv17_mismatches[:20]}),
    ]
    return [
        {"id": check_id, "passed": bool(passed), "details": details}
        for check_id, passed, details in checks
    ]


def _apply_incomplete_feedback_policy(
    payload: Dict[str, object],
    analysis_output: Dict[str, object],
    detected_points_payload: Dict[str, object],
) -> None:
    reason = str(analysis_output.get("incomplete_reason") or _incomplete_fallback_reason(analysis_output) or "incomplete_full_analyzer")
    warning = str(analysis_output.get("limited_analysis_warning") or "Analysen kunne ikke fullføres.")
    runtime_manifest = get_runtime_manifest(str(analysis_output.get("analysis_mode") or "local_postprocess_dommer_b_fallback"))
    payload["analysis_mode"] = str(runtime_manifest.get("analysis_mode") or "local_postprocess_dommer_b_fallback")
    payload["analysis_complete"] = False
    payload["score_valid"] = False
    payload["ui_status"] = "incomplete_analysis"
    payload["incomplete_reason"] = reason
    payload["limited_analysis_warning"] = warning
    payload["runtime_manifest"] = runtime_manifest
    gate = payload.get("gate")
    if not isinstance(gate, dict):
        gate = {}
        payload["gate"] = gate
    gate["active"] = False
    gate["blocked_96"] = False
    gate["blocked_by"] = []
    gate["message"] = "96-gate er ikke vurdert fordi analysen ikke ble fullført."
    gate["inactive_reason"] = reason
    _normalize_incomplete_source_finding_ids(analysis_output, payload, detected_points_payload)
    _ensure_incomplete_feedback_traceability(payload, analysis_output, detected_points_payload)
    findings = payload.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_finding_id") or "").strip()
            if not source_id:
                source_id = str(item.get("finding_id") or item.get("rule_id") or "").strip()
                if source_id:
                    item["source_finding_id"] = source_id
            if source_id:
                item["finding_id"] = source_id
            elif not str(item.get("finding_id") or "").strip():
                item["finding_id"] = str(item.get("rule_id") or "")
            _normalize_incomplete_feedback_finding(item)
        _sync_feedback_pass_through_from_sources(payload, analysis_output)
    _normalize_incomplete_points_overview(payload, detected_points_payload)
    score = payload.get("score")
    if isinstance(score, dict):
        score["total"] = None
        score["score_valid"] = False
        for key in ("category_deductions", "now"):
            rows = score.get(key)
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        row["deduction"] = None
                        row["deduction_valid"] = False
        score["top_drivers"] = []
    _remove_dead_public_visibility_fields(analysis_output)
    _remove_dead_public_visibility_fields(payload)
    _sanitize_customer_text_internal_ids(payload, detected_points_payload)
    payload.pop("policy_invariants", None)
    analysis_output.pop("policy_invariants", None)
    invariants = _validate_incomplete_policy_invariants(analysis_output, payload, detected_points_payload)
    # policy_invariants is QA/diagnostic data - kept in analysis_output only, NOT in customer-facing feedback_v11
    analysis_output["policy_invariants"] = invariants


def sanitize_bmtf_public_point_taxonomy_payload(payload: Dict[str, object], report_text: str) -> Dict[str, object]:
    """Remove public traces of Fremtind canonical point taxonomy from BMTF/unlabeled payloads."""
    return _sanitize_bmtf_feedback_v11_p_codes(payload, report_text)


def build_feedback_v11(
    analysis_output: Dict[str, object],
    detected_points_payload: Dict[str, object],
    report_id: Optional[str],
    document_hash: Optional[str],
    report_text: str = "",
) -> Dict[str, object]:
    payload = _build_feedback_v11(analysis_output, detected_points_payload, report_id, document_hash, report_text)
    if isinstance(payload, dict) and _incomplete_fallback_reason(analysis_output):
        _apply_incomplete_feedback_policy(payload, analysis_output, detected_points_payload)
    payload = _sanitize_feedback_v11_legacy_consequence_unclear(payload, analysis_output)
    payload = _sanitize_bmtf_feedback_v11_p_codes(payload, report_text)
    if isinstance(payload, dict) and _incomplete_fallback_reason(analysis_output):
        _apply_incomplete_feedback_policy(payload, analysis_output, detected_points_payload)
    return payload


def _build_evidence_for_component(
    component_id: str,
    component_title: str,
    tg: Optional[str],
    pages: List[Dict[str, str]],
) -> Dict[str, object]:
    search_terms = [term for term in [component_id, component_title] if term]
    for page in pages:
        page_text = page["text"]
        lower_text = page_text.lower()
        for term in search_terms:
            idx = lower_text.find(term.lower())
            if idx != -1:
                snippet = _extract_snippet(page_text, idx)
                source = "SUMMARY" if any(marker in lower_text for marker in SUMMARY_MARKERS) else "LOCAL"
                return {
                    "point_id": component_id or "",
                    "tg": tg or "",
                    "page": page["page"],
                    "heading": component_title or "",
                    "source": source,
                    "snippet": snippet,
                    "match_explain": f"Matched '{term}' on page {page['page']}.",
                }

    fallback_page = pages[0]["page"] if pages else 1
    fallback_text = pages[0]["text"] if pages else ""
    return {
        "point_id": component_id or "",
        "tg": tg or "",
        "page": fallback_page,
        "heading": component_title or "",
        "source": "LOCAL",
        "snippet": _extract_snippet(fallback_text, 0),
        "match_explain": "Fallback: no direct match for component_id/title in page text.",
    }


def _ensure_issue_evidence(analysis_output: Dict[str, object], report_text: str) -> None:
    pages = _split_pages(report_text)
    findings = analysis_output.get("findings", [])
    for component in findings:
        component_id = component.get("component_id", "")
        component_title = component.get("component_title", "")
        tg = component.get("tg")
        evidence_seed = _build_evidence_for_component(component_id, component_title, tg, pages)
        for issue in component.get("issues", []):
            evidence = issue.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                issue["evidence"] = [evidence_seed]
                continue
            normalized = _normalize_evidence_items(evidence)
            if normalized:
                issue["evidence"] = [_merge_evidence_defaults(item, evidence_seed) for item in normalized]
            else:
                issue["evidence"] = [evidence_seed]


def _ensure_driver_evidence(analysis_output: Dict[str, object]) -> None:
    findings = analysis_output.get("findings", [])
    issue_evidence_by_rule: Dict[str, List[Dict[str, object]]] = {}
    for component in findings:
        for issue in component.get("issues", []):
            for rule_id in issue.get("rule_refs", []):
                issue_evidence_by_rule.setdefault(rule_id, []).extend(issue.get("evidence", []))

    for driver in analysis_output.get("top_score_drivers", []):
        evidence = driver.get("evidence")
        if isinstance(evidence, list) and evidence:
            normalized = _normalize_evidence_items(evidence)
            if normalized:
                driver["evidence"] = normalized
                continue
        for rule_id in driver.get("rule_refs", []):
            candidate = issue_evidence_by_rule.get(rule_id)
            if candidate:
                normalized = _normalize_evidence_items(candidate)
                driver["evidence"] = normalized or [candidate[0]]
                break
        if not driver.get("evidence"):
            for candidates in issue_evidence_by_rule.values():
                if candidates:
                    normalized = _normalize_evidence_items(candidates)
                    driver["evidence"] = normalized or [candidates[0]]
                    break
        if not driver.get("evidence"):
            driver["evidence"] = [
                {
                    "point_id": "",
                    "tg": "",
                    "page": 1,
                    "heading": "",
                    "source": "LOCAL",
                    "snippet": "",
                    "match_explain": "Fallback: no evidence available from issues.",
                }
            ]


def _normalize_evidence_items(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    required_keys = {"point_id", "tg", "page", "heading", "source", "snippet", "match_explain"}
    normalized: List[Dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = dict(item)
        if not candidate.get("snippet") and candidate.get("text"):
            candidate["snippet"] = candidate.get("text")
        candidate.setdefault("point_id", "")
        candidate.setdefault("tg", "")
        candidate.setdefault("heading", "")
        candidate.setdefault("source", "LOCAL")
        candidate.setdefault("match_explain", "Derived from evidence text.")
        candidate.setdefault("page", 1)
        if required_keys.issubset(candidate.keys()) and candidate.get("snippet") and candidate.get("page"):
            normalized.append(candidate)
    return normalized


def _merge_evidence_defaults(item: Dict[str, object], defaults: Dict[str, object]) -> Dict[str, object]:
    merged = dict(item)
    for key in ("point_id", "tg", "heading", "source", "page", "match_explain", "snippet"):
        if not merged.get(key):
            merged[key] = defaults.get(key, "")
    return merged


def _ensure_meta_fields(
    analysis_output: Dict[str, object],
    document_title: Optional[str],
    document_id: Optional[str],
) -> None:
    meta = analysis_output.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        analysis_output["meta"] = meta
    meta.setdefault("schema_version", "1.4")
    meta.setdefault("analysis_timestamp_utc", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    meta.setdefault("document_title", document_title or "")
    if document_id:
        meta.setdefault("document_id", document_id)


def _ensure_required_arrays(analysis_output: Dict[str, object]) -> None:
    analysis_output.setdefault("score_total", 0)
    analysis_output.setdefault("score_band", "")
    analysis_output.setdefault("score_by_category", [])
    analysis_output.setdefault("top_score_drivers", [])
    analysis_output.setdefault("findings", [])
    analysis_output.setdefault("improvements", [])
    analysis_output.setdefault("disclaimers", [])


@lru_cache(maxsize=1)
def _load_category_config() -> Dict[str, object]:
    try:
        return json.loads(get_category_config_text())
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=1)
def _load_legality_rules() -> Dict[str, object]:
    try:
        return json.loads(get_legality_rules_text())
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=1)
def _load_legality_templates() -> Dict[str, object]:
    try:
        return json.loads(get_legality_arkat_templates_text())
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=1)
def _load_legality_rule_map() -> Dict[str, object]:
    try:
        return json.loads(get_legality_arkat_map_text())
    except json.JSONDecodeError:
        return {}


def _get_legality_rule_meta() -> Dict[str, Dict[str, object]]:
    rules_payload = _load_legality_rules()
    rules = rules_payload.get("rules", []) if isinstance(rules_payload, dict) else []
    meta: Dict[str, Dict[str, object]] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rule_id = rule.get("id")
        if not rule_id:
            continue
        gate_impact = rule.get("gate_impact", {}) if isinstance(rule.get("gate_impact"), dict) else {}
        if not gate_impact and isinstance(rule.get("gate_effect"), dict):
            gate_impact = rule.get("gate_effect", {})
        score_impact = rule.get("score_impact", {}) if isinstance(rule.get("score_impact"), dict) else {}
        meta[str(rule_id)] = {
            "blocks_96_gate": bool(gate_impact.get("blocks_96_gate")),
            "max_total_score": score_impact.get("max_total_score"),
            "category": rule.get("category"),
            "severity": rule.get("severity"),
        }
    return meta


def _build_legality_arkat_example(rule_id: str) -> Optional[str]:
    rule_map = _load_legality_rule_map()
    templates_payload = _load_legality_templates()
    template_key = None
    if isinstance(rule_map, dict):
        template_key = (rule_map.get("rule_to_template") or {}).get(rule_id)
    templates = templates_payload.get("templates", {}) if isinstance(templates_payload, dict) else {}
    template = templates.get(template_key) if template_key else None
    if not isinstance(template, dict):
        return None
    arkat = template.get("arkat", {}) if isinstance(template.get("arkat"), dict) else {}
    labels = {
        "arsak": "Årsak",
        "risiko": "Risiko",
        "konsekvens": "Konsekvens",
        "anbefalt_tiltak": "Anbefalt tiltak",
    }
    lines = []
    for key in ("arsak", "risiko", "konsekvens", "anbefalt_tiltak"):
        text = arkat.get(key)
        if isinstance(text, str) and text.strip():
            lines.append(f"{labels[key]}: {text.strip()}")
    if len(lines) != 4:
        return None
    return "\n".join(lines)


def _load_scoring_model() -> Dict[str, object]:
    try:
        payload = json.loads(get_scoring_model_text())
    except json.JSONDecodeError:
        payload = {}
    categories = payload.get("categories", [])
    category_order = [c.get("id") for c in categories if c.get("id")]
    if not category_order:
        category_order = ["A", "B", "C", "D", "E"]
    category_names = {c.get("id"): c.get("name", "") for c in categories if c.get("id")}
    mechanics = payload.get("scoring_mechanics", {}) if isinstance(payload.get("scoring_mechanics"), dict) else {}
    category_caps = mechanics.get("category_caps") or {c.get("id"): c.get("max_deduction", 0) for c in categories if c.get("id")}
    deduct_per_occurrence = mechanics.get("deduct_per_occurrence", True)
    aggregate_level = mechanics.get("aggregate_level", "bygningsdel")
    score_start = mechanics.get("score_start", payload.get("score_start", 100))
    score_floor = mechanics.get("score_floor", 0)
    score_ceiling = mechanics.get("score_ceiling", 100)
    category_config = _load_category_config()
    config_categories = category_config.get("categories", {}) if isinstance(category_config, dict) else {}
    if isinstance(config_categories, dict):
        for category_id, info in config_categories.items():
            if category_id not in category_order:
                category_order.append(category_id)
            if category_id not in category_names and isinstance(info, dict):
                category_names[category_id] = info.get("title", "")
            if category_id not in category_caps and isinstance(info, dict):
                category_caps[category_id] = info.get("max_deduction", 0)

    return {
        "category_order": category_order,
        "category_names": category_names,
        "category_caps": category_caps,
        "deduct_per_occurrence": deduct_per_occurrence,
        "aggregate_level": aggregate_level,
        "score_start": score_start,
        "score_floor": score_floor,
        "score_ceiling": score_ceiling,
    }


def _infer_category_from_rule_id(rule_id: str) -> str:
    if not rule_id or "_" not in rule_id:
        return ""
    prefix = rule_id.split("_", 1)[0].upper()
    return prefix if prefix in {"A", "B", "C", "D", "E", "F"} else ""


def _ensure_score_by_category(score_by_category: List[Dict[str, object]]) -> List[Dict[str, object]]:
    scoring_model = _load_scoring_model()
    category_order = scoring_model["category_order"]
    category_names = scoring_model["category_names"]
    category_caps = scoring_model["category_caps"]
    existing_map = {
        item.get("category_id"): item
        for item in score_by_category
        if isinstance(item, dict) and item.get("category_id")
    }
    normalized = []
    for category_id in category_order:
        existing = existing_map.get(category_id, {})
        max_deduction = existing.get("max_deduction", category_caps.get(category_id, 0))
        normalized.append(
            {
                "category_id": category_id,
                "category_name": existing.get("category_name", category_names.get(category_id, "")),
                "deduction": int(existing.get("deduction", 0) or 0),
                "max_deduction": int(max_deduction) if isinstance(max_deduction, (int, float)) else 0,
            }
        )
    return normalized


def _apply_legality_score_cap(analysis_output: Dict[str, object], score_total: int) -> int:
    legality_meta = _get_legality_rule_meta()
    if not legality_meta:
        return score_total
    triggered_rule_ids = set()
    for component in analysis_output.get("findings", []):
        if not isinstance(component, dict):
            continue
        for issue in component.get("issues", []):
            if not isinstance(issue, dict):
                continue
            for rule_id in issue.get("rule_refs", []) or []:
                if isinstance(rule_id, str):
                    triggered_rule_ids.add(rule_id)
        for deduction in component.get("deductions", []):
            if isinstance(deduction, dict):
                rule_id = deduction.get("rule_id")
                if isinstance(rule_id, str):
                    triggered_rule_ids.add(rule_id)
    caps = []
    cap_entries = []
    for rule_id in triggered_rule_ids:
        cap = legality_meta.get(rule_id, {}).get("max_total_score")
        if isinstance(cap, (int, float)):
            cap_value = int(cap)
            caps.append(cap_value)
            cap_entries.append({"rule_id": rule_id, "max_total_score": cap_value})
    if not caps:
        return score_total
    min_cap = min(caps)
    capped_score = min(score_total, min_cap)
    if capped_score < score_total:
        score_caps = analysis_output.get("score_caps")
        if not isinstance(score_caps, list):
            score_caps = []
            analysis_output["score_caps"] = score_caps
        capped_rules = [entry["rule_id"] for entry in cap_entries if entry["max_total_score"] == min_cap]
        score_caps.append(
            {
                "rule_ids": capped_rules,
                "max_total_score": min_cap,
                "original_score": score_total,
                "capped_score": capped_score,
                "points_deducted": score_total - capped_score,
            }
        )
    return capped_score


def _attach_score_reconciliation(analysis_output: Dict[str, object]) -> None:
    """
    Make score math auditable for UI:
    score_total = score_start - (sum(category deductions) + other deductions)

    "Other deductions" covers score caps/gates or legacy adjustments that are not attributable
    to one A–F category row.
    """
    if not isinstance(analysis_output, dict):
        return
    score_total = analysis_output.get("score_total")
    if not isinstance(score_total, (int, float)):
        return
    score_start = 100
    deduction_total = max(0, int(score_start - int(score_total)))

    score_by_category = analysis_output.get("score_by_category") or []
    if not isinstance(score_by_category, list):
        score_by_category = []
    category_deduction_total = 0
    for row in score_by_category:
        if isinstance(row, dict):
            try:
                category_deduction_total += int(row.get("deduction") or 0)
            except (TypeError, ValueError):
                continue

    other_deduction_total = max(0, deduction_total - category_deduction_total)
    other_reasons: List[Dict[str, object]] = []

    score_caps = analysis_output.get("score_caps")
    if isinstance(score_caps, list):
        for cap in score_caps:
            if not isinstance(cap, dict):
                continue
            pts = cap.get("points_deducted")
            if isinstance(pts, (int, float)) and pts > 0:
                other_reasons.append(
                    {
                        "reason": "Score cap applied",
                        "points": int(pts),
                        "meta": {
                            "max_total_score": cap.get("max_total_score"),
                            "rule_ids": cap.get("rule_ids"),
                        },
                    }
                )

    gate = analysis_output.get("gate")
    if isinstance(gate, dict) and gate.get("blocked_96"):
        max_if = gate.get("max_score_if_blocked")
        if isinstance(max_if, (int, float)):
            # This is not necessarily equal to other_deduction_total; keep as an explanation hint.
            other_reasons.append({"reason": "96-gate active (score limited)", "points": max(0, int(score_start - int(max_if)))})

    analysis_output["score_reconciliation"] = {
        "score_start": score_start,
        "score_total": int(score_total),
        "deduction_total": deduction_total,
        "category_deduction_total": category_deduction_total,
        "other_deduction_total": other_deduction_total,
        "other_deductions": other_reasons,
        "reconciles": (category_deduction_total + other_deduction_total) == deduction_total,
    }


def _normalize_scoring_output(analysis_output: Dict[str, object]) -> Dict[str, object]:
    # If the LLM explicitly marks the analysis as incomplete, do not manufacture a 100-score.
    meta = analysis_output.get("meta")
    if isinstance(meta, dict):
        analysis_complete = meta.get("analysis_complete")
        status = str(meta.get("status", "")).upper() if meta.get("status") is not None else ""
        if analysis_complete is False or (status and status != "OK"):
            # Preserve meta + disclaimers, but force score to unknown/zero and return early.
            gate = analysis_output.get("gate") or {}
            if not isinstance(gate, dict):
                gate = {}
            gate.setdefault("active", True)
            gate.setdefault("blocked_96", False)
            gate.setdefault("max_score_if_blocked", None)
            gate["blocked_by_count"] = int(gate.get("blocked_by_count") or 0)
            gate["message"] = gate.get("message") or meta.get("status_message") or "Analyse ikke fullført."
            analysis_output["gate"] = gate
            analysis_output["score_total"] = 0
            analysis_output["score_band"] = "Ukjent"
            # Ensure score_by_category is structurally present with zero deductions
            analysis_output["score_by_category"] = _ensure_score_by_category(analysis_output.get("score_by_category", []))
            return analysis_output

    scoring_model = _load_scoring_model()
    category_caps = scoring_model.get("category_caps", {})
    category_names = scoring_model.get("category_names", {})
    category_order = scoring_model.get("category_order", ["A", "B", "C", "D", "E"])
    score_start = int(scoring_model.get("score_start", 100))
    score_floor = int(scoring_model.get("score_floor", 0))
    score_ceiling = int(scoring_model.get("score_ceiling", 100))
    deduct_per_occurrence = scoring_model.get("deduct_per_occurrence", True)
    aggregate_level = str(scoring_model.get("aggregate_level", "bygningsdel")).lower()
    score_by_category = analysis_output.get("score_by_category", [])

    seen_keys = set()
    category_totals: Dict[str, int] = {cat: 0 for cat in category_order}
    has_deductions = False

    for component in analysis_output.get("findings", []):
        component_id = component.get("component_id", "")
        deductions = component.get("deductions", [])
        if not isinstance(deductions, list):
            deductions = []
        deduped = []
        for deduction in deductions:
            if not isinstance(deduction, dict):
                continue
            rule_id = deduction.get("rule_id", "")
            if rule_id:
                if not deduct_per_occurrence:
                    unique_key = f"report::{rule_id}"
                elif aggregate_level in {"report", "global", "document", "rule"}:
                    unique_key = f"report::{rule_id}"
                elif aggregate_level in {"issue", "evidence"}:
                    evidence_hash = _hash_evidence_span(deduction.get("evidence"))
                    unique_key = f"issue::{rule_id}::{evidence_hash or component_id}"
                else:
                    unique_key = f"component::{component_id or 'unknown'}::{rule_id}"
                if unique_key in seen_keys:
                    continue
                seen_keys.add(unique_key)
            category_id = deduction.get("category_id") or _infer_category_from_rule_id(rule_id)
            if category_id:
                deduction["category_id"] = category_id
            deduped.append(deduction)
        component["deductions"] = deduped
        for deduction in deduped:
            category_id = deduction.get("category_id") or _infer_category_from_rule_id(deduction.get("rule_id", ""))
            if not category_id:
                continue
            has_deductions = True
            points = deduction.get("points", 0)
            try:
                points_value = int(points)
            except (TypeError, ValueError):
                points_value = 0
            category_totals[category_id] = category_totals.get(category_id, 0) + points_value

    has_component_deductions = has_deductions
    supplemental_rows: List[Dict[str, object]] = []
    _supplement_with_scored_legal_findings(analysis_output, supplemental_rows, category_totals)
    if supplemental_rows:
        has_deductions = True
        if not has_component_deductions:
            _, fallback_category_totals = _collect_scored_all_findings_deductions(analysis_output)
            for category_id, fallback_total in fallback_category_totals.items():
                category_totals[category_id] = max(
                    int(category_totals.get(category_id, 0) or 0),
                    int(fallback_total or 0),
                )
            for row in score_by_category if isinstance(score_by_category, list) else []:
                if not isinstance(row, dict):
                    continue
                category_id = str(row.get("category_id") or row.get("category") or "").strip().upper()
                if not category_id:
                    continue
                try:
                    existing_deduction = int(row.get("deduction") or 0)
                except (TypeError, ValueError):
                    existing_deduction = 0
                if existing_deduction > 0:
                    category_totals[category_id] = max(
                        int(category_totals.get(category_id, 0) or 0),
                        existing_deduction,
                    )

    if not has_deductions:
        fallback_rows, fallback_category_totals = _collect_scored_all_findings_deductions(analysis_output)
        if fallback_rows:
            capped_totals: Dict[str, int] = {}
            for category_id in category_order:
                total = int(fallback_category_totals.get(category_id, 0) or 0)
                cap = category_caps.get(category_id)
                if isinstance(cap, (int, float)):
                    capped_totals[category_id] = min(total, int(cap))
                else:
                    capped_totals[category_id] = total
            analysis_output["score_by_category"] = [
                {
                    "category_id": category_id,
                    "category_name": category_names.get(category_id, ""),
                    "deduction": int(capped_totals.get(category_id, 0)),
                    "max_deduction": int(category_caps.get(category_id, 0) or 0),
                }
                for category_id in category_order
            ]
            score_total = analysis_output.get("score_total")
            if isinstance(score_total, (int, float)) and score_total > 0:
                analysis_output["score_total"] = _apply_legality_score_cap(analysis_output, int(score_total))
            else:
                total_deduction = sum(capped_totals.values())
                analysis_output["score_total"] = _apply_legality_score_cap(
                    analysis_output,
                    int(max(score_floor, min(score_ceiling, score_start - total_deduction))),
                )
            return analysis_output

        analysis_output["score_by_category"] = _ensure_score_by_category(score_by_category)
        score_total = analysis_output.get("score_total")
        if isinstance(score_total, (int, float)) and score_total > 0:
            analysis_output["score_total"] = _apply_legality_score_cap(analysis_output, int(score_total))
        else:
            # v1.6 payload: LLM sends trygghetsscore but score_total 0 – use trygghetsscore and gate
            score_total = int(max(score_floor, min(score_ceiling, score_start)))
            trygghetsscore = analysis_output.get("trygghetsscore")
            if isinstance(trygghetsscore, (int, float)) and 0 <= trygghetsscore <= score_ceiling:
                score_total = min(score_total, int(trygghetsscore))
            gate = analysis_output.get("gate")
            if isinstance(gate, dict) and gate.get("blocked_96"):
                max_if = gate.get("max_score_if_blocked")
                if isinstance(max_if, (int, float)):
                    score_total = min(score_total, int(max_if))
            analysis_output["score_total"] = _apply_legality_score_cap(analysis_output, score_total)
            # Derive score_by_category from category_breakdown so API/storage have non-zero deductions
            total_deduction = max(0, score_start - score_total)
            breakdown = analysis_output.get("category_breakdown")
            if total_deduction > 0 and isinstance(breakdown, list) and breakdown:
                band_weight = {"Høyt trekk": 3, "Middels trekk": 2, "Lavt trekk": 1, "Ikke scoretrekk": 0}
                category_band: Dict[str, str] = {}
                for item in breakdown:
                    if isinstance(item, dict):
                        cat = item.get("category")
                        band = item.get("deduction_band")
                        if cat and band:
                            category_band[str(cat).strip()] = str(band).strip()
                weight_sum = sum(band_weight.get(category_band.get(cid), 0) for cid in category_order)
                if weight_sum > 0:
                    by_cat = []
                    for category_id in category_order:
                        w = band_weight.get(category_band.get(category_id), 0)
                        raw = int(round(total_deduction * w / weight_sum)) if weight_sum else 0
                        cap = category_caps.get(category_id)
                        max_d = int(cap) if isinstance(cap, (int, float)) else 0
                        deduction = min(raw, max_d) if max_d else raw
                        by_cat.append({
                            "category_id": category_id,
                            "category_name": category_names.get(category_id, ""),
                            "deduction": deduction,
                            "max_deduction": max_d,
                        })
                    analysis_output["score_by_category"] = by_cat
        return analysis_output

    capped_totals: Dict[str, int] = {}
    for category_id, total in category_totals.items():
        cap = category_caps.get(category_id)
        if isinstance(cap, (int, float)):
            capped_totals[category_id] = min(int(total), int(cap))
        else:
            capped_totals[category_id] = int(total)

    score_by_category = []
    for category_id in category_order:
        max_deduction = category_caps.get(category_id, 0)
        score_by_category.append(
            {
                "category_id": category_id,
                "category_name": category_names.get(category_id, ""),
                "deduction": int(capped_totals.get(category_id, 0)),
                "max_deduction": int(max_deduction) if isinstance(max_deduction, (int, float)) else 0,
            }
        )
    analysis_output["score_by_category"] = score_by_category

    total_deduction = sum(capped_totals.values())
    score_start = int(scoring_model.get("score_start", 100))
    score_floor = int(scoring_model.get("score_floor", 0))
    score_ceiling = int(scoring_model.get("score_ceiling", 100))
    score_total = int(max(score_floor, min(score_ceiling, score_start - total_deduction)))
    analysis_output["score_total"] = _apply_legality_score_cap(analysis_output, score_total)
    _attach_score_reconciliation(analysis_output)
    return analysis_output


def normalize_scoring_output(analysis_output: Dict[str, object]) -> Dict[str, object]:
    normalized = _normalize_scoring_output(analysis_output)
    _sanitize_analysis_output_text(normalized)
    return normalized


_NON_NORWEGIAN_CHARS = re.compile(
    r"[^A-Za-z0-9 \t\n\r\.,;:!?\-–—/()\[\]{}\"'«»ÆØÅæøå%+*=<>|_#@€$§]"
)


def _sanitize_analysis_output_text(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                _sanitize_analysis_output_text(value)
            elif isinstance(value, str):
                payload[key] = _NON_NORWEGIAN_CHARS.sub("", value)
    elif isinstance(payload, list):
        for idx, item in enumerate(payload):
            if isinstance(item, (dict, list)):
                _sanitize_analysis_output_text(item)
            elif isinstance(item, str):
                payload[idx] = _NON_NORWEGIAN_CHARS.sub("", item)


def ensure_analysis_evidence(analysis_output: Dict[str, object], report_text: str) -> None:
    _ensure_issue_evidence(analysis_output, report_text)
    _ensure_driver_evidence(analysis_output)
    _sanitize_analysis_output_text(analysis_output)


def _incomplete_fallback_reason(analysis_output: Dict[str, object]) -> str:
    if not isinstance(analysis_output, dict):
        return ""
    meta = analysis_output.get("meta") if isinstance(analysis_output.get("meta"), dict) else {}
    mode = str(meta.get("analysis_mode") or analysis_output.get("analysis_mode") or "").strip()
    if mode != "local_postprocess_dommer_b_fallback":
        return ""
    reasons = meta.get("incomplete_full_analyzer_reasons") or analysis_output.get("incomplete_full_analyzer_reasons") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    if not isinstance(reasons, list):
        reasons = []
    cleaned = [str(reason or "").strip() for reason in reasons if str(reason or "").strip()]
    return cleaned[0] if cleaned else "incomplete_full_analyzer"


def _mark_incomplete_fallback_output(analysis_output: Dict[str, object]) -> None:
    reason = _incomplete_fallback_reason(analysis_output)
    if not reason:
        return
    meta = analysis_output.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        analysis_output["meta"] = meta
    warning = (
        "Analysen kunne ikke fullføres fordi rapporten er for stor eller for kompleks for nåværende "
        "analysegrense. Resultatet under er kun en begrenset/foreløpig kontroll og kan ikke brukes "
        "som fullstendig vurdering av rapporten."
    )
    runtime_manifest = get_runtime_manifest("local_postprocess_dommer_b_fallback")
    for target in (analysis_output, meta):
        target["analysis_mode"] = "local_postprocess_dommer_b_fallback"
        target["analysis_complete"] = False
        target["score_valid"] = False
        target["ui_status"] = "incomplete_analysis"
        target["incomplete_reason"] = reason
        target["limited_analysis_warning"] = warning
        target["runtime_manifest"] = runtime_manifest
    analysis_output["score_total"] = None
    analysis_output["trygghetsscore"] = None
    analysis_output["score_band"] = "Ukjent"
    preliminary_note = "Preliminært funn: ikke bekreftet fordi full analyse ble avbrutt."
    for key in ("all_findings", "top_issues", "top_score_drivers", "score_drivers", "feedback_findings"):
        items = analysis_output.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            item["preliminary"] = True
            item["verified"] = False
            item["verification_status"] = "unverified_incomplete_analysis"
            item["preliminary_reason"] = reason
            item["deduction_valid"] = False
            potential = _finding_potential_deduction(item)
            item["deduction"] = None
            if potential is not None:
                item["potential_deduction"] = potential
            gate_effect = item.get("gate_effect")
            has_gate = isinstance(gate_effect, dict) and bool(gate_effect.get("blocks_96_gate"))
            has_regulatory = bool(item.get("is_regulatory_breach"))
            if has_gate or has_regulatory:
                item["regulatory_breach_status"] = "preliminary_unverified"
                item["gate_effect"] = {"blocks_96_gate": False}
                msg = str(item.get("message") or "").strip()
                if preliminary_note not in msg:
                    item["message"] = f"{msg} {preliminary_note}".strip()
    analysis_output["gate"] = {
        "active": False,
        "blocked_96": False,
        "blocked_by": [],
        "blocked_by_count": 0,
        "max_score_if_blocked": None,
        "message": "96-gate er ikke vurdert fordi analysen ikke ble fullført.",
        "inactive_reason": reason,
    }
    score_rows = analysis_output.get("score_by_category")
    if isinstance(score_rows, list):
        for row in score_rows:
            if not isinstance(row, dict):
                continue
            row["deduction"] = None
            row["deduction_valid"] = False
    for key in ("top_score_drivers", "score_drivers"):
        if isinstance(analysis_output.get(key), list):
            analysis_output[key] = []
    incomplete_summary = "Ikke fullstendig kontrollert fordi analysen ble avbrutt."
    breakdown = analysis_output.get("category_breakdown")
    if isinstance(breakdown, list):
        for entry in breakdown:
            if not isinstance(entry, dict):
                continue
            entry["summary"] = incomplete_summary
            entry["deduction"] = None
            entry["deduction_band"] = "Ikke vurdert"


def postprocess_analysis_output(
    analysis_output: Dict[str, object],
    report_text: str,
    report_date_override: str = "",
) -> Dict[str, object]:
    """
    Single source of truth for post-LLM validation/normalization.
    Applies cost false-positive filtering and per-segment ARK/ARKAT checks on merged text.
    """
    if not isinstance(analysis_output, dict):
        return analysis_output
    _ensure_required_arrays(analysis_output)
    meta = analysis_output.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        analysis_output["meta"] = meta
    regime_context = _extract_report_regime_context(report_text or "")
    if report_date_override:
        regime_context["report_date"] = report_date_override
        regime_context["report_regime"] = _detect_report_regime(report_date_override, regime_context.get("ns_version") or "")
    if regime_context.get("report_date"):
        meta["report_date"] = regime_context["report_date"]
    ns_standard_version = _detect_ns_standard_version(report_text or "")
    if ns_standard_version:
        meta["ns_standard_version"] = ns_standard_version
    if regime_context.get("ns_version"):
        meta["ns_version"] = regime_context["ns_version"]
    if regime_context.get("report_regime"):
        meta["report_regime"] = regime_context["report_regime"]
    raw_detected_points = _extract_detected_points(report_text or "")
    validated_detected_points = _validate_detected_points_against_whitelist(raw_detected_points)
    use_validated_canonical_scoring = _report_text_suggests_fremtind_template(report_text or "")
    # Critical: ARKAT scoring must cover all extracted numeric points, not only the
    # whitelist-classified subset used for canonical/UI mapping.
    scoring_points = []
    scoring_point_ids = set()

    def _append_scoring_point(point: Dict[str, object], normalized_point_id: str) -> None:
        if not normalized_point_id or normalized_point_id in scoring_point_ids:
            return
        point_for_scoring = dict(point)
        if _is_canonical_child_point_id(normalized_point_id):
            point_for_scoring["point_id"] = normalized_point_id
            point_for_scoring["native_label"] = normalized_point_id
            point_for_scoring["numeric_id"] = ""
            # Canonical child IDs are scoring targets, not synthetic fillers.
            point_for_scoring["synthetic_supplement"] = False
        scoring_points.append(point_for_scoring)
        scoring_point_ids.add(normalized_point_id)

    for point in raw_detected_points:
        if not isinstance(point, dict):
            continue
        normalized_point_id = _normalize_point_id(
            str(
                point.get("canonical_point_id")
                or point.get("point_id")
                or point.get("numeric_id")
                or point.get("native_label")
                or ""
            )
        )
        if _is_scoring_eligible_point_id(normalized_point_id) or _is_canonical_child_point_id(normalized_point_id):
            _append_scoring_point(point, normalized_point_id)
    if use_validated_canonical_scoring:
        for point in validated_detected_points or []:
            if not isinstance(point, dict):
                continue
            normalized_point_id = _normalize_point_id(
                str(
                    point.get("canonical_point_id")
                    or point.get("point_id")
                    or point.get("numeric_id")
                    or point.get("native_label")
                    or ""
                )
            )
            if _is_scoring_eligible_point_id(normalized_point_id) or _is_canonical_child_point_id(normalized_point_id):
                _append_scoring_point(point, normalized_point_id)
        summary_tg_points = _extract_compressed_mixed_summary_tg_points(report_text or "")
        if summary_tg_points:
            scoring_points = [
                point for point in scoring_points
                if not _is_canonical_child_point_id(
                    _normalize_point_id(
                        str(
                            point.get("canonical_point_id")
                            or point.get("point_id")
                            or point.get("numeric_id")
                            or point.get("native_label")
                            or ""
                        )
                    )
                )
            ]
            scoring_point_ids = {
                _normalize_point_id(
                    str(
                        point.get("canonical_point_id")
                        or point.get("point_id")
                        or point.get("numeric_id")
                        or point.get("native_label")
                        or ""
                    )
                )
                for point in scoring_points
                if isinstance(point, dict)
            }
            for summary_point in summary_tg_points:
                normalized_point_id = _normalize_point_id(str(summary_point.get("point_id") or ""))
                if normalized_point_id:
                    _append_scoring_point(summary_point, normalized_point_id)
            meta["compressed_mixed_summary_tg_points_count"] = len(summary_tg_points)
    # Global fallback mode: if this report yielded scoring points but no explicit TG at all,
    # infer a conservative TG from point text to avoid zero point-level semantic evaluation.
    if use_validated_canonical_scoring and scoring_points and not any(_effective_point_tg(p, report_text) in {"TG2", "TG3", "TGIU"} for p in scoring_points if isinstance(p, dict)):
        inferred_count = 0
        for point in scoring_points:
            if not isinstance(point, dict):
                continue
            if _effective_point_tg(point, report_text) in {"TG2", "TG3", "TGIU"}:
                continue
            combined_text = str(
                point.get("effective_span_text")
                or point.get("exact_span_text")
                or point.get("span_text")
                or point.get("excerpt")
                or point.get("title")
                or ""
            )
            linked_summary_text = str(point.get("linked_summary_text") or "").strip()
            if linked_summary_text:
                combined_text = f"{linked_summary_text}\n{combined_text}".strip()
            inferred_tg = _fallback_infer_tg_from_point_text(combined_text, _normalize_report_text_for_analysis)
            if inferred_tg in {"TG2", "TG3", "TGIU"}:
                point["tg"] = inferred_tg
                point["tg_inferred_fallback"] = True
                inferred_count += 1
        if inferred_count:
            logger.info("Fallback TG inference assigned TG for %s scoring points", inferred_count)
    detected_points = _merge_detected_points_with_linked_summary(scoring_points, report_text or "")
    detected_points = _dedupe_detected_physical_points(detected_points)
    detected_points = _normalize_runtime_scoring_signals(detected_points)
    _hydrate_compressed_mixed_p_style_spans(report_text or "", detected_points)
    detected_points = _dedupe_detected_physical_points(_normalize_runtime_scoring_signals(detected_points))
    detected_points = _preserve_report_point_ids_for_non_fremtind(detected_points, report_text or "")
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        if _effective_point_tg(point, report_text) in {"TG2", "TG3", "TGIU"}:
            continue
        combined_text = str(
            point.get("effective_span_text")
            or point.get("exact_span_text")
            or point.get("span_text")
            or point.get("excerpt")
            or point.get("title")
            or ""
        )
        linked_summary_text = str(point.get("linked_summary_text") or "").strip()
        if linked_summary_text:
            combined_text = f"{linked_summary_text}\n{combined_text}".strip()
        if use_validated_canonical_scoring or str(point.get("linked_summary_text") or "").strip():
            inferred_tg = _fallback_infer_tg_from_point_text(combined_text, _normalize_report_text_for_analysis)
            if inferred_tg in {"TG2", "TG3", "TGIU"}:
                point["tg"] = inferred_tg
                point["tg_inferred_fallback"] = True
    detected_points = _apply_regime_to_detected_points(report_text or "", detected_points)
    meta["runtime_points_raw_count"] = len(raw_detected_points)
    meta["runtime_points_whitelist_count"] = len(validated_detected_points or [])
    meta["runtime_points_scoring_count"] = len(detected_points)
    analysis_output["source_primary_tg_conclusions"] = [
        {
            "point_id": point.get("point_id"),
            "title": point.get("title"),
            "tg": point.get("tg"),
            "page": point.get("page_start"),
            "span_hash": point.get("span_hash"),
            "source_tg_marker": point.get("source_tg_marker"),
        }
        for point in detected_points
        if isinstance(point, dict) and point.get("source_primary_tg_conclusion")
    ]
    _run_client_arkat_semantic_pipeline(
        report_text,
        detected_points,
        analysis_output,
        str(regime_context.get("report_date") or ""),
    )
    _attach_exact_point_sources_to_findings(analysis_output, detected_points)
    _filter_tg3_cost_missing_false_positives(report_text, analysis_output, detected_points)
    _drop_tg_and_consequence_false_positives(report_text, analysis_output, detected_points)
    _filter_regime_conditioned_rules(report_text, analysis_output, detected_points)
    _drop_no_tg_hms_as_regular_tg_findings(analysis_output, detected_points)
    _drop_false_electrical_tg_forbidden_findings(analysis_output, detected_points)
    _ensure_issue_evidence(analysis_output, report_text)
    _ensure_driver_evidence(analysis_output)
    _normalize_scoring_output(analysis_output)
    _run_ark_arkat_per_segment_validation(report_text, detected_points, analysis_output)
    _ensure_semantic_tg3_cost_backstop(report_text, analysis_output)
    _drop_arkat_false_positives(analysis_output)
    _drop_good_enough_content_false_positives(report_text, analysis_output, detected_points)
    _drop_segment_arkat_for_tg2_only_points(analysis_output)
    _drop_tg2_tiltak_requirement_false_positives(analysis_output)
    _soften_no_tg_hms_findings(report_text, analysis_output, detected_points)
    _ensure_electrical_no_tg_hms_findings(analysis_output, detected_points)
    _ensure_generic_backstop_findings(report_text, analysis_output, detected_points)
    _drop_age_only_false_positives(report_text, analysis_output, detected_points)
    _drop_unexpected_jargon_findings(analysis_output)
    _ensure_non_buyer_oriented_consequence_findings(detected_points, analysis_output, report_text or "")
    _normalize_non_buyer_oriented_consequence_findings(analysis_output)
    _drop_buyer_only_consequence_public_claims(analysis_output)
    _ensure_finding_suggestions_differentiated(analysis_output)
    _normalize_report_level_finding_targets(analysis_output)
    _ensure_writing_help_fields(analysis_output)
    _dedupe_all_findings_duplicate_safe(analysis_output)
    _force_required_public_findings(report_text, analysis_output)
    _normalize_legal_finding_labels(analysis_output)
    _drop_report_level_false_positives(report_text, analysis_output)
    _drop_known_client_false_positives(report_text, analysis_output)
    _drop_false_freestanding_garage_findings(analysis_output)
    _dedupe_all_findings_duplicate_safe(analysis_output)
    _drop_missing_tiltak_when_raw_action_present(analysis_output)
    _drop_duplicate_missing_tiltak_findings(analysis_output)
    _drop_missing_tiltak_when_raw_action_present(analysis_output)
    _drop_tg3_missing_tiltak_false_positives_from_point_text(analysis_output, detected_points, report_text or "")
    _drop_overlapping_consequence_missing_findings(analysis_output)
    _drop_missing_claims_when_semantic_field_correct(analysis_output)
    _drop_legacy_consequence_unclear_when_semantic_missing(analysis_output)
    _finalize_dommer_b_canonical_output(analysis_output)
    _drop_tg3_cost_top_issues_if_segments_have_cost(analysis_output) 
    _ensure_tg3_missing_cost_compliance_from_segments(analysis_output)
    _drop_false_electrical_tg_forbidden_findings(analysis_output, detected_points)
    _drop_false_freestanding_garage_findings(analysis_output)
    _normalize_scoring_output(analysis_output)
    _sync_gate_from_all_findings(analysis_output)
    _sync_category_breakdown_with_score_by_category(analysis_output)
    _scrub_age_only_category_summary_without_finding(analysis_output)
    _normalize_zero_score_language_findings(analysis_output)
    _ensure_tgiu_deductions_visible_in_all_findings(analysis_output)
    _sync_public_output_views(analysis_output)
    _ensure_writing_help_fields(analysis_output)
    _normalize_user_facing_child_titles(analysis_output)
    _polish_analysis_text_fields(analysis_output)
    _sanitize_analysis_output_text(analysis_output)
    finalize_client_arkat_semantic_pipeline_output(analysis_output, _normalize_tg3_cost_text)
    _drop_legacy_consequence_unclear_when_semantic_missing(analysis_output)
    _drop_tg3_missing_tiltak_for_semantic_tg2_not_applicable(analysis_output)
    _mark_duplicate_f001_informational(analysis_output)
    _normalize_category_summary_consequence_wording(analysis_output)
    _sanitize_user_facing_text_contracts(analysis_output)
    _drop_buyer_only_consequence_public_claims(analysis_output)
    _finalize_category_summary_public_contracts(analysis_output)
    _scrub_age_only_category_summary_without_finding(analysis_output)
    _ensure_tgiu_deductions_visible_in_all_findings(analysis_output)
    _remove_dead_public_visibility_fields(analysis_output)
    _mark_incomplete_fallback_output(analysis_output)
    final_score_total = analysis_output.get("score_total")
    if isinstance(final_score_total, (int, float)):
        analysis_output["trygghetsscore"] = int(final_score_total)
    return analysis_output


def _hash_evidence_span(evidence: object) -> str:
    if not evidence:
        return ""
    candidate = None
    if isinstance(evidence, list) and evidence:
        candidate = evidence[0]
    elif isinstance(evidence, dict):
        candidate = evidence
    if not isinstance(candidate, dict):
        return ""
    for key in ("snippet", "text", "span_excerpt"):
        value = candidate.get(key)
        if value:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()
    return ""


def _to_codepoint_string(text: str, max_chars: int = 24) -> str:
    if not isinstance(text, str):
        return ""
    sample = text[:max_chars]
    return " ".join(f"U+{ord(ch):04X}" for ch in sample)


def _log_text_encoding_diagnostics(run_id: str, raw_text: str) -> None:
    """
    Log diagnostics to pinpoint where encoding drift starts:
    - raw excerpts
    - normalized excerpts
    - codepoints around suspicious CJK characters
    """
    if not _should_log_debug(run_id):
        return
    raw = raw_text or ""
    normalized = _normalize_tg3_cost_text(raw)
    suspicious = SUSPICIOUS_CJK_RE.search(raw)
    payload: Dict[str, object] = {
        "raw_len": len(raw),
        "normalized_len": len(normalized),
    }
    if suspicious:
        idx = suspicious.start()
        start = max(0, idx - 24)
        end = min(len(raw), idx + 24)
        raw_window = raw[start:end]
        payload["raw_window"] = raw_window
        payload["raw_window_codepoints"] = _to_codepoint_string(raw_window, max_chars=48)
        norm_window = _normalize_tg3_cost_text(raw_window)
        payload["normalized_window"] = norm_window
        payload["normalized_window_codepoints"] = _to_codepoint_string(norm_window, max_chars=48)
    else:
        match = re.search(r"(?i)kostnads\w{0,20}", raw)
        if match:
            start = max(0, match.start() - 24)
            end = min(len(raw), match.end() + 24)
            raw_window = raw[start:end]
            payload["raw_window"] = raw_window
            payload["raw_window_codepoints"] = _to_codepoint_string(raw_window, max_chars=48)
    _log_debug(run_id, "text_encoding_diagnostics", payload)


def _build_scoring_result_export(analysis_output: Dict[str, object], document_hash: str) -> Dict[str, object]:
    deductions_export = []
    for component in analysis_output.get("findings", []):
        point_id = component.get("component_id", "")
        for deduction in component.get("deductions", []):
            if not isinstance(deduction, dict):
                continue
            rule_id = deduction.get("rule_id", "")
            category_id = deduction.get("category_id") or _infer_category_from_rule_id(rule_id)
            deductions_export.append(
                {
                    "point_id": point_id,
                    "rule_id": rule_id,
                    "category_id": category_id,
                    "points": deduction.get("points", 0),
                    "evidence_span_hash": _hash_evidence_span(deduction.get("evidence")),
                }
            )
    if not deductions_export:
        fallback_rows, _ = _collect_scored_all_findings_deductions(analysis_output)
        deductions_export = [
            {
                "point_id": row.get("point_id", ""),
                "rule_id": row.get("rule_id", ""),
                "category_id": row.get("category_id", ""),
                "points": row.get("points", 0),
                "evidence_span_hash": row.get("evidence_span_hash", ""),
            }
            for row in fallback_rows
        ]
    return {
        "document_hash": document_hash,
        "score_total": analysis_output.get("score_total", 0),
        "score_by_category": analysis_output.get("score_by_category", []),
        "deductions": deductions_export,
    }


def _collect_scored_all_findings_deductions(
    analysis_output: Dict[str, object],
    allowed_categories: Optional[set] = None,
) -> Tuple[List[Dict[str, object]], Dict[str, int]]:
    band_to_points = {
        "Høyt trekk": 5,
        "Middels trekk": 3,
        "Lavt trekk": 1,
        "Ikke scoretrekk": 0,
    }
    rows: List[Dict[str, object]] = []
    category_totals: Dict[str, int] = {}
    seen_keys: set = set()
    all_findings = analysis_output.get("all_findings")
    if not isinstance(all_findings, list):
        return rows, category_totals

    for idx, finding in enumerate(all_findings):
        if not isinstance(finding, dict):
            continue
        band = str(finding.get("deduction_band") or "").strip()
        explicit_points = finding.get("points", finding.get("deduction_points", finding.get("deduction")))
        try:
            points = int(explicit_points) if explicit_points is not None else band_to_points.get(band, 0)
        except (TypeError, ValueError):
            points = band_to_points.get(band, 0)
        if points <= 0:
            continue
        rule_id = str(finding.get("rule_id") or finding.get("finding_id") or "")
        category_id = str(finding.get("category") or _infer_category_from_rule_id(rule_id) or "")
        if not category_id:
            continue
        category_id = category_id.strip().upper()
        if allowed_categories is not None and category_id not in allowed_categories:
            continue
        point_id = str(finding.get("exact_point_id") or finding.get("point_id") or "")
        point_title = str(finding.get("exact_point_title") or finding.get("title") or point_id)
        dedupe_key = (
            str(finding.get("finding_id") or ""),
            rule_id,
            point_id,
            category_id,
            points,
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        category_totals[category_id] = category_totals.get(category_id, 0) + points
        evidence_snippets = finding.get("evidence_snippets") or []
        evidence_hash = ""
        if isinstance(evidence_snippets, list) and evidence_snippets and isinstance(evidence_snippets[0], str):
            evidence_hash = hashlib.sha256(evidence_snippets[0].encode("utf-8")).hexdigest()
        rows.append(
            {
                "source": "all_findings",
                "index": idx,
                "point_id": point_id,
                "point_title": point_title,
                "rule_id": rule_id,
                "category_id": category_id,
                "points": points,
                "reason": str(finding.get("message") or finding.get("title") or ""),
                "evidence_span_hash": evidence_hash,
            }
        )
    return rows, category_totals


def _supplement_with_scored_legal_findings(
    analysis_output: Dict[str, object],
    deduction_rows: List[Dict[str, object]],
    category_totals: Dict[str, int],
) -> None:
    legal_rows, _ = _collect_scored_all_findings_deductions(analysis_output, allowed_categories={"F"})
    method_rows, _ = _collect_scored_all_findings_deductions(analysis_output, allowed_categories={"E"})
    method_rows = [
        row for row in method_rows
        if str(row.get("rule_id") or "") in {"E_METHOD.tg3_cost_missing", "E_METHOD.tg3_cost_single_amount_only"}
    ]
    supplemental_rows = legal_rows + method_rows
    if not supplemental_rows:
        return
    existing_keys = {
        (
            str(row.get("rule_id") or ""),
            str(row.get("point_id") or ""),
            str(row.get("category_id") or "").strip().upper(),
            str(row.get("evidence_span_hash") or ""),
        )
        for row in deduction_rows
        if isinstance(row, dict)
    }
    for row in supplemental_rows:
        key = (
            str(row.get("rule_id") or ""),
            str(row.get("point_id") or ""),
            str(row.get("category_id") or "").strip().upper(),
            str(row.get("evidence_span_hash") or ""),
        )
        if key in existing_keys:
            continue
        existing_keys.add(key)
        deduction_rows.append(row)
        category_id = str(row.get("category_id") or "").strip().upper()
        points = int(row.get("points", 0) or 0)
        if category_id and points > 0:
            category_totals[category_id] = category_totals.get(category_id, 0) + points


def _build_score_reconciliation_payload(
    analysis_output: Dict[str, object],
    feedback_payload: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    scoring_model = _load_scoring_model()
    score_start = int(scoring_model.get("score_start", 100))
    score_floor = int(scoring_model.get("score_floor", 0))
    score_ceiling = int(scoring_model.get("score_ceiling", 100))

    deduction_rows: List[Dict[str, object]] = []
    raw_category_totals: Dict[str, int] = {}
    for component in analysis_output.get("findings", []):
        if not isinstance(component, dict):
            continue
        point_id = str(component.get("component_id") or "")
        point_title = str(component.get("component_title") or component.get("location") or point_id)
        for deduction in component.get("deductions", []):
            if not isinstance(deduction, dict):
                continue
            rule_id = str(deduction.get("rule_id") or "")
            category_id = str(deduction.get("category_id") or _infer_category_from_rule_id(rule_id) or "")
            points = int(deduction.get("points", 0) or 0)
            raw_category_totals[category_id] = raw_category_totals.get(category_id, 0) + points
            deduction_rows.append(
                {
                    "point_id": point_id,
                    "point_title": point_title,
                    "rule_id": rule_id,
                    "category_id": category_id,
                    "points": points,
                    "reason": str(deduction.get("reason") or ""),
                    "evidence_span_hash": _hash_evidence_span(deduction.get("evidence")),
                }
            )

    _supplement_with_scored_legal_findings(analysis_output, deduction_rows, raw_category_totals)

    fallback_rows, fallback_category_totals = _collect_scored_all_findings_deductions(analysis_output)
    if not deduction_rows and fallback_rows:
        deduction_rows = fallback_rows
        raw_category_totals = dict(fallback_category_totals)

    score_by_category = _ensure_score_by_category(analysis_output.get("score_by_category", []))
    visible_category_totals = {
        str(item.get("category_id") or ""): int(item.get("deduction", 0) or 0)
        for item in score_by_category
        if isinstance(item, dict)
    }
    visible_category_total = sum(visible_category_totals.values())
    uncapped_score_total = int(max(score_floor, min(score_ceiling, score_start - visible_category_total)))

    score_caps = analysis_output.get("score_caps", [])
    score_caps_export: List[Dict[str, object]] = []
    cap_points_total = 0
    if isinstance(score_caps, list):
        for cap in score_caps:
            if not isinstance(cap, dict):
                continue
            points_deducted = int(cap.get("points_deducted", 0) or 0)
            cap_points_total += points_deducted
            trigger_rules = []
            for rule_id in (cap.get("rule_ids") or []):
                if not rule_id:
                    continue
                trigger_rules.append(
                    {
                        "rule_id": str(rule_id),
                        "category_id": _infer_category_from_rule_id(str(rule_id)),
                    }
                )
            score_caps_export.append(
                {
                    "rule_ids": [item["rule_id"] for item in trigger_rules],
                    "trigger_rules": trigger_rules,
                    "max_total_score": int(cap.get("max_total_score", 0) or 0),
                    "original_score": int(cap.get("original_score", 0) or 0),
                    "capped_score": int(cap.get("capped_score", 0) or 0),
                    "points_deducted": points_deducted,
                }
            )

    final_score_total = int(analysis_output.get("score_total", 0) or 0)
    implied_total_deduction = max(0, score_start - final_score_total)
    reconciled_total_deduction = visible_category_total + cap_points_total
    unreconciled_gap = implied_total_deduction - reconciled_total_deduction

    feedback_summary: Dict[str, object] = {}
    if isinstance(feedback_payload, dict):
        feedback_score = feedback_payload.get("score", {})
        feedback_categories = (
            feedback_score.get("category_deductions", [])
            if isinstance(feedback_score, dict)
            else []
        )
        feedback_category_totals = {
            str(item.get("category") or ""): int(item.get("deduction", 0) or 0)
            for item in feedback_categories
            if isinstance(item, dict)
        }
        feedback_summary = {
            "score_total": int((feedback_score or {}).get("total", 0) or 0) if isinstance(feedback_score, dict) else 0,
            "category_totals": feedback_category_totals,
            "category_total": sum(feedback_category_totals.values()),
            "findings_count": len(feedback_payload.get("findings", [])) if isinstance(feedback_payload.get("findings"), list) else 0,
        }

    return {
        "score_start": score_start,
        "score_floor": score_floor,
        "score_ceiling": score_ceiling,
        "applied_deductions": deduction_rows,
        "raw_category_totals_from_components": raw_category_totals,
        "visible_category_totals": visible_category_totals,
        "visible_category_total": visible_category_total,
        "uncapped_score_total": uncapped_score_total,
        "score_caps": score_caps_export,
        "score_cap_points_total": cap_points_total,
        "final_score_total": final_score_total,
        "implied_total_deduction_from_final_score": implied_total_deduction,
        "reconciled_total_deduction": reconciled_total_deduction,
        "unreconciled_gap": unreconciled_gap,
        "feedback_summary": feedback_summary,
    }


def write_run_exports(
    document_hash: str,
    analysis_output: Dict[str, object],
    detected_points_payload: Dict[str, object],
    scoring_result_payload: Dict[str, object],
) -> None:
    exports_dir = Path(__file__).resolve().parents[2] / "exports"
    run_id = str(uuid.uuid4())
    run_dir = exports_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    run_meta = scoring_result_payload.get("run_meta", {}) if isinstance(scoring_result_payload, dict) else {}
    scoring_meta = run_meta.get("scoring_model", {}) if isinstance(run_meta, dict) else {}
    run_metadata = {
        "run_id": run_id,
        "document_hash": document_hash,
        "analysis_timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model_name": run_meta.get("model_name", ""),
        "temperature": run_meta.get("temperature", 0.0),
        "top_p": run_meta.get("top_p", 1.0),
        "seed": run_meta.get("seed"),
        "scoring_model_id": scoring_meta.get("model_id", ""),
        "scoring_model_version": scoring_meta.get("version", ""),
        "scoring_model_sha256": scoring_meta.get("sha256", ""),
        "pipeline_git_sha": settings.PIPELINE_GIT_SHA or get_prompt_context_sha(),
    }

    detected_points_export = detected_points_payload if isinstance(detected_points_payload, dict) else {}

    scoring_result_export = _build_scoring_result_export(analysis_output, document_hash)
    feedback_payload = None
    if isinstance(scoring_result_payload, dict):
        feedback_payload = scoring_result_payload.get("feedback_v11")
    score_reconciliation_export = _build_score_reconciliation_payload(analysis_output, feedback_payload)

    export_items = [
        ("run_metadata.json", run_metadata),
        ("detected_points.json", detected_points_export),
        ("scoring_result.json", scoring_result_export),
        ("score_reconciliation.json", score_reconciliation_export),
    ]
    if isinstance(feedback_payload, dict):
        export_items.append(("feedback_v1.1.json", feedback_payload))
    for filename, payload in export_items:
        (run_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def build_analysis_result_from_output(analysis_output: Dict[str, object]) -> AnalysisResult:
    overall_score = 0.0
    score_total = analysis_output.get("score_total")
    if isinstance(score_total, (int, float)):
        overall_score = float(score_total)
    if overall_score == 0.0:
        trygghetsscore = analysis_output.get("trygghetsscore")
        if isinstance(trygghetsscore, (int, float)) and 0 <= trygghetsscore <= 100:
            overall_score = float(trygghetsscore)

    components: List[ComponentBase] = []
    findings: List[FindingBase] = []
    recommendations: List[str] = []

    for component in analysis_output.get("findings", []):
        component_id = component.get("component_id") or "ukjent"
        component_title = component.get("component_title") or "Ukjent"
        tg = component.get("tg")
        location = component.get("location")

        components.append(
            ComponentBase(
                component_type=component_id,
                name=component_title,
                condition=tg,
                description=location,
                score=None,
            )
        )

        for issue in component.get("issues", []):
            rule_refs = issue.get("rule_refs", [])
            findings.append(
                FindingBase(
                    finding_type=issue.get("issue_id", "issue"),
                    severity=issue.get("severity", "medium"),
                    title=issue.get("summary", "Avvik"),
                    description=issue.get("details", ""),
                    suggestion=None,
                    standard_reference=", ".join(rule_refs) if rule_refs else None,
                )
            )

    for improvement in analysis_output.get("improvements", []):
        title = improvement.get("title")
        what_to_change = improvement.get("what_to_change")
        if title or what_to_change:
            recommendations.append(title or what_to_change)

    summary = analysis_output.get("score_band", "")

    return AnalysisResult(
        overall_score=overall_score,
        quality_score=0.0,
        completeness_score=0.0,
        compliance_score=0.0,
        components=components,
        findings=findings,
        summary=summary,
        recommendations=recommendations,
    )


class AIAnalyzer:
    """Analyze building condition reports using the current Validert baseline"""

    @staticmethod
    def analyze_report(
        text: str,
        report_system: str = None,
        building_year: int = None,
        pdf_metadata: Optional[Dict] = None,
        document_title: Optional[str] = None,
        document_id: Optional[str] = None,
        document_hash: Optional[str] = None,
    ):
        """
        Analyze a building condition report using the current Validert baseline.

        Args:
            text: Extracted text from PDF (should include all pages, appendices, images)
            report_system: Optional report system identifier
            building_year: Optional building year
            pdf_metadata: Optional PDF metadata (pages, appendices, etc.)
            document_title: Optional filename/title for output meta
            document_id: Optional report id for output meta

        Returns:
            Tuple of (AnalysisResult, analysis_output_dict, detected_points_payload, scoring_result_payload)
        """
        try:
            normalized_text = _normalize_report_text_for_analysis(text or "")
            context_info = ""
            if building_year:
                context_info += f"\nByggeår: {building_year}\n"
            if report_system:
                context_info += f"Rapportsystem: {report_system}\n"
            if document_title:
                context_info += f"Dokumenttittel: {document_title}\n"
            if document_id:
                context_info += f"Dokument-ID: {document_id}\n"
            report_regime_context = _extract_report_regime_context(normalized_text)
            identity_report_date = _detect_report_date_from_document_identity(document_title, document_id)
            if identity_report_date and _report_text_suggests_compressed_mixed_format(normalized_text):
                report_regime_context["report_date"] = identity_report_date
                report_regime_context["report_regime"] = _detect_report_regime(
                    identity_report_date,
                    report_regime_context.get("ns_version") or "",
                )
            if any(report_regime_context.values()):
                context_info += "Rapportmetadata utledet av backend før scoring:\n"
                context_info += f"- report_date: {report_regime_context.get('report_date') or 'UNKNOWN'}\n"
                context_info += f"- ns_version: {report_regime_context.get('ns_version') or 'UNKNOWN'}\n"
                context_info += f"- report_regime: {report_regime_context.get('report_regime') or 'UNKNOWN'}\n"

            if pdf_metadata is None:
                if "[PDF METADATA]" in normalized_text:
                    metadata_section = normalized_text.split("[PDF METADATA]")[1].split("[START RAPPORTTEKST]")[0]
                    total_pages = 0
                    if "Totalt antall sider:" in metadata_section:
                        try:
                            total_pages = int(
                                metadata_section.split("Totalt antall sider:")[1].split("\n")[0].strip()
                            )
                        except Exception:
                            pass
                    pdf_metadata = {
                        "total_pages": total_pages,
                        "pages_with_text": total_pages,
                        "images_detected": 0,
                        "full_document_available": True,
                    }
                else:
                    pdf_metadata = {
                        "total_pages": 0,
                        "pages_with_text": 0,
                        "images_detected": 0,
                        "full_document_available": True,
                    }

            if not document_hash:
                document_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()

            run_id = str(uuid.uuid4())
            scoring_model_info = get_scoring_model_info()
            run_meta_base = {
                "run_id": run_id,
                "analysis_timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "model_name": "eu.anthropic.claude-sonnet-4-20250514-v1:0" if settings.USE_AWS_BEDROCK else settings.OPENAI_MODEL,
                "temperature": 0.0,
                "top_p": 1.0,
                "seed": None if settings.USE_AWS_BEDROCK else settings.OPENAI_SEED,
                "text_sha256": document_hash,
                "scoring_model": scoring_model_info,
                "pipeline_git_sha": f"{settings.PIPELINE_GIT_SHA}:{get_prompt_context_sha()}" if settings.PIPELINE_GIT_SHA else get_prompt_context_sha(),
                "report_date": report_regime_context.get("report_date") or "",
                "ns_version": report_regime_context.get("ns_version") or "",
                "report_regime": report_regime_context.get("report_regime") or "",
            }

            incomplete_reasons: List[str] = []
            if isinstance(pdf_metadata, dict):
                if pdf_metadata.get("full_document_available") is False:
                    incomplete_reasons.append("pdf_text_incomplete")

            prompt_context = build_prompt_context()

            system_tokens = estimate_tokens(SYSTEM_PROMPT)
            response_tokens = 8000
            context_tokens = estimate_tokens(context_info)
            prompt_context_tokens = estimate_tokens(prompt_context)
            buffer_tokens = 1000

            if settings.USE_AWS_BEDROCK:
                available_tokens = 100000 - system_tokens - response_tokens - context_tokens - prompt_context_tokens - buffer_tokens
            else:
                available_tokens = 100000 - system_tokens - response_tokens - context_tokens - prompt_context_tokens - buffer_tokens

            text_tokens = estimate_tokens(normalized_text)
            token_overflow = max(0, text_tokens - available_tokens)
            token_overflow_allowance = _preflight_token_overflow_allowance(available_tokens)
            if token_overflow > token_overflow_allowance:
                incomplete_reasons.append("input_truncated")

            detected_points_payload = get_validated_detected_points_payload(
                normalized_text,
                document_hash=document_hash,
                document_title=document_title,
                document_id=document_id,
                pdf_metadata=pdf_metadata,
            )
            detected_points = detected_points_payload.get("points", []) if isinstance(detected_points_payload, dict) else []
            runtime_e3_context = _build_runtime_e3_scoring_context(normalized_text, detected_points)
            _log_debug(
                run_id,
                "preflight",
                {
                    "document_hash": document_hash,
                    "text_chars": len(normalized_text),
                    "text_tokens_est": text_tokens,
                    "available_tokens_est": available_tokens,
                    "token_overflow_est": token_overflow,
                    "token_overflow_allowance_est": token_overflow_allowance,
                    "pdf_metadata": pdf_metadata or {},
                    "detected_points": len(detected_points),
                    "chunking": "none",
                    "incomplete_reasons": incomplete_reasons,
                },
            )
            _log_text_encoding_diagnostics(run_id, text)

            if incomplete_reasons:
                raise IncompleteAnalysisError(
                    "Analysis incomplete: full document could not be analyzed.",
                    incomplete_reasons,
                    run_meta_base,
                    detected_points_payload=detected_points_payload,
                    document_hash=document_hash,
                )

            user_prompt = f"""
{context_info}

{prompt_context}

{runtime_e3_context}

===== TILSTANDSRAPPORT SOM SKAL ANALYSERES =====

Analyser følgende norske tilstandsrapport.

VIKTIG: Du må analysere HELE dokumentet. Alle sider, vedlegg og bilder må vurderes.

Rapporttekst:
{normalized_text}

FORMATKRAV: Returner kompakt JSON (ingen innrykk/linjeskift).
Du må returnere FULLSTENDIG liste over alle påviselige avvik og alle score-trekk.
Ikke skjul, prioriter bort eller slå sammen funn for å spare plass.
Produser KUN gyldig JSON i henhold til OUTPUT SCHEMA. Ingen tekst utenfor JSON.
"""

            llm_start = time.monotonic()
            if settings.USE_AWS_BEDROCK:
                logger.info("Using AWS Bedrock Claude for analysis")
                from app.services.bedrock_ai import BedrockAI
                bedrock = BedrockAI(region=settings.AWS_REGION)
                try:
                    analysis_output, llm_meta = bedrock.analyze_report_with_claude(
                        user_prompt=user_prompt,
                        return_meta=True,
                    )
                except Exception as e:
                    if "timeout" in str(e).lower():
                        raise IncompleteAnalysisError(
                            "Analysis incomplete: LLM timeout.",
                            ["llm_timeout"],
                            run_meta_base,
                            detected_points_payload=detected_points_payload,
                            document_hash=document_hash,
                        )
                    if "json" in str(e).lower() or "parse" in str(e).lower():
                        raise IncompleteAnalysisError(
                            "Analysis incomplete: LLM returned invalid JSON.",
                            ["llm_parse_failed"],
                            run_meta_base,
                            detected_points_payload=detected_points_payload,
                            document_hash=document_hash,
                        )
                    raise
                llm_duration = time.monotonic() - llm_start
                model_name = llm_meta.get("model_name") or "eu.anthropic.claude-sonnet-4-20250514-v1:0"
                _log_debug(
                    run_id,
                    "llm_response",
                    {
                        "provider": "bedrock",
                        "model_name": model_name,
                        "duration_s": round(llm_duration, 3),
                        "stop_reason": llm_meta.get("stop_reason"),
                        "truncated": llm_meta.get("truncated"),
                        "response_chars": llm_meta.get("response_chars"),
                    },
                )
                if llm_meta.get("truncated"):
                    raise IncompleteAnalysisError(
                        "Analysis incomplete: LLM response truncated.",
                        ["llm_max_tokens"],
                        {**run_meta_base, "model_name": model_name},
                        detected_points_payload=detected_points_payload,
                        document_hash=document_hash,
                    )
            else:
                logger.info("Using OpenAI GPT-4 for analysis")
                client = get_openai_client()
                model = settings.OPENAI_MODEL

                try:
                    request_kwargs = {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.0,
                        "top_p": 1.0,
                        "max_tokens": 8000,
                    }
                    if settings.OPENAI_SEED is not None:
                        request_kwargs["seed"] = settings.OPENAI_SEED
                    response = client.chat.completions.create(
                        **request_kwargs
                    )
                except Exception as e:
                    if "model" in str(e).lower():
                        logger.info("Falling back to gpt-4o model")
                        model = "gpt-4o"
                        fallback_kwargs = {
                            "model": model,
                            "messages": [
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": user_prompt},
                            ],
                            "temperature": 0.0,
                            "top_p": 1.0,
                            "max_tokens": 8000,
                        }
                        if settings.OPENAI_SEED is not None:
                            fallback_kwargs["seed"] = settings.OPENAI_SEED
                        response = client.chat.completions.create(
                            **fallback_kwargs
                        )
                    elif "timeout" in str(e).lower():
                        raise IncompleteAnalysisError(
                            "Analysis incomplete: LLM timeout.",
                            ["llm_timeout"],
                            run_meta_base,
                            detected_points_payload=detected_points_payload,
                            document_hash=document_hash,
                        )
                    else:
                        raise

                llm_duration = time.monotonic() - llm_start
                finish_reason = response.choices[0].finish_reason
                _log_debug(
                    run_id,
                    "llm_response",
                    {
                        "provider": "openai",
                        "model_name": model,
                        "duration_s": round(llm_duration, 3),
                        "finish_reason": finish_reason,
                    },
                )
                if finish_reason == "length":
                    raise IncompleteAnalysisError(
                        "Analysis incomplete: LLM response truncated.",
                        ["llm_max_tokens"],
                        {**run_meta_base, "model_name": model},
                        detected_points_payload=detected_points_payload,
                        document_hash=document_hash,
                    )

                response_text = response.choices[0].message.content.strip()
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1

                if json_start != -1 and json_end > json_start:
                    json_text = response_text[json_start:json_end]
                    try:
                        analysis_output = json.loads(json_text)
                    except json.JSONDecodeError:
                        raise IncompleteAnalysisError(
                            "Analysis incomplete: LLM returned invalid JSON.",
                            ["llm_parse_failed"],
                            {**run_meta_base, "model_name": model},
                            detected_points_payload=detected_points_payload,
                            document_hash=document_hash,
                        )
                else:
                    raise IncompleteAnalysisError(
                        "Analysis incomplete: LLM returned no JSON.",
                        ["llm_parse_failed"],
                        {**run_meta_base, "model_name": model},
                        detected_points_payload=detected_points_payload,
                        document_hash=document_hash,
                    )

            if not isinstance(analysis_output, dict):
                raise ValueError("AI output is not a JSON object")

            _ensure_meta_fields(analysis_output, document_title, document_id)
            postprocess_analysis_output(
                analysis_output,
                normalized_text,
                identity_report_date if identity_report_date and _report_text_suggests_compressed_mixed_format(normalized_text) else "",
            )
            meta = analysis_output.get("meta", {})
            if isinstance(meta, dict) and identity_report_date and _report_text_suggests_compressed_mixed_format(normalized_text):
                meta["report_date"] = identity_report_date
                meta["report_regime"] = _detect_report_regime(identity_report_date, meta.get("ns_version") or meta.get("ns_standard_version") or "")
            if isinstance(meta, dict):
                meta.setdefault("scoring_model_id", scoring_model_info.get("model_id", ""))
                meta.setdefault("scoring_model_version", scoring_model_info.get("version", ""))
                meta.setdefault("scoring_model_updated_at", scoring_model_info.get("updated_at", ""))
                meta.setdefault("analysis_mode", "full")
                meta["runtime_manifest"] = get_runtime_manifest(str(meta.get("analysis_mode") or "full"))
                analysis_output["meta"] = meta
                analysis_output["runtime_manifest"] = meta["runtime_manifest"]
            if settings.USE_AWS_BEDROCK:
                seed_used = None
            else:
                seed_used = settings.OPENAI_SEED
                model_name = model

            run_meta = dict(run_meta_base)
            run_meta["model_name"] = model_name
            run_meta["seed"] = seed_used
            run_meta["runtime_manifest"] = analysis_output.get("runtime_manifest") or get_runtime_manifest(str(meta.get("analysis_mode") or "full"))
            logger.info("Detected %s points before scoring", len(detected_points))

            feedback_v11 = build_feedback_v11(
                analysis_output,
                detected_points_payload,
                report_id=document_id,
                document_hash=document_hash,
                report_text=normalized_text,
            )
            _drop_buyer_only_consequence_public_claims(feedback_v11)
            score_reconciliation = _build_score_reconciliation_payload(analysis_output, feedback_v11)
            _log_debug(
                run_id,
                "score_reconciliation",
                score_reconciliation,
            )

            scoring_result_payload = {
                "run_meta": run_meta,
                "analysis_output": analysis_output,
                "feedback_v11": feedback_v11,
                "score_reconciliation": score_reconciliation,
            }
            analysis_output = sanitize_bmtf_public_point_taxonomy_payload(
                analysis_output,
                normalized_text,
            )
            scoring_result_payload["analysis_output"] = analysis_output
            scoring_result_payload = sanitize_bmtf_public_point_taxonomy_payload(
                scoring_result_payload,
                normalized_text,
            )
            _drop_buyer_only_consequence_public_claims(analysis_output)
            _drop_buyer_only_consequence_public_claims(scoring_result_payload)
            detected_points_payload = sanitize_bmtf_public_point_taxonomy_payload(
                detected_points_payload,
                normalized_text,
            )

            result = build_analysis_result_from_output(analysis_output)
            overall_score = result.overall_score

            logger.info("Successfully analyzed report. Score: %s", overall_score)

            return result, analysis_output, detected_points_payload, scoring_result_payload

        except IncompleteAnalysisError:
            raise
        except json.JSONDecodeError as e:
            logger.error("Failed to parse AI response as JSON: %s", str(e))
            raise IncompleteAnalysisError(
                "Analysis incomplete: LLM returned invalid JSON.",
                ["llm_parse_failed"],
                run_meta_base if "run_meta_base" in locals() else {},
                detected_points_payload=locals().get("detected_points_payload"),
                document_hash=locals().get("document_hash"),
            )
        except Exception as e:
            logger.error("Error analyzing report with AI: %s", str(e), exc_info=True)
            raise Exception(f"AI analysis failed: {str(e)}")
