"""Deterministic scoring for the isolated Phase A shadow candidate."""

from __future__ import annotations

from app.services.phase_a_contracts import (
    FindingAdmission,
    FindingValidationDecision,
    PhaseAScoreCategory,
    PhaseAScoreResult,
)


CATEGORY_CAPS = {"A": 40, "B": 20, "C": 15, "D": 15, "E": 10, "F": 20}


def score_admitted_findings(
    decisions: list[FindingValidationDecision],
    *,
    score_valid: bool = True,
) -> PhaseAScoreResult:
    raw = {category: 0 for category in CATEGORY_CAPS}
    gate_blocked = False
    seen: set[str] = set()
    for decision in decisions:
        if decision.admission != FindingAdmission.ACCEPTED or not decision.accepted_finding_id:
            continue
        if decision.accepted_finding_id in seen:
            continue
        seen.add(decision.accepted_finding_id)
        if decision.category:
            raw[decision.category] += decision.deduction
        gate_blocked = gate_blocked or decision.blocks_96_gate
    categories = [
        PhaseAScoreCategory(
            category=category,
            raw_deduction=raw[category],
            capped_deduction=min(raw[category], cap),
            cap=cap,
        )
        for category, cap in CATEGORY_CAPS.items()
    ]
    total = sum(item.capped_deduction for item in categories)
    return PhaseAScoreResult(
        categories=categories,
        total_deduction=total,
        score=max(0, 100 - total),
        gate_blocked=gate_blocked,
        score_valid=score_valid,
    )
