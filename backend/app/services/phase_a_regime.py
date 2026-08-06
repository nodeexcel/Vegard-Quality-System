"""Phase A regime-resolution boundary.

The product owner has authorized the interface only. Substantive resolution must
remain pending until a signed governed regime decision document is delivered.
"""

from __future__ import annotations

from typing import Iterable

from app.services.phase_a_contracts import (
    RegimeResolution,
    RegimeResolutionStatus,
    RuleCategory,
    ValidatedDocumentFact,
)


class PendingGovernedRegimeResolver:
    """Fail closed without encoding any date/standard transition decision."""

    def resolve(
        self,
        rule_category: RuleCategory,
        facts: Iterable[ValidatedDocumentFact],
    ) -> RegimeResolution:
        # Materialize the iterable to prove the interface accepts validated facts,
        # but do not interpret them before the governed decision is approved.
        list(facts)
        return RegimeResolution(
            rule_category=rule_category,
            status=RegimeResolutionStatus.PENDING_GOVERNED_DECISION,
            regime_id=None,
            controlling_fact_ids=[],
            explanation=(
                "No substantive regime decision is authorized. Awaiting the "
                "signed governed regime decision document."
            ),
        )

    def resolve_rule(
        self,
        rule_category: RuleCategory,
        rule_id: str,
        rule_content: dict,
        facts: Iterable[ValidatedDocumentFact],
    ) -> RegimeResolution:
        # Preserve inspection/report/revision facts independently for the future
        # signed resolver. No precedence or transition date is inferred here.
        list(facts)
        return RegimeResolution(
            rule_category=rule_category,
            status=RegimeResolutionStatus.PENDING_GOVERNED_DECISION,
            regime_id=None,
            controlling_fact_ids=[],
            explanation=(
                f"Rule {rule_id} has no authorized per-rule regime decision. "
                "Inspection, report and revision dates remain separate inputs."
            ),
        )
