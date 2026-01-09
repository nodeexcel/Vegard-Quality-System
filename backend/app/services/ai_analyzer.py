from openai import OpenAI
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
import json
import logging
import re
import hashlib
import uuid
from pathlib import Path
from app.config import settings
from app.schemas import AnalysisResult, ComponentBase, FindingBase
from app.services.system_prompt import SYSTEM_PROMPT
from app.services.validert_files import build_prompt_context, get_prompt_context_sha, get_scoring_model_info, get_scoring_model_text

logger = logging.getLogger(__name__)

PAGE_MARKER_RE = re.compile(r"\[SIDE (\d+)\]\n", re.IGNORECASE)
SUMMARY_MARKERS = ["oppsummering", "takstmannens vurdering", "summary"]
POINT_HEADER_RE = re.compile(r"^\s*(\d+(?:\.\d+){1,4})\s+(.*\S)?$")
TG_RE = re.compile(r"\bTG(?:0|1|2|3|IU)\b")
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


def _extract_detected_points(report_text: str) -> List[Dict[str, object]]:
    pages = _split_pages(report_text)
    line_index: List[Dict[str, object]] = []
    for page in pages:
        for line in page["text"].splitlines():
            line_index.append({"page": page["page"], "text": line})

    headings: List[Dict[str, object]] = []
    for idx, line in enumerate(line_index):
        match = POINT_HEADER_RE.match(line["text"])
        if match:
            headings.append(
                {
                    "idx": idx,
                    "point_id": match.group(1),
                    "section_title": (match.group(2) or "").strip(),
                }
            )

    detected: List[Dict[str, object]] = []
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
            }
        )
    return detected


def _build_detected_points_payload(
    detected_points: List[Dict[str, object]],
    document_hash: str,
    document_title: Optional[str],
    document_id: Optional[str],
    pdf_metadata: Optional[Dict[str, object]],
) -> Dict[str, object]:
    page_count = 1
    if isinstance(pdf_metadata, dict):
        page_count = int(pdf_metadata.get("total_pages") or pdf_metadata.get("pages_with_text") or 1)
    source_filename = document_title or (f"report_{document_id}.pdf" if document_id else "report.pdf")
    return {
        "version": "v1.2",
        "document": {
            "document_hash": document_hash,
            "source_filename": source_filename,
            "page_count": max(page_count, 1),
            "extraction": {
                "engine": "validert-point-detector",
                "engine_version": "1.0.0",
                "notes": "Point headers detected via regex on extracted PDF text.",
            },
        },
        "points": detected_points,
    }


def _is_numeric_point_id(value: str) -> bool:
    if not value:
        return False
    return bool(re.match(r"^\d+(?:\.\d+)*$", value))


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
    mode = _detect_sort_mode(points)
    if mode == "NUMERIC":
        unique_points = _dedupe_points(points, "numeric_id")
        def _cmp(a: Dict[str, object], b: Dict[str, object]) -> int:
            a_id = _numeric_id_for_point(a)
            b_id = _numeric_id_for_point(b)
            if not a_id and not b_id:
                return 0
            if not a_id:
                return 1
            if not b_id:
                return -1
            return _compare_numeric_ids(a_id, b_id)
        from functools import cmp_to_key
        sorted_points = sorted(unique_points, key=cmp_to_key(_cmp))
        return mode, "numeric_id", sorted_points
    unique_points = _dedupe_points(points, "point_key")
    if all(isinstance(p, dict) and p.get("order_in_doc") is not None for p in unique_points):
        sorted_points = sorted(
            unique_points,
            key=lambda p: int(p.get("order_in_doc") or 0),
        )
    else:
        sorted_points = sorted(
            unique_points,
            key=lambda p: int(p.get("page_start") or 0),
        )
    return mode, "point_key", sorted_points


def _derive_rule_family(rule_id: str) -> str:
    if not rule_id:
        return ""
    if "." in rule_id:
        return rule_id.split(".", 1)[0]
    if "_" in rule_id:
        return rule_id.split("_", 1)[0]
    return rule_id


def _build_feedback_v11(
    analysis_output: Dict[str, object],
    detected_points_payload: Dict[str, object],
    report_id: Optional[str],
    document_hash: Optional[str],
) -> Dict[str, object]:
    points = detected_points_payload.get("points", []) if isinstance(detected_points_payload, dict) else []
    allowed_point_ids = set()
    point_lookup: Dict[str, Dict[str, object]] = {}
    for point in points:
        if not isinstance(point, dict):
            continue
        for key in (
            point.get("point_id"),
            point.get("numeric_id"),
            point.get("point_key"),
            point.get("native_label"),
        ):
            if isinstance(key, str) and key:
                allowed_point_ids.add(key)
                point_lookup.setdefault(key, point)

    score_total = analysis_output.get("score_total", 0)
    score_by_category = analysis_output.get("score_by_category", [])
    top_score_drivers = analysis_output.get("top_score_drivers", [])

    feedback_findings: List[Dict[str, object]] = []
    finding_ids_by_point: Dict[str, List[str]] = {}
    deduction_totals: Dict[str, int] = {}

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
        issues = component.get("issues", []) if isinstance(component.get("issues"), list) else []
        point_meta = point_lookup.get(point_id, {})
        for issue_idx, issue in enumerate(issues):
            if not isinstance(issue, dict):
                continue
            rule_refs = issue.get("rule_refs", []) if isinstance(issue.get("rule_refs"), list) else []
            rule_id = rule_refs[0] if rule_refs else "unknown"
            severity = issue.get("severity", "medium")
            evidence_items = issue.get("evidence", []) if isinstance(issue.get("evidence"), list) else []
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
                    "snippet": point_meta.get("excerpt") or issue.get("details") or issue.get("summary") or "",
                    "match": "Derived from point header excerpt.",
                }
            if not evidence.get("snippet"):
                evidence["snippet"] = "Ikke tilgjengelig."

            finding_id = f"f-{point_id}-{issue_idx + 1:03d}"
            point_key = point_meta.get("point_key") if isinstance(point_meta, dict) else None
            feedback_findings.append(
                {
                    "finding_id": finding_id,
                    "rule_id": rule_id,
                    "rule_family": _derive_rule_family(rule_id),
                    "severity": severity,
                    "affects_96_gate": False,
                    "point_id": point_id,
                    "point_key": point_key or point_id,
                    "arkat_section": "annet",
                    "message": issue.get("summary") or "Avvik",
                    "what_to_change": issue.get("details") or issue.get("summary") or "Se forbedringsforslag.",
                    "example_fix": {
                        "good_example": issue.get("details") or issue.get("summary") or "Se forbedringsforslag.",
                    },
                    "evidence": evidence,
                    "deduction": next(
                        (
                            d.get("points", 0)
                            for d in deductions
                            if isinstance(d, dict) and d.get("rule_id") == rule_id
                        ),
                        0,
                    ),
                }
            )
            finding_ids_by_point.setdefault(point_id, []).append(finding_id)

    mode, dedupe_key, sorted_points = _sort_points(points)
    ordering_note = "Sortert numerisk (parent før child)." if mode == "NUMERIC" else "Sortert etter dokumentrekkefølge."

    points_overview: List[Dict[str, object]] = []
    display_index = 1
    for point in sorted_points:
        if not isinstance(point, dict):
            continue
        kind = point.get("kind")
        if isinstance(kind, str) and kind not in ("point", "subpoint"):
            continue
        point_id = point.get("point_id") or point.get("numeric_id") or point.get("native_label") or ""
        point_key = point.get("point_key") or point_id
        component = next(
            (
                c
                for c in analysis_output.get("findings", [])
                if isinstance(c, dict) and c.get("component_id") == point_id
            ),
            None,
        )
        issues = component.get("issues", []) if isinstance(component, dict) else []
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
        points_overview.append(
            {
                "display_index": display_index,
                "point_id": point_id,
                "point_key": point_key,
                "native_label": point.get("native_label") or point_id or point_key or "Ukjent",
                "numeric_id": point.get("numeric_id") or (_numeric_id_for_point(point) or None),
                "native_path": point.get("native_path"),
                "title": point.get("title") or "Ukjent",
                "tg": tg_value,
                "status": status,
                "summary": summary,
                "deduction_total": max(deduction_total, 0),
                "finding_ids": finding_ids_by_point.get(point_id, []),
                "where": where,
            }
        )
        display_index += 1

    return {
        "version": "v1.1",
        "report_id": str(report_id) if report_id else "unknown_report",
        "document_hash": document_hash or "unknown_hash",
        "ordering": {
            "mode": mode,
            "dedupe_key": dedupe_key,
            "source": "detected_points",
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
            "blocked_96": False,
            "blocked_by": [],
        },
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
    return prefix if prefix in {"A", "B", "C", "D", "E"} else ""


def _normalize_scoring_output(analysis_output: Dict[str, object]) -> Dict[str, object]:
    scoring_model = _load_scoring_model()
    category_caps = scoring_model["category_caps"]
    category_names = scoring_model["category_names"]
    category_order = scoring_model["category_order"]
    deduct_per_occurrence = scoring_model["deduct_per_occurrence"]
    aggregate_level = str(scoring_model.get("aggregate_level", "bygningsdel")).lower()

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
    score_start = scoring_model["score_start"]
    score_floor = scoring_model["score_floor"]
    score_ceiling = scoring_model["score_ceiling"]
    score_total = int(max(score_floor, min(score_ceiling, score_start - total_deduction)))
    analysis_output["score_total"] = score_total
    return analysis_output


def normalize_scoring_output(analysis_output: Dict[str, object]) -> Dict[str, object]:
    return _normalize_scoring_output(analysis_output)


def ensure_analysis_evidence(analysis_output: Dict[str, object], report_text: str) -> None:
    _ensure_issue_evidence(analysis_output, report_text)
    _ensure_driver_evidence(analysis_output)


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
                if "[PDF METADATA]" in text:
                    metadata_section = text.split("[PDF METADATA]")[1].split("[START RAPPORTTEKST]")[0]
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
                document_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

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

            text_tokens = estimate_tokens(text)
            text_was_truncated = False
            if text_tokens > available_tokens:
                logger.warning("Text too long (%s tokens), truncating to fit within limit", text_tokens)
                text = truncate_text_smart(text, available_tokens)
                text_was_truncated = True

            truncation_note = ""
            if text_was_truncated:
                truncation_note = "\nMERK: Rapporttekst ble trunkert. Full dokumentanalyse er ikke mulig.\n"

            run_id = str(uuid.uuid4())
            scoring_model_info = get_scoring_model_info()
            detected_points = _extract_detected_points(text)
            detected_points_payload = _build_detected_points_payload(
                detected_points,
                document_hash=document_hash,
                document_title=document_title,
                document_id=document_id,
                pdf_metadata=pdf_metadata,
            )

            user_prompt = f"""
{context_info}

{prompt_context}
{truncation_note}

===== TILSTANDSRAPPORT SOM SKAL ANALYSERES =====

Analyser følgende norske tilstandsrapport.

VIKTIG: Du må analysere HELE dokumentet. Alle sider, vedlegg og bilder må vurderes.

Rapporttekst:
{text}

FORMATKRAV: Returner kompakt JSON (ingen innrykk/linjeskift). Begrens omfanget:
- findings: maks 25 (velg de viktigste, slå sammen når mulig)
- improvements: maks 15 (velg de viktigste)
- evidence per issue: maks 1 kort utdrag
Produser KUN gyldig JSON i henhold til OUTPUT SCHEMA. Ingen tekst utenfor JSON.
"""

            if settings.USE_AWS_BEDROCK:
                logger.info("Using AWS Bedrock Claude for analysis")
                from app.services.bedrock_ai import BedrockAI
                bedrock = BedrockAI(region=settings.AWS_REGION)
                analysis_output = bedrock.analyze_report_with_claude(user_prompt=user_prompt)
                model_name = "eu.anthropic.claude-sonnet-4-20250514-v1:0"
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
                    else:
                        raise

                response_text = response.choices[0].message.content.strip()
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1

                if json_start != -1 and json_end > json_start:
                    json_text = response_text[json_start:json_end]
                    analysis_output = json.loads(json_text)
                else:
                    raise ValueError("Could not find JSON in AI response")

            if not isinstance(analysis_output, dict):
                raise ValueError("AI output is not a JSON object")

            _ensure_meta_fields(analysis_output, document_title, document_id)
            _ensure_required_arrays(analysis_output)
            _ensure_issue_evidence(analysis_output, text)
            _ensure_driver_evidence(analysis_output)
            _normalize_scoring_output(analysis_output)
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

            run_meta = {
                "run_id": run_id,
                "analysis_timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "model_name": model_name,
                "temperature": 0.0,
                "top_p": 1.0,
                "seed": seed_used,
                "text_sha256": document_hash,
                "scoring_model": scoring_model_info,
                "pipeline_git_sha": f"{settings.PIPELINE_GIT_SHA}:{get_prompt_context_sha()}" if settings.PIPELINE_GIT_SHA else get_prompt_context_sha(),
            }
            logger.info("Detected %s points before scoring", len(detected_points))

            if text_was_truncated:
                meta = analysis_output.get("meta", {})
                meta["model_notes"] = "Rapporttekst ble trunkert - full dokumentanalyse ikke mulig"
                analysis_output["meta"] = meta

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

        except json.JSONDecodeError as e:
            logger.error("Failed to parse AI response as JSON: %s", str(e))
            raise Exception("Failed to parse AI analysis response")
        except Exception as e:
            logger.error("Error analyzing report with AI: %s", str(e), exc_info=True)
            raise Exception(f"AI analysis failed: {str(e)}")
