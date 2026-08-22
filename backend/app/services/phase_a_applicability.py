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


class DeterministicApplicabilityPlanner:
    """Select categories from structural facts, never from model judgement."""

    ARKAT = (
        RuleCategory.AARSAK,
        RuleCategory.RISIKO,
        RuleCategory.KONSEKVENS,
        RuleCategory.ANBEFALT_TILTAK,
    )

    def plan(self, segments: Iterable[ValidatedSegment]) -> list[ApplicabilityPlanItem]:
        output: list[ApplicabilityPlanItem] = []
        for segment in segments:
            if segment.validation_status != ValidationStatus.VALIDATED:
                continue
            if segment.supporting_primary_segment_id:
                # Explicit supporting context is never independently assessed.
                continue
            tg = (segment.tg_grade or "").upper()
            categories: list[tuple[RuleCategory, list[str]]] = []
            if segment.kind == SegmentKind.REPORT_POINT:
                # Validated physical type and section context are authoritative.
                if _is_aggregate_container(segment):
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
