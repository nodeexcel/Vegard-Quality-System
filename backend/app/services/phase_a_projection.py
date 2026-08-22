"""Trace-preserving customer projection for shadow A4 evidence."""

from __future__ import annotations

from datetime import datetime, timezone
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


def _customer_wording(identity: str | None, category: str) -> tuple[str, str]:
    """Translate an admitted governed result without exposing heading requirements."""
    identity = identity or ""
    if "MISSING (aarsak)" in identity:
        return (
            "Punktet forklarer ikke den faglige årsaken til det beskrevne avviket.",
            "Forklar hvorfor avviket har oppstått eller hvilken mekanisme som ligger bak.",
        )
    if "MISSING (risiko)" in identity:
        return (
            "Punktet forklarer ikke den tekniske risikoen eller mulige skadeutviklingen som kan følge av forholdet.",
            "Beskriv hvilken teknisk utvikling, skade eller funksjonssvikt som kan oppstå dersom forholdet vedvarer.",
        )
    if "MISSING (konsekvens)" in identity:
        return (
            "Punktet forklarer ikke hva forholdet konkret kan bety for kjøper.",
            "Beskriv den praktiske, økonomiske, sikkerhetsmessige eller bruksmessige konsekvensen.",
        )
    if "RISIKO_AS_KONSEKVENS" in identity:
        return (
            "Punktet beskriver mulig skadeutvikling, men ikke hva dette konkret kan bety for kjøper.",
            "Suppler med den praktiske, økonomiske, sikkerhetsmessige eller bruksmessige konsekvensen.",
        )
    if "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS" in identity:
        return (
            "Punktet beskriver en teknisk utvikling, men ikke den konkrete betydningen for kjøper.",
            "Forklar hvilken praktisk eller økonomisk følge den tekniske utviklingen kan få.",
        )
    if "TILTAK_AS_KONSEKVENS" in identity:
        return (
            "Punktet oppgir et utbedringsbehov, men forklarer ikke den konkrete konsekvensen for kjøper.",
            "Beskriv konsekvensen separat fra hva som bør utbedres.",
        )
    if "LIMITATION_USED_AS_RISK_SUBSTITUTE" in identity:
        return (
            "Punktet beskriver en dokumentasjons- eller inspeksjonsbegrensning uten å forklare en konkret teknisk risiko.",
            "Beskriv hvilken teknisk skade, utvikling eller funksjonssvikt begrensningen skaper usikkerhet om.",
        )
    if identity.startswith("TGIU_MISSING_REASON"):
        return (
            "Punktet forklarer ikke tilstrekkelig hvorfor undersøkelsen ikke kunne gjennomføres.",
            "Beskriv den konkrete årsaken til at bygningsdelen ikke kunne undersøkes.",
        )
    if identity.startswith("TGIU_MISSING_FURTHER_INVESTIGATION"):
        return (
            "Punktet gir ikke en konkret anbefaling om videre undersøkelse.",
            "Oppgi hvilken videre undersøkelse som bør utføres og av hvem når det er relevant.",
        )
    if identity == "E_METHOD.tg3_cost_missing":
        return (
            "TG3-punktet mangler et punktbundet sjablongmessig kostnadsanslag.",
            "Oppgi kostnadsklasse eller kostnadsintervall for dette TG3-punktet.",
        )
    if identity == "E_METHOD.tg2_missing_anbefalt_tiltak_ns2025":
        return (
            "TG2-punktet beskriver ikke et nødvendig eller anbefalt tiltak i substans.",
            "Beskriv hva som bør gjøres for det konkrete punktet.",
        )
    if identity == "E_METHOD.garasje_avvik_uten_arkat":
        return (
            "Det frittstående bygget har konkrete beskrevne avvik uten en fullstendig faglig forklaring av årsak, risiko, konsekvens og tiltak.",
            "Suppler de konkrete avvikene med nødvendig årsak, risiko, konsekvens og anbefalt tiltak.",
        )
    if identity == "L-AV-01":
        return (
            "Det beskrevne lovlighetsavviket mangler en forklaring av den praktiske eller rettslige konsekvensen.",
            "Forklar hva avviket kan innebære for bruk, godkjenning eller videre oppfølging.",
        )
    return (
        "Punktet oppfyller ikke det gjeldende kvalitetskravet i substans.",
        "Suppler punktet slik at det faglige kravet blir tydelig oppfylt.",
    )


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
    grouped: dict[str, list[tuple[FindingValidationDecision, StructuredAssessment, object]]] = {}
    for decision in decisions:
        if decision.admission != FindingAdmission.ACCEPTED or not decision.accepted_finding_id:
            continue
        assessment = assessment_by_id[decision.assessment_id]
        segment = segments[assessment.segment_id]
        grouped.setdefault(segment.segment_id, []).append((decision, assessment, segment))

    for grouped_findings in grouped.values():
        first_decision, first_assessment, segment = grouped_findings[0]
        all_evidence = [*segment.bound_body_spans, *segment.evidence_spans]
        if segment.evidence is not None:
            all_evidence.append(segment.evidence)
        evidence_by_id = {item.evidence_id: item for item in all_evidence}
        evidence_ids = list(dict.fromkeys(
            evidence_id
            for _, assessment, _ in grouped_findings
            for evidence_id in assessment.evidence_ids
        ))
        evidence = [evidence_by_id[item] for item in evidence_ids if item in evidence_by_id]
        if not evidence:
            raise ValueError(f"accepted finding group has no projectable evidence: {segment.segment_id}")
        accepted_ids = [decision.accepted_finding_id for decision, _, _ in grouped_findings]
        accepted_ids = [value for value in accepted_ids if value]
        identities = [decision.canonical_finding_identity for decision, _, _ in grouped_findings]
        messages_and_fixes = [_customer_wording(identity, decision.category or "A") for identity, (decision, _, _) in zip(identities, grouped_findings)]
        messages = list(dict.fromkeys(item[0] for item in messages_and_fixes))
        fixes = list(dict.fromkeys(item[1] for item in messages_and_fixes))
        category_totals: dict[str, int] = {}
        for decision, _, _ in grouped_findings:
            category_totals[decision.category or "A"] = category_totals.get(decision.category or "A", 0) + decision.deduction
        primary_category = max(category_totals, key=lambda value: (category_totals[value], value))
        obligation_classes = {decision.obligation_class or "validert_product_quality" for decision, _, _ in grouped_findings}
        obligation_class = (
            "regulatory" if "regulatory" in obligation_classes
            else "standard_methodology" if "standard_methodology" in obligation_classes
            else "validert_product_quality"
        )
        point_id = first_decision.canonical_point_id or segment.point_label or segment.segment_id
        customer_item_id = _id("|".join(sorted(accepted_ids)))
        item = PhaseACustomerItem(
            customer_item_id=customer_item_id,
            accepted_finding_id=accepted_ids[0],
            accepted_finding_ids=accepted_ids,
            point_id=point_id,
            point_title=segment.title,
            evidence=evidence,
            deficiency=" ".join(messages),
            obligation_class=obligation_class,
            why_it_matters=" ".join(messages),
            improvement=" ".join(fixes),
            category=primary_category,
            deduction=sum(decision.deduction for decision, _, _ in grouped_findings),
            blocks_96_gate=any(decision.blocks_96_gate for decision, _, _ in grouped_findings),
        )
        items.append(item)
        for decision, assessment, _ in grouped_findings:
            lineage.append(FindingLineageRecord(
                accepted_finding_id=decision.accepted_finding_id,
                assessment_id=assessment.assessment_id,
                segment_id=segment.segment_id,
                rule_category=assessment.rule_category,
                public_projection_status="projected",
                public_finding_id=item.customer_item_id,
                reason=(
                    "Accepted raw finding is represented by this normalized customer item; "
                    "coherent same-point findings intentionally share the item while retaining independent lineage."
                ),
            ))
    projected_ids = [finding_id for item in items for finding_id in item.accepted_finding_ids]
    if len(projected_ids) != len(set(projected_ids)):
        raise ValueError("accepted raw finding is projected more than once")
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
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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
        "uploaded_at": timestamp,
        "overall_score": None,
        "quality_score": None,
        "completeness_score": None,
        "compliance_score": None,
        "components": [],
        "findings": [],
        "extracted_text": None,
        "phase_a_shadow": True,
        "ai_analysis": {
            "meta": {"schema_version": "1.4", "analysis_timestamp_utc": timestamp, "document_title": filename},
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
