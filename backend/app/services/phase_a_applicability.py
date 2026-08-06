"""Deterministic, regime-independent assessment applicability planning."""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Iterable

from app.services.phase_a_contracts import ApplicabilityPlanItem, RuleCategory, SegmentKind, ValidatedSegment


def _id(segment_id: str, category: RuleCategory) -> str:
    digest = hashlib.sha256(f"{segment_id}|{category.value}".encode()).hexdigest()[:24]
    return f"plan_{digest}"


def _normal(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


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
            tg = (segment.tg_grade or "").upper()
            context = _normal(" ".join((segment.kind.value, segment.title, segment.professional_subject)))
            categories: list[tuple[RuleCategory, list[str]]] = []
            if tg in {"TG2", "TG3"}:
                categories.extend((category, [f"grade_{tg.lower()}", "locked_arkat_structure"]) for category in self.ARKAT)
            if tg == "TG3":
                categories.append((RuleCategory.TG3_COST, ["grade_tg3", "point_bound_cost_required"]))
            if tg == "TGIU":
                categories.append((RuleCategory.METHODOLOGY, ["grade_tgiu"]))
            if segment.kind == SegmentKind.METHODOLOGY or any(term in context for term in ("metod", "undersok", "tilgjengelig")):
                categories.append((RuleCategory.METHODOLOGY, ["methodology_context"]))
            if segment.kind == SegmentKind.LEGALITY or any(term in context for term in ("lovlighet", "ferdigattest", "bruksendring", "tegning")):
                categories.append((RuleCategory.LEGALITY, ["legality_context"]))
            if any(term in context for term in ("elektr", "hms", "sikkerhet")):
                categories.append((RuleCategory.METHODOLOGY, ["electrical_or_hms_context"]))
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
