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
from app.services.system_prompt import SYSTEM_PROMPT
from app.services.validert_files import (
    build_prompt_context,
    get_building_part_whitelist,
    get_building_part_whitelist_v21,
    get_building_part_whitelist_v22,
    get_canonical_points_v30,
    get_category_config_text,
    get_legality_arkat_map_text,
    get_legality_arkat_templates_text,
    get_legality_rules_text,
    get_prompt_context_sha,
    get_scoring_model_info,
    get_scoring_model_text,
    get_ui_overlay_config,
    get_points_overview_mapping_config,
    get_migration_map,
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
# Fallback: line that is just a number or number.number (e.g. "6.2" or "6.2.")
POINT_HEADER_FALLBACK_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,4})\.?\s*(?:[-–—:]?\s*(.*\S)?)?$")
SUMMARY_INLINE_POINT_RE = re.compile(r"\b(\d+(?:\.\d+){1,4})\.?\b")
TG_RE = re.compile(r"\bTG(?:0|1|2|3|IU)\b")
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


POINT_ID_IN_TEXT_RE = re.compile(r"(?:Punkt|punkt)\s+(\d+(?:\.\d+)*)", re.IGNORECASE)
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
    return s.strip()

# Content-based ARK/ARKAT detection (semantic, no strict labels) – per-segment validation
ARK_ÅRSAK_RE = re.compile(
    r"årsak|begrunnelse|fordi|på grunn|forårsaket|vurderes å være|grunnet|pga\.|forklaring|derfor er",
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
    r"anbefalt\s+tiltak|anbefalte\s+tiltak|(?:andre\s+)?tiltak\s*:|anbefales|må\s+utbedres|krever\s+utbedring|bør\s+skiftes|"
    r"utskiftning\s+anbefales|det\s+anbefales|anbefalt\s+å|anbefales\s+å|"
    r"bør\s+undersøkes|undersøkelse\s+(?:av|ved)|utført\s+av\s+fagperson|videre\s+undersøkelser|"
    r"etableres|utbedres|skiftes|undersøkes\s+av|må\s+undersøkes",
    re.IGNORECASE,
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


def truncate_text_smart(text: str, max_tokens: int = 5000) -> str:
    """
    Truncate text intelligently to fit within token limit.
    Keeps the beginning and end of the text, removing middle sections.
    NOTE: For Validert, we should try to process full document, but if too large,
    we need to indicate this in the prompt context.
    """
    max_chars = max_tokens * 4

    if len(text) <= max_chars:
        return text

    first_part_chars = int(max_chars * 0.6)
    last_part_chars = int(max_chars * 0.4)

    first_part = text[:first_part_chars]
    last_part = text[-last_part_chars:]

    truncated = (
        f"{first_part}\n\n"
        "[... midtdel av rapporten utelatt for a spare tokens - FULL DOKUMENTANALYSE IKKE MULIG ...]\n\n"
        f"{last_part}"
    )

    logger.warning(
        "Text truncated from %s to %s characters (estimated %s tokens)",
        len(text),
        len(truncated),
        estimate_tokens(truncated),
    )
    return truncated


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
            tg_match = TG_RE.search(span_text)
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
                    "tg": tg_match.group(0) if tg_match else "",
                    "span_text": span_text,
                }
            )

    # Supplement extraction with heading-like lines classified by whitelist v2.2.
    # Some templates have partial/unstable numeric structure; keep this available
    # even when numeric headers exist so canonical mapping can still recover.
    wl = _get_whitelist_v22_lookup()
    if wl and line_index:
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
        tg_match = TG_RE.search(full_text)
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
                "tg": tg_match.group(0) if tg_match else "TG2",
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
    validated = _validate_detected_points_against_whitelist(detected, trace=trace)
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


def _parse_numeric_id(value: str) -> List[int]:
    return [int(part) for part in value.split(".") if part.isdigit()]


def _compare_numeric_ids(a: str, b: str) -> int:
    arr_a = _parse_numeric_id(a)
    arr_b = _parse_numeric_id(b)
    max_len = max(len(arr_a), len(arr_b))
    for i in range(max_len):
        val_a = arr_a[i] if i < len(arr_a) else None
        val_b = arr_b[i] if i < len(arr_b) else None
        if val_a is None and val_b is not None:
            return -1
        if val_a is not None and val_b is None:
            return 1
        if val_a is None and val_b is None:
            return 0
        if val_a < val_b:
            return -1
        if val_a > val_b:
            return 1
    return 0


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
        validated.append({
            **dict(p),
            "canonical_building_part_id": bp.get("id", ""),
            "canonical_display_name": bp.get("display_name", ""),
            "required_by_forskrift": bp.get("required_by_forskrift", True),
            "ui_badge": bp.get("ui_badge"),
            "legal_status": legal,
            "instance_label": instance_label,
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
        if not existing.get("tg") and point.get("tg"):
            existing["tg"] = point.get("tg")
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
    for m in mapping_points:
        if not isinstance(m, dict):
            continue
        if _segment_matches_canonical(norm_text, m, {}):
            cid = m.get("canonical_id")
            if isinstance(cid, str) and cid:
                return cid
    return None


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
        point_id = _parse_point_id_from_v16_finding(finding)
        if point_id and point_id in allowed_point_ids and point_id != "GLOBAL":
            linked += 1
            continue
        text_candidates: List[str] = []
        for key in ("message", "title"):
            value = finding.get(key)
            if isinstance(value, str) and value.strip():
                text_candidates.append(value)
        for snip in (finding.get("evidence_snippets") or []):
            if isinstance(snip, str) and snip.strip():
                text_candidates.append(snip)
        inferred = None
        for candidate in text_candidates:
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
    linked = _extract_linked_summary_text_per_point(report_text or "")
    segment_by_point = {}
    for p in detected_points:
        if isinstance(p, dict) and p.get("point_id"):
            main = (p.get("span_text") or "").strip()
            summary = _get_linked_summary_for_point(linked, p.get("point_id") or "").strip()
            combined = (main + "\n" + summary).strip() if summary else main
            segment_by_point[str(p["point_id"])] = combined
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
        point_id = _parse_point_id_from_v16_finding(f)
        segment_text = segment_by_point.get(point_id or "", "")

        evidence_parts: List[str] = []
        message = f.get("message")
        if isinstance(message, str):
            evidence_parts.append(message)
        for snip in f.get("evidence_snippets") or []:
            if isinstance(snip, str):
                evidence_parts.append(snip)
        evidence_text = "\n".join(evidence_parts)

        statuses = []
        if segment_text:
            statuses.append(_tg3_cost_status(segment_text))
        if evidence_text:
            statuses.append(_tg3_cost_status(evidence_text))
        status = "high"
        if "pass" in statuses:
            status = "pass"
        elif "medium" in statuses:
            status = "medium"

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
    text = _normalize_tg3_cost_text(segment_text).lower()
    return any(term in text for term in _PRACTICAL_CONSEQUENCE_TERMS)


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
    for p in detected_points:
        if not isinstance(p, dict):
            continue
        point_id = str(p.get("point_id") or "").strip()
        if not point_id:
            continue
        main = str(p.get("span_text") or "").strip()
        summary = _get_linked_summary_for_point(linked, point_id).strip()
        combined = (main + "\n" + summary).strip() if summary else main
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
    pages = _split_pages(report_text)
    linked: Dict[str, str] = {}
    for page in pages:
        page_text = (page.get("text") or "").strip()
        if not page_text:
            continue
        lower = page_text.lower()
        if not any(m in lower for m in SUMMARY_MARKERS):
            continue
        lines = page_text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            match = POINT_HEADER_RE.match(stripped) or POINT_HEADER_FALLBACK_RE.match(stripped)
            if match:
                raw_pid = (match.group(1) or "").strip()
                section_title = (match.group(2) or "").strip() if match.lastindex >= 2 else ""
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
                    next_match = POINT_HEADER_RE.match(next_stripped) or POINT_HEADER_FALLBACK_RE.match(next_stripped)
                    if next_match:
                        next_pid = (next_match.group(1) or "").strip()
                        if next_pid and not _looks_like_date_point_id(next_pid):
                            break
                    block_lines.append(next_line)
                    j += 1
                block_text = "\n".join(block_lines).strip()
                if block_text:
                    existing = linked.get(point_id, "")
                    linked[point_id] = (existing + "\n" + block_text).strip() if existing else block_text
                i = j
            else:
                i += 1
        # Inline fallback for summary pages that list multiple punktnummer in running text.
        for raw_line in lines:
            line = (raw_line or "").strip()
            if len(line) < 8:
                continue
            refs = [m.group(1) for m in SUMMARY_INLINE_POINT_RE.finditer(line)]
            if not refs:
                continue
            if not (TG_RE.search(line) or ARK_ÅRSAK_RE.search(line) or ARK_RISIKO_RE.search(line) or ARK_KONSEKVENS_RE.search(line) or ARK_TILTAK_RE.search(line) or TG3_INTERVAL_RE.search(line) or TG3_COST_CLASS_RE.search(line) or TG3_SINGLE_AMOUNT_RE.search(line)):
                continue
            for ref in refs:
                if _looks_like_date_point_id(ref) or _is_noise_point_id(ref):
                    continue
                pid = _normalize_point_id(ref)
                existing = linked.get(pid, "")
                linked[pid] = (existing + "\n" + line).strip() if existing else line
    return linked


def _get_linked_summary_for_point(linked: Dict[str, str], point_id: str) -> str:
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
    # Parent chain: 10.5.1 -> 10.5 -> 10
    while "." in pid:
        pid = pid.rsplit(".", 1)[0]
        if pid in linked:
            return linked[pid]
    return ""


def _merge_detected_points_with_linked_summary(
    detected_points: List[Dict[str, object]],
    report_text: str,
) -> List[Dict[str, object]]:
    """
    Pipeline join step for E3: merge main point text with TG2/TG3 summary sections by punktnummer.
    Preserves existing point IDs and upgrades TG when summary provides stronger TG marker.
    """
    if not detected_points:
        return detected_points
    linked = _extract_linked_summary_text_per_point(report_text or "")
    if not linked:
        return detected_points
    merged: List[Dict[str, object]] = []
    for p in detected_points:
        if not isinstance(p, dict):
            continue
        point_id = _normalize_point_id(str(p.get("point_id") or p.get("numeric_id") or p.get("native_label") or ""))
        if not point_id:
            merged.append(p)
            continue
        summary_text = _get_linked_summary_for_point(linked, point_id).strip()
        if not summary_text:
            merged.append(p)
            continue
        out = dict(p)
        main_text = str(out.get("span_text") or "").strip()
        combined = (main_text + "\n" + summary_text).strip() if main_text else summary_text
        out["span_text"] = combined
        out["linked_summary_text"] = summary_text
        summary_tg_match = TG_RE.search(summary_text)
        if summary_tg_match:
            existing_tg = str(out.get("tg") or "").upper()
            summary_tg = summary_tg_match.group(0).upper()
            if _tg_rank(summary_tg) > _tg_rank(existing_tg):
                out["tg"] = summary_tg
        merged.append(out)
    return merged


def _segment_has_ark_arkat(combined_text: str, tg: str) -> Tuple[bool, List[str]]:
    """
    Content-based check: does combined segment text (main + linked summary) contain
    required ARK/ARKAT elements.

    Guardrail:
    - TG2: requires ARK (årsak, risiko, konsekvens). Manglende anbefalt tiltak eller kostnad
      skal ikke alene gi trekk.
    - TG3: requires full ARKAT + kostnad (årsak, risiko, konsekvens, anbefalt tiltak, kostnad).

    Returns (passed, list of missing internal element keys).
    """
    if not tg or ("TG2" not in tg.upper() and "TG3" not in tg.upper()):
        return True, []
    if not (combined_text or "").strip():
        missing = ["årsak", "risiko", "konsekvens"]
        if "TG3" in tg.upper():
            missing.extend(["anbefalt_tiltak", "kostnad"])
        return False, missing
    lower = combined_text.lower()
    missing: List[str] = []
    if "TG2" in tg.upper() or "TG3" in tg.upper():
        if not ARK_ÅRSAK_RE.search(lower):
            missing.append("årsak")
        if not ARK_RISIKO_RE.search(lower):
            missing.append("risiko")
        if not ARK_KONSEKVENS_RE.search(lower):
            missing.append("konsekvens")
    if "TG3" in tg.upper():
        if not ARK_TILTAK_RE.search(lower):
            missing.append("anbefalt_tiltak")
        cost_status = _tg3_cost_status(combined_text)
        if cost_status == "high":
            missing.append("kostnad")
        elif cost_status == "medium":
            missing.append("kostnad_single_only")
    if missing:
        return False, missing
    return True, []


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
        "konsekvens for kjøper (bruk/sikkerhet/økonomi/videre skade)",
        "Presiser den praktiske konsekvensen for kjøper (f.eks. økte kostnader, behov for tiltak, eller sikkerhetsmessig betydning).",
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
    merged_by_id: Dict[str, Dict[str, object]] = {}
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        point_id = (point.get("point_id") or point.get("native_label") or "").strip()
        if not point_id or _is_noise_point_id(point_id):
            continue
        tg = (point.get("tg") or "").strip().upper()
        main_text = (point.get("span_text") or "").strip()
        title = point.get("title") or point_id
        if point_id not in merged_by_id:
            merged_by_id[point_id] = {"point_id": point_id, "tg": tg, "title": title, "span_text": main_text}
        else:
            existing = merged_by_id[point_id]
            existing["span_text"] = ((existing.get("span_text") or "") + "\n" + main_text).strip()
            if "TG3" in tg and "TG3" not in (existing.get("tg") or ""):
                existing["tg"] = tg
            elif "TG2" in tg and "TG2" not in (existing.get("tg") or ""):
                existing["tg"] = tg
    failed_segments = []
    segment_validation = []
    for point_id, point in merged_by_id.items():
        tg = (point.get("tg") or "").strip().upper()
        if "TG2" not in tg and "TG3" not in tg:
            segment_validation.append({"point_id": point_id, "tg": tg, "passed": True, "missing": []})
            continue
        main_text = (point.get("span_text") or "").strip()
        summary_text = _get_linked_summary_for_point(linked_summary, point_id).strip()
        combined = (main_text + "\n" + summary_text).strip() if summary_text else main_text
        passed, missing = _segment_has_ark_arkat(combined, tg)
        segment_validation.append({"point_id": point_id, "tg": tg, "passed": passed, "missing": missing})
        if not passed:
            failed_segments.append({
                "point_id": point_id,
                "tg": tg,
                "title": point.get("title") or point_id,
                "missing": missing,
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
        missing_keys = seg.get("missing") or []
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
        all_findings.append({
            "finding_id": f"SEGMENT_ARKAT_{point_label.replace('.', '_')}",
            "category": "A",
            "severity": severity,
            "title": f"Punkt {point_label} ({seg.get('tg')}) mangler full ARK/ARKAT-tekst",
            "message": f"Punkt {point_label} ({title}): mangler {missing_str}. Validering gjøres per punkt; tekst må finnes i selve punktet eller i en tydelig koblet oppsummering.",
            "deduction_band": deduction_band,
            "recommended_fix_text": recommended_fix,
            "evidence_snippets": [],
            "gate_effect": {"blocks_96_gate": False, "caps_total_score_to": None},
        })


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
        point_id = _parse_point_id_from_v16_finding(f)
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
        point_ref = f" for punkt {point_id}" if point_id else ""
        if "risiko" in fid or "risiko_missing" in title.lower() or "mangler risiko" in title.lower():
            f["recommended_fix_text"] = (
                f"Legg til kort risiko{point_ref}: beskriv hva som kan skje dersom forholdet ikke håndteres, "
                "slik at kjøper kan vurdere alvorlighetsgrad."
            )
        elif "konsekvens" in fid or "konsekvens" in title.lower():
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
                f"Oppdater innholdet{point_ref} slik at det tydelig adresserer funnet: «{title[:80]}{'…' if len(title) > 80 else ''}». "
                "Beskriv konkret hva som mangler eller bør forbedres."
            )


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
            if "sjablonmessig kostnadsanslag" in summary.lower():
                entry["summary"] = "Metodikk og lovforankring: se øvrige funn."


def _drop_tg2_tiltak_requirement_false_positives(analysis_output: Dict[str, object]) -> None:
    """
    TG2 rule guard: TG2 must not be penalized for missing recommended measure (tiltak).
    Remove such findings/drivers/issues while keeping TG3 tiltak requirements intact.
    """
    def _is_tg2_tiltak_only(text: str, tg_hint: str = "") -> bool:
        low = _normalize_tg3_cost_text(text or "").lower()
        tg_norm = str(tg_hint or "").strip().upper()
        has_tg2 = ("tg2" in low) or (tg_norm == "TG2")
        has_tg3 = ("tg3" in low) or (tg_norm == "TG3")
        has_tiltak_phrase = (
            "mangler anbefalt tiltak" in low
            or "tg2-punkt mangler anbefalt tiltak" in low
            or "anbefalt tiltak mangler" in low
            or "mangler tiltak" in low
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


def _drop_arkat_false_positives(analysis_output: Dict[str, object]) -> None:
    """
    Remove LLM findings that claim missing anbefalt tiltak / ARKAT for a point when
    our per-segment validation (semantic ARK/ARKAT check) passed for that point.
    """
    segment_validation = analysis_output.get("segment_validation")
    if not isinstance(segment_validation, list):
        return
    passed_point_ids = {
        _normalize_point_id(str(s.get("point_id") or ""))
        for s in segment_validation
        if isinstance(s, dict) and s.get("passed") is True
    }
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
        if "SEGMENT_ARKAT_" in (f.get("finding_id") or ""):
            continue
        is_arkat_finding = (
            "tiltak" in fid or "arkat" in fid or "anbefalt" in title or "mangler anbefalt" in msg
            or "mangler full arkat" in msg or "tg3 mangler" in title
        )
        if not is_arkat_finding:
            continue
        point_id = _parse_point_id_from_v16_finding(f)
        if point_id and _normalize_point_id(point_id) in passed_point_ids:
            to_drop.append(idx)
    for idx in reversed(to_drop):
        all_findings.pop(idx)


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
    s = re.sub(r"\bmed n kort setning\b", "med en kort setning", s, flags=re.IGNORECASE)
    s = re.sub(r"\bmed n\b", "med en", s, flags=re.IGNORECASE)
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


def _polish_feedback_findings(findings: List[Dict[str, object]]) -> None:
    for finding in findings:
        if not isinstance(finding, dict):
            continue
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
            for key in ("title", "excerpt", "native_label", "anchor_text"):
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
            if _segment_matches_canonical(norm_title, cp, {}):
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
            summary = "OK – ingen endringer nødvendig." if deduction <= 0 else "Trekk er registrert for punktet."
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


def _build_feedback_v11(
    analysis_output: Dict[str, object],
    detected_points_payload: Dict[str, object],
    report_id: Optional[str],
    document_hash: Optional[str],
) -> Dict[str, object]:
    points = detected_points_payload.get("points", []) if isinstance(detected_points_payload, dict) else []
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
    if (
        not analysis_output.get("findings")
        and isinstance(all_findings, list)
        and len(all_findings) > 0
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
            mapping_input_points.extend([p for p in points if isinstance(p, dict)])
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
                mapping_input_points.append(p)

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
            mapping_input_points.extend([p for p in points if isinstance(p, dict)])
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
                mapping_input_points.append(p)
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
            int(d.get("points", 0)) for d in deductions if isinstance(d, dict)
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
                    "point_id": point_id,
                    "point_key": point_key or point_id,
                    "arkat_section": arkat_section,
                    "message": issue.get("summary") or "Avvik",
                    "what_to_change": issue.get("details") or issue.get("summary") or "Se forbedringsforslag.",
                    "example_fix": {
                        "good_example": example_fix,
                    },
                    "evidence": evidence,
                    "deduction": deduction_points,
                }
            )
            finding_ids_by_point.setdefault(point_id, []).append(finding_id)

        for deduction_idx, deduction in enumerate(deductions):
            if deduction_idx in used_deductions_by_point[point_id]:
                continue
            if not isinstance(deduction, dict):
                continue
            rule_id = deduction.get("rule_id") or "unknown"
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
            finding_id = f"f-{point_id}-d{deduction_idx + 1:03d}"
            point_key = point_meta.get("point_key") if isinstance(point_meta, dict) else None
            feedback_findings.append(
                {
                    "finding_id": finding_id,
                    "rule_id": rule_id,
                    "rule_family": rule_family,
                    "severity": severity,
                    "affects_96_gate": affects_96_gate,
                    "point_id": point_id,
                    "point_key": point_key or point_id,
                    "arkat_section": "annet",
                    "message": deduction.get("reason") or "Trekk registrert.",
                    "what_to_change": deduction.get("reason") or "Oppdater punktet for å fjerne trekket.",
                    "example_fix": {
                        "good_example": deduction.get("reason")
                        or "Oppdater punktet slik at kravet fremgår tydelig.",
                    },
                    "evidence": evidence,
                    "deduction": int(deduction.get("points", 0) or 0),
                }
            )
            finding_ids_by_point.setdefault(point_id, []).append(finding_id)

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

        # Roll up values from all detected points (mark FOUND even if no findings)
        for point_id in allowed_point_ids:
            if parent_id := child_to_parent.get(point_id):
                if parent_id in parent_found_status:
                    parent_found_status[parent_id] = "FOUND"
                    point_meta = point_lookup.get(point_id)
                    if point_meta and not parent_where[parent_id]:
                        parent_where[parent_id] = {"page": int(point_meta.get("page_start", 1))}
        # E3 heading fallback: some P11/P12 sections are detected as headings (not canonical child IDs).
        # If whitelist preserved such a heading with explicit parent hint, mark parent as FOUND.
        for point in points:
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

        not_found_text = ui_overlay_cfg.get("not_found_policy", {}).get("ui_text_nb", "Ikke funnet i rapport")
        points_overview = []
        for idx, p in enumerate(parent_cards):
            pid = p["canonical_id"]
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
                parent_summary = "Avvik funnet" if deduction > 0 else "OK"

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
            mapping_points.extend([p for p in points if isinstance(p, dict)])
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
                mapping_points.append(p)
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
                    "point_id": "GLOBAL",
                    "point_key": "GLOBAL",
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
                "point_id": "GLOBAL",
                "point_key": "GLOBAL",
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
            mapping_input_points.extend([p for p in points if isinstance(p, dict)])
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
                mapping_input_points.append(p)

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
            mapping_input_points.extend([p for p in points if isinstance(p, dict)])
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
                mapping_input_points.append(p)
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
    linked_tg3_count = 0
    for idx, f in enumerate(all_findings):
        if not isinstance(f, dict):
            continue
        is_tg3 = _is_tg3_related_finding(f)
        point_id = _parse_point_id_from_v16_finding(f)
        text_candidates: List[str] = []
        for key in ("message", "title"):
            value = f.get(key)
            if isinstance(value, str) and value.strip():
                text_candidates.append(value)
        for snip in (f.get("evidence_snippets") or []):
            if isinstance(snip, str) and snip.strip():
                text_candidates.append(snip)
        # Try text inference when point is missing OR parsed point is not part of runtime IDs
        # (common for v1.6 rule IDs ending in _001/_002).
        if (not point_id) or (point_id not in allowed_point_ids):
            for candidate in text_candidates:
                inferred = _infer_canonical_child_from_text(candidate, mapping_points)
                if inferred:
                    point_id = inferred
                    allowed_point_ids.add(inferred)
                    break
        point_is_linked = bool(point_id and point_id in allowed_point_ids and _is_canonical_child_point_id(point_id))
        if is_tg3 and not point_is_linked:
            # Never apply TG3 deductions without clear segment linkage.
            continue
        if not point_is_linked:
            # Tight fallback: unresolved findings are ignored instead of becoming GLOBAL.
            continue
        if is_tg3:
            linked_tg3_count += 1
        rid = f.get("finding_id") or f.get("rule_id") or f"v16-{idx}"
        if isinstance(f.get("gate_effect"), dict) and f.get("gate_effect", {}).get("blocks_96_gate") and rid not in blocked_by:
            blocked_by.append(rid)
        band = (f.get("deduction_band") or "").strip()
        deduction_pts = band_to_deduction.get(band, 0)
        deduction_totals[point_id] = deduction_totals.get(point_id, 0) + deduction_pts
        finding_id = f"f-v16-{point_id}-{idx + 1:03d}"
        snips = f.get("evidence_snippets") or []
        snippet = (snips[0] if snips and isinstance(snips[0], str) else "") or (f.get("message") or "Ingen utdrag.")
        feedback_findings.append({
            "finding_id": finding_id,
            "rule_id": rid,
            "rule_family": (rid.split(".")[0] if "." in str(rid) else "UNKNOWN"),
            "severity": "high" if f.get("severity") == "major" else "medium" if f.get("severity") == "minor" else "low",
            "affects_96_gate": bool(isinstance(f.get("gate_effect"), dict) and f.get("gate_effect", {}).get("blocks_96_gate")),
            "point_id": point_id,
            "point_key": point_lookup.get(point_id, {}).get("point_key") or point_id,
            "arkat_section": "annet",
            "message": f.get("title") or f.get("message") or "Avvik",
            "what_to_change": f.get("recommended_fix_text") or f.get("message") or "Se forbedringsforslag.",
            "example_fix": {"good_example": f.get("recommended_fix_text") or f.get("message") or ""},
            "evidence": {"page": 1, "snippet": snippet[:500] if snippet else "Ikke tilgjengelig.", "match": "From all_findings."},
            "deduction": deduction_pts,
        })
        finding_ids_by_point.setdefault(point_id, []).append(finding_id)

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
        parent_finding_ids = {p["canonical_id"]: [] for p in parent_cards}
        parent_worst_tg = {p["canonical_id"]: "" for p in parent_cards}
        parent_found_status = {p["canonical_id"]: "NOT_FOUND_IN_REPORT" for p in parent_cards}
        parent_where = {p["canonical_id"]: {} for p in parent_cards}
        e3_presence = _detect_e3_p11_p12_presence(points, points_before_whitelist)
        
        # Roll up values from all detected points (mark FOUND even if no findings)
        for point_id in allowed_point_ids:
            if parent_id := child_to_parent.get(point_id):
                if parent_id in parent_found_status:
                    parent_found_status[parent_id] = "FOUND"
                    point_meta = point_lookup.get(point_id)
                    if point_meta and not parent_where[parent_id]:
                        parent_where[parent_id] = {"page": int(point_meta.get("page_start", 1))}
        # E3 heading fallback: ensure P11/P12 become FOUND when preserved heading exists
        # but no canonical child ID was linked for that section.
        for point in points:
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
                        
        not_found_text = ui_overlay_cfg.get("not_found_policy", {}).get("ui_text_nb", "Ikke funnet i rapport")
        points_overview = []
        for idx, p in enumerate(parent_cards):
            pid = p["canonical_id"]
            if pid == "P11_LAWFULNESS_AND_SAFETY" and e3_presence.get("P11"):
                parent_found_status[pid] = "FOUND"
            if pid == "P12_SUPPLEMENTARY_INFORMATION" and e3_presence.get("P12"):
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
                parent_summary = "Avvik funnet" if deduction > 0 else "OK"

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
        mapping_points: List[Dict[str, object]] = []
        if isinstance(points, list):
            mapping_points.extend([p for p in points if isinstance(p, dict)])
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
                mapping_points.append(p)
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
    _polish_feedback_findings(feedback_findings)
    return {
        "version": "v1.1",
        "report_id": str(report_id) if report_id else "unknown_report",
        "document_hash": document_hash or "unknown_hash",
        "ordering": {"mode": mode, "dedupe_key": dedupe_key, "source": "detected_points", "note": ordering_note},
        "score": {
            "total": score_total,
            "category_deductions": [
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


def build_feedback_v11(
    analysis_output: Dict[str, object],
    detected_points_payload: Dict[str, object],
    report_id: Optional[str],
    document_hash: Optional[str],
) -> Dict[str, object]:
    return _build_feedback_v11(analysis_output, detected_points_payload, report_id, document_hash)


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

    if not has_deductions:
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


def postprocess_analysis_output(analysis_output: Dict[str, object], report_text: str) -> Dict[str, object]:
    """
    Single source of truth for post-LLM validation/normalization.
    Applies cost false-positive filtering and per-segment ARK/ARKAT checks on merged text.
    """
    if not isinstance(analysis_output, dict):
        return analysis_output
    _ensure_required_arrays(analysis_output)
    detected_points = _extract_detected_points(report_text or "")
    detected_points = _validate_detected_points_against_whitelist(detected_points)
    detected_points = _merge_detected_points_with_linked_summary(detected_points, report_text or "")
    _filter_tg3_cost_missing_false_positives(report_text, analysis_output, detected_points)
    _drop_tg_and_consequence_false_positives(report_text, analysis_output, detected_points)
    _ensure_issue_evidence(analysis_output, report_text)
    _ensure_driver_evidence(analysis_output)
    _normalize_scoring_output(analysis_output)
    _run_ark_arkat_per_segment_validation(report_text, detected_points, analysis_output)
    _drop_arkat_false_positives(analysis_output)
    _drop_segment_arkat_for_tg2_only_points(analysis_output)
    _drop_tg2_tiltak_requirement_false_positives(analysis_output)
    _ensure_finding_suggestions_differentiated(analysis_output)
    _drop_tg3_cost_top_issues_if_segments_have_cost(analysis_output) 
    _polish_analysis_text_fields(analysis_output)
    _sanitize_analysis_output_text(analysis_output)
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
    return {
        "document_hash": document_hash,
        "score_total": analysis_output.get("score_total", 0),
        "score_by_category": analysis_output.get("score_by_category", []),
        "deductions": deductions_export,
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

    export_items = [
        ("run_metadata.json", run_metadata),
        ("detected_points.json", detected_points_export),
        ("scoring_result.json", scoring_result_export),
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
            if text_tokens > available_tokens:
                incomplete_reasons.append("input_truncated")

            detected_points_payload = get_validated_detected_points_payload(
                normalized_text,
                document_hash=document_hash,
                document_title=document_title,
                document_id=document_id,
                pdf_metadata=pdf_metadata,
            )
            detected_points = detected_points_payload.get("points", []) if isinstance(detected_points_payload, dict) else []
            _log_debug(
                run_id,
                "preflight",
                {
                    "document_hash": document_hash,
                    "text_chars": len(normalized_text),
                    "text_tokens_est": text_tokens,
                    "available_tokens_est": available_tokens,
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
            postprocess_analysis_output(analysis_output, normalized_text)
            meta = analysis_output.get("meta", {})
            if isinstance(meta, dict):
                meta.setdefault("scoring_model_id", scoring_model_info.get("model_id", ""))
                meta.setdefault("scoring_model_version", scoring_model_info.get("version", ""))
                meta.setdefault("scoring_model_updated_at", scoring_model_info.get("updated_at", ""))
                analysis_output["meta"] = meta
            if settings.USE_AWS_BEDROCK:
                seed_used = None
            else:
                seed_used = settings.OPENAI_SEED
                model_name = model

            run_meta = dict(run_meta_base)
            run_meta["model_name"] = model_name
            run_meta["seed"] = seed_used
            logger.info("Detected %s points before scoring", len(detected_points))

            scoring_result_payload = {
                "run_meta": run_meta,
                "analysis_output": analysis_output,
                "feedback_v11": _build_feedback_v11(
                    analysis_output,
                    detected_points_payload,
                    report_id=document_id,
                    document_hash=document_hash,
                ),
            }

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
