import hashlib
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DATABASE_URL", "sqlite:///tmp.db")
os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("SECRET_KEY", "dummy")

from app.services.phase_a_assessment import DeterministicAssessmentValidator
from app.services.phase_a_contracts import (
    AnalysisState, AssessmentDecision, CandidateEvidence, DocumentUnderstandingResult,
    FactType, FindingAdmission, FindingValidationDecision, InventoryRole,
    RegimeResolutionStatus, RuleApplicability, RuleCategory, RuleRetrievalRecord,
    SegmentCoverage, SegmentKind, SourceEvidence, StructuredAssessment,
    UnderstandingStatus, ValidatedDocumentFact, ValidatedSegment, ValidationStatus,
)
from app.services.phase_a_governed_retrieval import ManifestGovernedCatalog
from app.services.phase_a_projection import project_customer_result, serialize_for_customer_result_component
from app.services.phase_a_regime import ApprovedGovernedRegimeResolver
from app.services.phase_a_scoring import score_admitted_findings


def _evidence(text="TG2-punkt uten anbefalt tiltak"):
    return SourceEvidence(
        evidence_id="evidence_12345678", exact_quote=text, page=1, char_start=0,
        char_end=len(text), quote_sha256=hashlib.sha256(text.encode()).hexdigest(),
        validation_status=ValidationStatus.VALIDATED,
    )


def _fact(kind, value, suffix):
    evidence = _evidence(value)
    return ValidatedDocumentFact(
        fact_id=f"fact_{suffix}_12345678", fact_type=kind, raw_value=value,
        normalized_value=value, confidence=1.0,
        candidate_evidence=CandidateEvidence(exact_quote=value, page=1),
        evidence=evidence, validation_status=ValidationStatus.VALIDATED,
    )


def _segment(label="7.1"):
    evidence = _evidence()
    return ValidatedSegment(
        segment_id="segment_tg2_12345678", kind=SegmentKind.REPORT_POINT,
        title="Bad – Overflater", professional_subject="våtrom", point_label=label,
        tg_grade="TG2", point_type="graded", confidence=1.0,
        candidate_evidence=CandidateEvidence(exact_quote=evidence.exact_quote, page=1),
        evidence=evidence, evidence_spans=[evidence], bound_body_spans=[evidence],
        bound_body_sha256=evidence.quote_sha256, validation_status=ValidationStatus.VALIDATED,
    )


def _rule(segment, edition="NS 3600:2025"):
    content = {
        "id": "E_METHOD.tg2_missing_anbefalt_tiltak_ns2025",
        "category": "E", "deduction": 3, "obligation_class": "standard_methodology",
        "regulatory": False, "blocks_96_gate": False,
        "applies_when": {"applicable_ns_edition": "NS 3600:2025"},
    }
    return RuleRetrievalRecord(
        retrieval_id="retrieval_12345678", segment_id=segment.segment_id,
        rule_category=RuleCategory.ANBEFALT_TILTAK,
        asset_path="candidates/a3_a4_v2/validert_phase_a_methodology_rules_v1_0.json",
        asset_sha256="a" * 64, rule_id=content["id"], json_pointer="/rules/0",
        content_sha256="b" * 64, content=content, relevance_score=1.0,
        applicability=RuleApplicability.REGIME_RESOLVED,
        regime_status=RegimeResolutionStatus.RESOLVED, regime_id="TRANSITION_2026",
        controlling_fact_ids=["fact_report_12345678"], regime_explanation=edition,
        retrieval_reason="Synthetic governed candidate rule.",
    )


def _understanding(segment):
    return DocumentUnderstandingResult(
        run_id="understanding_12345678", document_hash="c" * 64,
        source_filename="test.pdf", route_used="test", extracted_text_sha256="d" * 64,
        status=UnderstandingStatus.COMPLETE, facts=[], segments=[segment], batch_count=1,
        segment_coverage=SegmentCoverage(raw_candidate_count=1, unique_candidate_count=1,
            admitted_count=1, abstained_count=0, duplicate_count=0, dispositions_count=1),
    )


def test_candidate_manifest_is_isolated_and_hash_pinned():
    manifest = ROOT / "files/candidates/a3_a4_v2/MANIFEST.a3_a4_candidate.json"
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    catalog = ManifestGovernedCatalog(ROOT / "files", manifest, approved_manifest_sha256=digest)
    _, verification = catalog.load("candidates/a3_a4_v2/validert_phase_a_methodology_rules_v1_0.json")
    assert verification.verified is True
    assert hashlib.sha256((ROOT / "files/MANIFEST.json").read_bytes()).hexdigest() == "310f2377501024ecc32646a6adad3175414f6dbdfa0b3ecd156bd4d47bc2d8a1"


def test_governed_regime_transition_and_full_2026_rules():
    resolver = ApprovedGovernedRegimeResolver()
    june_2018 = resolver.resolve(RuleCategory.ANBEFALT_TILTAK, [
        _fact(FactType.REPORT_DATE, "2026-06-10", "report"),
        _fact(FactType.DECLARED_STANDARD, "NS 3600:2018", "standard"),
    ])
    assert (june_2018.regime_id, june_2018.applicable_ns_edition) == ("TRANSITION_2026", "NS 3600:2018")
    june_2025 = resolver.resolve(RuleCategory.ANBEFALT_TILTAK, [
        _fact(FactType.REPORT_DATE, "2026-06-10", "report"),
        _fact(FactType.DECLARED_STANDARD, "NS 3600:2025", "standard"),
    ])
    assert june_2025.applicable_ns_edition == "NS 3600:2025"
    missing = resolver.resolve(RuleCategory.ANBEFALT_TILTAK, [_fact(FactType.REPORT_DATE, "2026-06-10", "report")])
    assert missing.status == RegimeResolutionStatus.REQUIRES_CLARIFICATION
    july_conflict = resolver.resolve(RuleCategory.ANBEFALT_TILTAK, [
        _fact(FactType.REPORT_DATE, "2026-07-13", "report"),
        _fact(FactType.DECLARED_STANDARD, "NS 3600:2018", "standard"),
    ])
    assert july_conflict.applicable_ns_edition == "NS 3600:2025"
    assert july_conflict.conflict_detail == "declared_ns2018_after_2026_07_01"


def test_tg2_ns2025_missing_measure_admits_exact_e_rule_and_customer_chain():
    segment = _segment()
    assessment = StructuredAssessment(
        assessment_id="assessment_12345678", segment_id=segment.segment_id,
        retrieval_ids=["retrieval_12345678"], rule_category=RuleCategory.ANBEFALT_TILTAK,
        decision=AssessmentDecision.DEFICIENT, explanation="Anbefalt tiltak mangler.",
        evidence_ids=[segment.evidence.evidence_id], proposed_finding_type="MISSING",
    )
    decision = DeterministicAssessmentValidator().validate(
        assessment, segment, [_rule(segment)], RegimeResolutionStatus.RESOLVED
    )
    assert decision.admission == FindingAdmission.ACCEPTED
    assert decision.accepted_finding_id == "E_METHOD_tg2_missing_anbefalt_tiltak_ns2025_7.1"
    assert (decision.category, decision.deduction, decision.obligation_class) == ("E", 3, "standard_methodology")
    assert decision.regulatory is False and decision.blocks_96_gate is False
    score = score_admitted_findings([decision])
    assert score.score == 97 and score.gate_blocked is False
    items, public, lineage = project_customer_result(
        _understanding(segment), [assessment], [decision], score, AnalysisState.COMPLETE_WITH_FINDINGS
    )
    assert len(items) == len(public.findings) == len(lineage) == 1
    assert "accepted_finding_id" not in public.model_dump(mode="json")["findings"][0]
    envelope = serialize_for_customer_result_component(public, score, report_id=1, filename="test.pdf")
    assert envelope["phase_a_shadow"] is True
    assert envelope["ai_analysis"]["score_total"] == 97
    assert len(envelope["public_feedback"]["findings"]) == 1
    forbidden = ("rule_id", "finding_id", "retrieval", "governance")
    rendered = str(envelope["public_feedback"]).lower()
    assert not any(token in rendered for token in forbidden)


def test_same_physical_point_findings_group_with_complete_raw_lineage():
    segment = _segment("10.1")
    assessments = [
        StructuredAssessment(
            assessment_id=f"assessment_group_{index}_12345678", segment_id=segment.segment_id,
            retrieval_ids=["retrieval_12345678"], rule_category=category,
            decision=AssessmentDecision.DEFICIENT, explanation="Semantic deficiency.",
            evidence_ids=[segment.evidence.evidence_id], proposed_finding_type=identity,
        )
        for index, (category, identity) in enumerate([
            (RuleCategory.AARSAK, "MISSING (aarsak)"),
            (RuleCategory.RISIKO, "MISSING (risiko)"),
        ], start=1)
    ]
    decisions = [
        FindingValidationDecision(
            validation_id=f"validation_group_{index}_12345678",
            assessment_id=assessment.assessment_id, admission=FindingAdmission.ACCEPTED,
            accepted_finding_id=f"finding_group_{index}_12345678",
            canonical_finding_identity=assessment.proposed_finding_type,
            canonical_point_id="10.1", category="A", deduction=deduction,
            obligation_class="validert_product_quality", regulatory=False,
            blocks_96_gate=True,
        )
        for index, (assessment, deduction) in enumerate(zip(assessments, [6, 5]), start=1)
    ]
    score = score_admitted_findings(decisions)
    items, public, lineage = project_customer_result(
        _understanding(segment), assessments, decisions, score, AnalysisState.COMPLETE_WITH_FINDINGS
    )
    assert len(items) == len(public.findings) == 1
    assert items[0].accepted_finding_ids == [decision.accepted_finding_id for decision in decisions]
    assert items[0].deduction == 11
    assert len(lineage) == 2
    assert len({item.public_finding_id for item in lineage}) == 1
    assert "heading" not in public.findings[0].message.casefold()
    assert "felt" not in public.findings[0].message.casefold()


def test_category_e_cap_and_no_category_a_duplicate():
    decisions = [
        FindingValidationDecision(
            validation_id=f"validation_{index}_12345678", assessment_id=f"assessment_{index}_12345678",
            admission=FindingAdmission.ACCEPTED,
            accepted_finding_id=f"E_METHOD_tg2_missing_anbefalt_tiltak_ns2025_{index}",
            canonical_finding_identity="E_METHOD.tg2_missing_anbefalt_tiltak_ns2025",
            canonical_point_id=str(index), category="E", deduction=3,
            obligation_class="standard_methodology", regulatory=False, blocks_96_gate=False,
        ) for index in range(1, 6)
    ]
    score = score_admitted_findings(decisions)
    category_e = next(item for item in score.categories if item.category == "E")
    category_a = next(item for item in score.categories if item.category == "A")
    assert (category_e.raw_deduction, category_e.capped_deduction, score.score) == (15, 10, 90)
    assert category_a.raw_deduction == 0


def test_limitation_as_risk_uses_governed_canonical_identity_and_stable_point_id():
    segment = _segment("7.4")
    content = {
        "id": "A_ARKAT_SEMANTIC.RISIKO.LIMITATION_USED_AS_RISK_SUBSTITUTE",
        "semantic_field": "risiko",
        "semantic_error_type": "LIMITATION_USED_AS_RISK_SUBSTITUTE",
        "category": "A", "deduction": 3,
        "obligation_class": "validert_product_quality",
        "regulatory": False, "blocks_96_gate": False,
    }
    rule = _rule(segment)
    rule = rule.model_copy(update={
        "rule_category": RuleCategory.RISIKO,
        "rule_id": content["id"],
        "content": content,
    })
    assessment = StructuredAssessment(
        assessment_id="assessment_risk_12345678", segment_id=segment.segment_id,
        retrieval_ids=[rule.retrieval_id], rule_category=RuleCategory.RISIKO,
        decision=AssessmentDecision.DEFICIENT,
        explanation="Dokumentasjonsbegrensning uten teknisk risiko.",
        evidence_ids=[segment.evidence.evidence_id],
        proposed_finding_type="LIMITATION_USED_AS_RISK_SUBSTITUTE",
    )
    decision = DeterministicAssessmentValidator().validate(
        assessment, segment, [rule], RegimeResolutionStatus.RESOLVED
    )
    assert decision.admission == FindingAdmission.ACCEPTED
    assert decision.canonical_finding_identity == content["id"]
    assert decision.accepted_finding_id == "A_ARKAT_7.4_RISIKO_LIMITATION_USED_AS_RISK_SUBSTITUTE"
    assert (decision.category, decision.deduction, decision.blocks_96_gate) == ("A", 3, False)
