"""Evidence-bound Phase A4 assessment and deterministic admission.

The service is internal/shadow-only. With the production default pending regime
resolver it cannot invoke a model or admit a finding.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Protocol

from app.services.phase_a_contracts import (
    Abstention,
    AnalysisState,
    AssessmentCandidate,
    AssessmentDecision,
    DocumentUnderstandingResult,
    FindingAdmission,
    FindingLineageRecord,
    FindingValidationDecision,
    PhaseA4Result,
    RegimeResolutionStatus,
    RuleCategory,
    RuleRetrievalRecord,
    StructuredAssessment,
    TraceRecord,
    ValidatedSegment,
    ValidationStatus,
)
from app.services.phase_a_governed_retrieval import ManifestVerifiedRuleRetriever
from app.services.phase_a_applicability import DeterministicApplicabilityPlanner


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _identifier(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:24]}"


class AssessmentModel(Protocol):
    def assess(
        self,
        segment: ValidatedSegment,
        category: RuleCategory,
        rules: list[RuleRetrievalRecord],
    ) -> AssessmentCandidate: ...


class BedrockSemanticAssessmentModel:
    """JSON-only semantic assessor; invoked only after regime resolution."""

    SYSTEM_PROMPT = """You assess one bound point from a Norwegian condition report.
Use only the complete point body, its explicit source evidence, and the retrieved governed rules.
Assess meaning, not the presence or absence of headings. For TG2/TG3, Årsak, Risiko,
Konsekvens and recommended/necessary measures may be expressed in prose without an
'ANBEFALT TILTAK' label. Do not use text from another point to satisfy this point.
For TG3 cost, only a cost class/interval or other schematic estimate explicitly bound
to this point (or an explicitly linked same-point summary supplied as evidence) counts.
If evidence or applicability is uncertain, abstain. Return JSON only."""

    def __init__(self, bedrock_client: Any | None = None, max_tokens: int = 3000):
        self._client = bedrock_client
        self.max_tokens = max_tokens

    def _bedrock(self):
        if self._client is None:
            from app.config import settings
            from app.services.bedrock_ai import BedrockAI

            self._client = BedrockAI(region=settings.AWS_REGION)
        return self._client

    def assess(
        self,
        segment: ValidatedSegment,
        category: RuleCategory,
        rules: list[RuleRetrievalRecord],
    ) -> AssessmentCandidate:
        if segment.kind.value == "report_point" and not segment.bound_body_spans:
            raise ValueError("complete bound report-point body is required")
        source_spans = segment.bound_body_spans or segment.evidence_spans
        if not source_spans and segment.evidence is not None:
            source_spans = [segment.evidence]
        prompt = {
            "segment": {
                "segment_id": segment.segment_id,
                "kind": segment.kind.value,
                "title": segment.title,
                "point_label": segment.point_label,
                "tg_grade": segment.tg_grade,
                "professional_subject": segment.professional_subject,
                "complete_bound_body": [span.model_dump(mode="json") for span in source_spans],
            },
            "rule_category": category.value,
            "retrieved_rules": [record.model_dump(mode="json") for record in rules],
            "required_output_schema": AssessmentCandidate.model_json_schema(),
        }
        payload = self._bedrock().generate_json_with_claude(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=json.dumps(prompt, ensure_ascii=False, sort_keys=True),
            max_tokens=self.max_tokens,
            retry_json_prompt=True,
        )
        return AssessmentCandidate.model_validate(payload)


def _governed_finding_types(records: Iterable[RuleRetrievalRecord]) -> set[str]:
    values: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"id", "rule_id", "error_type", "error_type_if_wrong"} and isinstance(child, str):
                    values.add(child)
                    # Some governed fields contain comma-separated alternatives.
                    values.update(part.strip(" .") for part in child.replace(" or ", ",").split(",") if part.strip())
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for record in records:
        values.add(record.rule_id)
        walk(record.content)
    return values


class DeterministicAssessmentValidator:
    def validate(
        self,
        assessment: StructuredAssessment,
        segment: ValidatedSegment,
        rules: list[RuleRetrievalRecord],
        regime_status: RegimeResolutionStatus,
    ) -> FindingValidationDecision:
        reasons: list[str] = []
        rule_ids = {record.retrieval_id for record in rules}
        evidence_ids = {span.evidence_id for span in segment.evidence_spans}
        evidence_ids.update(span.evidence_id for span in segment.bound_body_spans)
        if segment.evidence is not None:
            evidence_ids.add(segment.evidence.evidence_id)
        if regime_status != RegimeResolutionStatus.RESOLVED:
            reasons.append("regime_not_resolved")
        if any(record.regime_status != RegimeResolutionStatus.RESOLVED for record in rules):
            reasons.append("retrieved_rule_regime_not_resolved")
        if assessment.segment_id != segment.segment_id:
            reasons.append("segment_reference_mismatch")
        if any(record.segment_id != segment.segment_id for record in rules):
            reasons.append("retrieval_segment_mismatch")
        if any(record.rule_category != assessment.rule_category for record in rules):
            reasons.append("retrieval_category_mismatch")
        if not set(assessment.retrieval_ids).issubset(rule_ids):
            reasons.append("unknown_retrieval_reference")
        if not set(assessment.evidence_ids).issubset(evidence_ids):
            reasons.append("unknown_evidence_reference")
        if assessment.decision == AssessmentDecision.DEFICIENT:
            if segment.kind.value == "report_point" and not segment.bound_body_spans:
                reasons.append("complete_point_body_missing")
            if not assessment.evidence_ids:
                reasons.append("finding_without_source_evidence")
            if not assessment.proposed_finding_type:
                reasons.append("finding_type_missing")
            elif assessment.proposed_finding_type not in _governed_finding_types(rules):
                reasons.append("finding_type_not_governed_by_retrieved_rules")
        else:
            reasons.append("no_finding_proposed")
        admission = FindingAdmission.ACCEPTED if not reasons else FindingAdmission.REJECTED
        accepted_id = (
            _identifier(
                "finding",
                segment.segment_id,
                assessment.rule_category.value,
                assessment.proposed_finding_type or "",
                *sorted(assessment.evidence_ids),
                *sorted(assessment.retrieval_ids),
            )
            if admission == FindingAdmission.ACCEPTED
            else None
        )
        return FindingValidationDecision(
            validation_id=_identifier("val", assessment.assessment_id),
            assessment_id=assessment.assessment_id,
            admission=admission,
            reason_codes=reasons,
            accepted_finding_id=accepted_id,
        )


class PhaseA4ShadowService:
    def __init__(
        self,
        retriever: ManifestVerifiedRuleRetriever,
        model: AssessmentModel,
        validator: DeterministicAssessmentValidator | None = None,
        planner: DeterministicApplicabilityPlanner | None = None,
    ):
        self.retriever = retriever
        self.model = model
        self.validator = validator or DeterministicAssessmentValidator()
        self.planner = planner or DeterministicApplicabilityPlanner()

    def analyze(
        self,
        understanding: DocumentUnderstandingResult,
        categories: Iterable[RuleCategory] | None = None,
    ) -> PhaseA4Result:
        retrieval_results = []
        assessments: list[StructuredAssessment] = []
        decisions: list[FindingValidationDecision] = []
        lineage: list[FindingLineageRecord] = []
        abstentions = list(understanding.abstentions)
        traces = list(understanding.trace_records)
        category_filter = set(categories) if categories is not None else None
        plan = self.planner.plan(understanding.segments)
        if category_filter is not None:
            plan = [item for item in plan if item.rule_category in category_filter]
        plan_by_segment: dict[str, list] = {}
        for item in plan:
            plan_by_segment.setdefault(item.segment_id, []).append(item)

        for segment in understanding.segments:
            if segment.validation_status != ValidationStatus.VALIDATED:
                continue
            for plan_item in plan_by_segment.get(segment.segment_id, []):
                category = plan_item.rule_category
                retrieval = self.retriever.retrieve(
                    segment,
                    category,
                    understanding.facts,
                    document_hash=understanding.document_hash,
                )
                retrieval_results.append(retrieval)
                abstentions.extend(retrieval.abstentions)
                traces.extend(retrieval.trace_records)
                # Critical safety boundary: no AI assessment occurs before an
                # authorized governed regime is resolved.
                if retrieval.regime_resolution.status != RegimeResolutionStatus.RESOLVED:
                    continue
                if not retrieval.records:
                    continue
                candidate = self.model.assess(segment, category, retrieval.records)
                assessment_id = _identifier(
                    "assess",
                    understanding.document_hash,
                    segment.segment_id,
                    category.value,
                    *sorted(record.retrieval_id for record in retrieval.records),
                )
                assessment = StructuredAssessment(
                    assessment_id=assessment_id,
                    segment_id=candidate.segment_id,
                    retrieval_ids=candidate.retrieval_ids,
                    rule_category=candidate.rule_category,
                    decision=candidate.decision,
                    explanation=candidate.explanation,
                    evidence_ids=candidate.evidence_ids,
                    proposed_finding_type=candidate.proposed_finding_type,
                )
                assessments.append(assessment)
                decision = self.validator.validate(
                    assessment,
                    segment,
                    retrieval.records,
                    retrieval.regime_resolution.status,
                )
                decisions.append(decision)
                if decision.admission == FindingAdmission.ACCEPTED and decision.accepted_finding_id:
                    lineage.append(FindingLineageRecord(
                        accepted_finding_id=decision.accepted_finding_id,
                        assessment_id=assessment_id,
                        segment_id=segment.segment_id,
                        rule_category=category,
                        public_projection_status="pending",
                        public_finding_id=None,
                        reason="Customer projection remains unauthorized; accepted raw finding is explicitly retained.",
                    ))
                trace = TraceRecord(
                    trace_id=_identifier("trace", assessment_id),
                    document_hash=understanding.document_hash,
                    stage="structured_assessment_validation",
                    entity_type="assessment",
                    entity_id=assessment_id,
                    parent_trace_ids=[record.retrieval_id for record in retrieval.records],
                    payload_sha256=hashlib.sha256(_canonical({
                        "assessment": assessment.model_dump(mode="json"),
                        "decision": decision.model_dump(mode="json"),
                    })).hexdigest(),
                )
                traces.append(trace)
                if decision.admission == FindingAdmission.REJECTED and assessment.decision == AssessmentDecision.DEFICIENT:
                    abstentions.append(Abstention(
                        abstention_id=_identifier("abs", assessment_id),
                        stage="finding_admission",
                        subject=assessment_id,
                        reason_code="deterministic_validation_rejected",
                        explanation="; ".join(decision.reason_codes),
                    ))

        accepted = sum(decision.admission == FindingAdmission.ACCEPTED for decision in decisions)
        unresolved = any(
            result.regime_resolution.status != RegimeResolutionStatus.RESOLVED
            for result in retrieval_results
        )
        assessment_by_id = {item.assessment_id: item for item in assessments}
        rejected_required = any(
            decision.admission == FindingAdmission.REJECTED
            and assessment_by_id[decision.assessment_id].decision == AssessmentDecision.DEFICIENT
            for decision in decisions
        )
        assessment_abstained = any(item.decision == AssessmentDecision.ABSTAIN for item in assessments)
        required_count = len(plan)
        completed_count = len(assessments)
        if (
            unresolved
            or understanding.segment_coverage.completion_blockers
            or rejected_required
            or assessment_abstained
            or completed_count < required_count
        ):
            state = AnalysisState.LIMITED
        elif accepted:
            state = AnalysisState.COMPLETE_WITH_FINDINGS
        elif decisions:
            state = AnalysisState.COMPLETE_WITHOUT_FINDINGS
        else:
            state = AnalysisState.REQUIRES_CLARIFICATION
        return PhaseA4Result(
            run_id=_identifier("a4", understanding.run_id),
            document_hash=understanding.document_hash,
            analysis_state=state,
            retrievals=retrieval_results,
            applicability_plan=plan,
            assessments=assessments,
            validation_decisions=decisions,
            finding_lineage=lineage,
            abstentions=abstentions,
            trace_records=traces,
            shadow_only=True,
            customer_publication_authorized=False,
        )
