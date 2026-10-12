"""Deterministic, regime-independent assessment applicability planning."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable

from app.services.phase_a_contracts import (
    ApplicabilityPlanItem,
    RuleCategory,
    SegmentKind,
    ValidatedSegment,
    ValidationStatus,
)


def _id(segment_id: str, category: RuleCategory) -> str:
    digest = hashlib.sha256(f"{segment_id}|{category.value}".encode()).hexdigest()[:24]
    return f"plan_{digest}"


def _normal(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _is_aggregate_container(segment: ValidatedSegment) -> bool:
    if segment.kind != SegmentKind.REPORT_POINT:
        return False
    body = "\n".join(span.exact_quote for span in segment.bound_body_spans)
    markers = re.findall(r"(?m)^\d+\.\s+Avvik/Årsak:", body)
    return len(markers) >= 2


def _has_traceable_assessment_coverage(
    segment: ValidatedSegment,
    segments_by_id: dict[str, ValidatedSegment],
) -> bool:
    primary_id = segment.supporting_primary_segment_id
    if not primary_id:
        return False
    primary = segments_by_id.get(primary_id)
    if primary is None or primary.validation_status != ValidationStatus.VALIDATED:
        return False
    if primary.supporting_primary_segment_id:
        return False
    if primary.kind == SegmentKind.REPORT_POINT:
        tg = (primary.tg_grade or "").upper()
        eligible = (
            (primary.point_type == "graded" and tg in {"TG2", "TG3"})
            or primary.point_type in {
                "tgiu", "electrical_no_tg", "hms_no_tg", "methodology_only", "legality_no_tg",
            }
        )
    else:
        eligible = primary.kind in {SegmentKind.METHODOLOGY, SegmentKind.LEGALITY, SegmentKind.SECTION}
    if not eligible:
        return False

    covered_spans = primary.bound_body_spans or primary.evidence_spans
    required_spans = segment.bound_body_spans or segment.evidence_spans
    if not covered_spans or not required_spans:
        return False

    def span_is_covered(required) -> bool:
        for covered in covered_spans:
            if required.page != covered.page:
                continue
            if not (covered.char_start <= required.char_start and required.char_end <= covered.char_end):
                continue
            offset = required.char_start - covered.char_start
            if covered.exact_quote[offset:offset + len(required.exact_quote)] == required.exact_quote:
                return True
        return False

    if all(span_is_covered(required) for required in required_spans):
        return True

    required_body = "\n".join(span.exact_quote for span in required_spans)
    covered_body = "\n".join(span.exact_quote for span in covered_spans)
    return _normal(required_body).strip() == _normal(covered_body).strip()


class DeterministicApplicabilityPlanner:
    """Select categories from structural facts, never from model judgement."""

    ARKAT = (
        RuleCategory.AARSAK,
        RuleCategory.RISIKO,
        RuleCategory.KONSEKVENS,
        RuleCategory.ANBEFALT_TILTAK,
    )

    def plan(self, segments: Iterable[ValidatedSegment]) -> list[ApplicabilityPlanItem]:
        segments = list(segments)
        segments_by_id = {segment.segment_id: segment for segment in segments}
        output: list[ApplicabilityPlanItem] = []
        for segment in segments:
            if segment.validation_status != ValidationStatus.VALIDATED:
                continue
            if segment.supporting_primary_segment_id:
                if _has_traceable_assessment_coverage(segment, segments_by_id):
                    # Explicit supporting context is deduplicated only when its
                    # linked primary is itself eligible for governed assessment.
                    continue
            tg = (segment.tg_grade or "").upper()
            categories: list[tuple[RuleCategory, list[str]]] = []
            if segment.kind == SegmentKind.REPORT_POINT:
                # Validated physical type and section context are authoritative.
                if _is_aggregate_container(segment) and _has_traceable_assessment_coverage(
                    segment, segments_by_id
                ):
                    continue
                if segment.point_type == "graded" and tg in {"TG2", "TG3"}:
                    categories.extend(
                        (category, [f"grade_{tg.lower()}", "validated_physical_type", "locked_arkat_structure"])
                        for category in self.ARKAT
                    ) 
                    if tg == "TG3":
                        categories.append((RuleCategory.TG3_COST, [
                            "grade_tg3", "validated_physical_type", "point_bound_cost_required",
                        ]))
                elif segment.point_type == "tgiu":
                    categories.append((RuleCategory.METHODOLOGY, ["validated_point_type_tgiu"]))
                elif segment.point_type in {"electrical_no_tg", "hms_no_tg", "methodology_only"}:
                    categories.append((RuleCategory.METHODOLOGY, [
                        f"validated_point_type_{segment.point_type}", "validated_section_context",
                    ]))
                elif segment.point_type == "legality_no_tg":
                    categories.append((RuleCategory.LEGALITY, [
                        "validated_point_type_legality_no_tg", "validated_section_context",
                    ]))
            else:
                # Standalone AI sections are eligible only when they are not
                # linked to a physical object above.
                if segment.kind == SegmentKind.METHODOLOGY:
                    categories.append((RuleCategory.METHODOLOGY, ["standalone_methodology_section"]))
                if segment.kind == SegmentKind.LEGALITY:
                    categories.append((RuleCategory.LEGALITY, ["standalone_legality_section"]))
                if segment.kind == SegmentKind.SECTION and any(
                    term in _normal(segment.section_context) for term in ("elektr", "hms", "sikkerhet")
                ):
                    categories.append((RuleCategory.METHODOLOGY, ["validated_section_context"]))
            # A structurally identified no-TG legality/methodology section is still
            # assessed, but ordinary TG0/TG1 report points are not sent to ARKAT.
            seen: set[RuleCategory] = set()
            for category, reasons in categories:
                if category in seen:
                    continue
                seen.add(category)
                output.append(ApplicabilityPlanItem(
                    plan_item_id=_id(segment.segment_id, category),
                    segment_id=segment.segment_id,
                    rule_category=category,
                    required=True,
                    reason_codes=reasons,
                ))
        return output
