"""Trace-preserving customer projection for shadow A4 evidence."""

from __future__ import annotations

import hashlib

from app.services.phase_a_contracts import (
    AnalysisState,
    DocumentUnderstandingResult,
    FindingAdmission,
    FindingLineageRecord,
    PhaseACustomerItem,
    PhaseAPublicPayload,
    PhaseAPublicFinding,
    PhaseAScoreResult,
    StructuredAssessment,
    FindingValidationDecision,
)


def _id(value: str) -> str:
    return f"customer_{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def project_customer_result(
    understanding: DocumentUnderstandingResult,
    assessments: list[StructuredAssessment],
    decisions: list[FindingValidationDecision],
    score: PhaseAScoreResult,
    state: AnalysisState,
) -> tuple[list[PhaseACustomerItem], PhaseAPublicPayload, list[FindingLineageRecord]]:
    segments = {item.segment_id: item for item in understanding.segments}
    assessment_by_id = {item.assessment_id: item for item in assessments}
    items: list[PhaseACustomerItem] = []
    lineage: list[FindingLineageRecord] = []
    for decision in decisions:
        if decision.admission != FindingAdmission.ACCEPTED or not decision.accepted_finding_id:
            continue
        assessment = assessment_by_id[decision.assessment_id]
        segment = segments[assessment.segment_id]
        all_evidence = [*segment.bound_body_spans, *segment.evidence_spans]
        if segment.evidence is not None:
            all_evidence.append(segment.evidence)
        evidence_by_id = {item.evidence_id: item for item in all_evidence}
        evidence = [evidence_by_id[item] for item in assessment.evidence_ids if item in evidence_by_id]
        if not evidence:
            raise ValueError(f"accepted finding has no projectable evidence: {decision.accepted_finding_id}")
        point_id = decision.canonical_point_id or segment.point_label or segment.segment_id
        item = PhaseACustomerItem(
            customer_item_id=_id(decision.accepted_finding_id),
            accepted_finding_id=decision.accepted_finding_id,
            point_id=point_id,
            point_title=segment.title,
            evidence=evidence,
            deficiency=assessment.explanation,
            obligation_class=decision.obligation_class or "validert_product_quality",
            why_it_matters=assessment.explanation,
            improvement="Oppdater punktteksten slik at det styrte kravet er oppfylt i substans.",
            category=decision.category or "A",
            deduction=decision.deduction,
            blocks_96_gate=decision.blocks_96_gate,
        )
        items.append(item)
        lineage.append(FindingLineageRecord(
            accepted_finding_id=decision.accepted_finding_id,
            assessment_id=assessment.assessment_id,
            segment_id=segment.segment_id,
            rule_category=assessment.rule_category,
            public_projection_status="projected",
            public_finding_id=item.customer_item_id,
            reason="Accepted finding is represented exactly once in the normalized and serialized shadow customer payload.",
        ))
    if len({item.accepted_finding_id for item in items}) != len(items):
        raise ValueError("duplicate accepted finding in customer projection")
    public_findings = [PhaseAPublicFinding(
        point=f"{item.point_id} – {item.point_title}",
        evidence=[span.exact_quote for span in item.evidence],
        message=item.deficiency,
        obligation_class=item.obligation_class,
        why_it_matters=item.why_it_matters,
        recommended_fix_text=item.improvement,
        category=item.category,
        deduction=item.deduction,
        blocks_96_gate=item.blocks_96_gate,
    ) for item in items]
    payload = PhaseAPublicPayload(
        status=state,
        score=score.score if score.score_valid else None,
        score_valid=score.score_valid,
        gate_blocked=score.gate_blocked,
        findings=public_findings,
    )
    return items, payload, lineage


def serialize_for_customer_result_component(
    payload: PhaseAPublicPayload,
    score: PhaseAScoreResult,
    *,
    report_id: int,
    filename: str,
) -> dict:
    """Controlled injection envelope consumed by the real results page.

    This is never returned by a production API route. It intentionally contains
    no rule IDs, finding IDs, retrieval records or governance machinery.
    """
    categories = [
        {
            "category_id": item.category,
            "category_name": {
                "A": "ARKAT-kvalitet (TG2/TG3)", "B": "TG-setting og konsistens",
                "C": "TGIU og undersøkelsesbegrensninger", "D": "Klarspråk og struktur",
                "E": "Metodikk og lovforankring", "F": "Lovlighetsmangler",
            }[item.category],
            "deduction": item.capped_deduction,
            "max_deduction": item.cap,
        }
        for item in score.categories
    ]
    findings = [
        {
            "point_id": item.point.split(" – ", 1)[0],
            "message": item.message,
            "what_to_change": item.recommended_fix_text,
            "evidence": {"page": None, "snippet": "\n".join(item.evidence)},
            "obligation_class": item.obligation_class,
            "deduction": item.deduction,
            "affects_96_gate": item.blocks_96_gate,
        }
        for item in payload.findings
    ]
    return {
        "id": report_id,
        "filename": filename,
        "report_system": "phase_a_shadow_candidate",
        "building_year": None,
        "uploaded_at": "2026-08-10T00:00:00Z",
        "overall_score": None,
        "quality_score": None,
        "completeness_score": None,
        "compliance_score": None,
        "components": [],
        "findings": [],
        "extracted_text": None,
        "phase_a_shadow": True,
        "ai_analysis": {
            "meta": {"schema_version": "1.4", "analysis_timestamp_utc": "2026-08-10T00:00:00Z", "document_title": filename},
            "score_total": payload.score,
            "score_band": "",
            "score_by_category": categories,
            "top_score_drivers": [],
            "findings": [],
            "improvements": [],
            "disclaimers": [],
            "gate": {"active": True, "blocked_96": payload.gate_blocked},
        },
        "scoring_result": None,
        "public_feedback": {
            "version": "feedback_v11_public_v1",
            "points_overview": [],
            "findings": findings,
        },
    }
