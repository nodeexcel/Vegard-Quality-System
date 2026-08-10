"""Governed Phase A regime resolution.

The approved resolver is used only by the isolated candidate manifest. The
pending resolver remains the default for every non-candidate caller.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

from app.services.phase_a_contracts import (
    RegimeResolution,
    RegimeResolutionStatus,
    RuleCategory,
    FactType,
    ValidatedDocumentFact,
    ValidationStatus,
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


class ApprovedGovernedRegimeResolver:
    """Implement VALIDERT-GRD-2026-02 without changing the v46 resolver default."""

    def _values(self, facts: Iterable[ValidatedDocumentFact], fact_type: FactType) -> list[ValidatedDocumentFact]:
        return [
            fact for fact in facts
            if fact.fact_type == fact_type
            and fact.validation_status == ValidationStatus.VALIDATED
            and fact.normalized_value
        ]

    @staticmethod
    def _parse(value: str | None) -> date | None:
        try:
            return date.fromisoformat(str(value or ""))
        except ValueError:
            return None

    @staticmethod
    def _edition(facts: Iterable[ValidatedDocumentFact]) -> tuple[str | None, list[str]]:
        editions: dict[str, list[str]] = {}
        for fact in facts:
            if fact.fact_type != FactType.DECLARED_STANDARD or fact.validation_status != ValidationStatus.VALIDATED:
                continue
            value = str(fact.normalized_value or fact.raw_value or "").replace(" ", "").upper()
            if "3600" not in value:
                continue
            if "2025" in value:
                editions.setdefault("NS 3600:2025", []).append(fact.fact_id)
            elif "2018" in value:
                editions.setdefault("NS 3600:2018", []).append(fact.fact_id)
        if len(editions) == 1:
            edition = next(iter(editions))
            return edition, editions[edition]
        return None, [item for values in editions.values() for item in values]

    def resolve(self, rule_category: RuleCategory, facts: Iterable[ValidatedDocumentFact]) -> RegimeResolution:
        facts = list(facts)
        report_facts = self._values(facts, FactType.REPORT_DATE) or self._values(facts, FactType.ISSUE_DATE)
        report_dates = [(fact, self._parse(fact.normalized_value)) for fact in report_facts]
        report_dates = [(fact, value) for fact, value in report_dates if value]
        edition, edition_fact_ids = self._edition(facts)
        controlling_ids = [fact.fact_id for fact, _ in report_dates] + edition_fact_ids
        obligation = (
            "standard_methodology"
            if rule_category in {RuleCategory.AARSAK, RuleCategory.RISIKO, RuleCategory.KONSEKVENS,
                                 RuleCategory.ANBEFALT_TILTAK, RuleCategory.METHODOLOGY, RuleCategory.TG3_COST}
            else "regulatory"
        )
        if len({value for _, value in report_dates}) != 1:
            return RegimeResolution(
                rule_category=rule_category,
                status=RegimeResolutionStatus.REQUIRES_CLARIFICATION,
                controlling_fact_ids=controlling_ids,
                explanation="Original report/issue date is missing or ambiguous; date-dependent rules fail closed.",
                obligation_class=obligation,
                controlling_fact_type="report_date",
                conflict_detail="missing_or_ambiguous_original_report_date",
            )
        report_date = report_dates[0][1]
        if report_date < date(2025, 12, 17):
            regime = "PRE_AMENDMENT"
            permitted = {"NS 3600:2018"}
        elif report_date < date(2026, 1, 1):
            regime = "TRANSITION_DEC_2025"
            permitted = {"NS 3600:2018", "NS 3600:2025"}
        elif report_date < date(2026, 7, 1):
            regime = "TRANSITION_2026"
            permitted = {"NS 3600:2018", "NS 3600:2025"}
        else:
            regime = "FULL_2026"
            permitted = {"NS 3600:2025"}
        if edition is None:
            if len(permitted) == 1:
                edition = next(iter(permitted))
            else:
                return RegimeResolution(
                    rule_category=rule_category,
                    status=RegimeResolutionStatus.REQUIRES_CLARIFICATION,
                    regime_id=regime,
                    controlling_fact_ids=controlling_ids,
                    explanation="NS edition is missing or ambiguous during a transition period; edition-specific rules abstain.",
                    obligation_class=obligation,
                    controlling_fact_type="declared_standard",
                    excluded_alternatives=sorted(permitted),
                    conflict_detail="missing_or_ambiguous_ns_edition",
                )
        conflict = edition not in permitted
        if conflict and regime == "FULL_2026":
            edition = "NS 3600:2025"
        elif conflict:
            return RegimeResolution(
                rule_category=rule_category,
                status=RegimeResolutionStatus.CONFLICT,
                regime_id=regime,
                controlling_fact_ids=controlling_ids,
                explanation="Declared edition conflicts with the permitted regime.",
                applicable_ns_edition=edition,
                obligation_class=obligation,
                controlling_fact_type="report_date_and_declared_standard",
                excluded_alternatives=sorted(permitted),
                conflict_detail="declared_edition_not_permitted",
            )
        return RegimeResolution(
            rule_category=rule_category,
            status=RegimeResolutionStatus.RESOLVED,
            regime_id=regime,
            controlling_fact_ids=controlling_ids,
            explanation=(
                f"Original report date {report_date.isoformat()} resolves {regime}; "
                f"applicable TG-methodology edition is {edition}."
                + (" A conflicting post-transition declaration was recorded." if conflict else "")
            ),
            applicable_ns_edition=edition,
            obligation_class=obligation,
            controlling_fact_type="report_date_and_declared_standard",
            excluded_alternatives=sorted(permitted - {edition}),
            conflict_detail="declared_ns2018_after_2026_07_01" if conflict else None,
        )

    def resolve_rule(
        self,
        rule_category: RuleCategory,
        rule_id: str,
        rule_content: dict,
        facts: Iterable[ValidatedDocumentFact],
    ) -> RegimeResolution:
        resolution = self.resolve(rule_category, facts)
        if resolution.status != RegimeResolutionStatus.RESOLVED:
            return resolution
        applies = rule_content.get("applies_when") if isinstance(rule_content, dict) else None
        required_edition = applies.get("applicable_ns_edition") if isinstance(applies, dict) else None
        if required_edition and resolution.applicable_ns_edition != required_edition:
            return resolution.model_copy(update={
                "status": RegimeResolutionStatus.NOT_APPLICABLE,
                "explanation": f"Rule {rule_id} is excluded for {resolution.applicable_ns_edition}.",
                "excluded_alternatives": [rule_id],
                "conflict_detail": "rule_not_applicable_to_resolved_edition",
            })
        return resolution
