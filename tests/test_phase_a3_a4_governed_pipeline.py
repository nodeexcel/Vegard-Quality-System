import hashlib
import json
import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DATABASE_URL", "sqlite:///tmp.db")
os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("SECRET_KEY", "dummy")

from app.services.phase_a_assessment import (
    BedrockSemanticAssessmentModel,
    PhaseA4ShadowService,
    _assessment_segments_with_linked_summaries,
    _semantic_risiko_present,
    _split_compound_tgiu_candidate,
    _normalize_semantic_candidate,
)
from app.services.phase_a_applicability import DeterministicApplicabilityPlanner
from app.services.phase_a_contracts import (
    AssessmentCandidate,
    AssessmentDecision,
    FindingAdmission,
    RegimeResolution,
    RegimeResolutionStatus,
    RuleCategory,
    ValidatedSegment,
)
from app.services.phase_a_document_understanding import DocumentUnderstandingService
from app.services.phase_a_governed_retrieval import (
    GovernedAssetError,
    ManifestGovernedCatalog,
    ManifestVerifiedRuleRetriever,
)


REPORT = "[SIDE 1]\nLovlighet\nIngen ferdigattest er fremlagt.\n"


def test_hierarchy_linked_summary_is_trace_only_not_semantic_evidence():
    def evidence(evidence_id, quote, start):
        return {
            "evidence_id": evidence_id,
            "exact_quote": quote,
            "page": 1,
            "char_start": start,
            "char_end": start + len(quote),
            "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
            "match_method": "exact",
            "validation_status": "validated",
            "validation_notes": [],
        }

    primary_span = evidence("evidence_primary_0001", "Avvik er observert.", 0)
    summary_span = evidence("evidence_summary_0001", "Risiko for fuktskade.", 30)
    common = {
        "title": "Ventilasjon", "section_context": "7. Våtrom > Bad",
        "professional_subject": "Ventilasjon", "point_label": "7.3", "tg_grade": "TG2",
        "confidence": 1.0, "candidate_evidence": {
            "exact_quote": "Ventilasjon", "page": 1,
            "claimed_char_start": None, "claimed_char_end": None,
        },
        "validation_status": "validated", "validation_notes": [],
    }
    primary = ValidatedSegment.model_validate({
        **common, "segment_id": "segment_primary_0001", "kind": "report_point",
        "point_type": "graded", "evidence": primary_span,
        "evidence_spans": [primary_span], "bound_body_spans": [primary_span],
    })
    summary = ValidatedSegment.model_validate({
        **common, "segment_id": "segment_summary_0001", "kind": "summary",
        "point_type": "summary", "evidence": summary_span,
        "evidence_spans": [summary_span], "bound_body_spans": [summary_span],
        "supporting_primary_segment_id": primary.segment_id,
    })

    enriched = _assessment_segments_with_linked_summaries([primary, summary])

    assert [item.evidence_id for item in enriched[primary.segment_id].bound_body_spans] == [
        "evidence_primary_0001",
    ]
    assert enriched[summary.segment_id].supporting_primary_segment_id == primary.segment_id


@pytest.mark.parametrize("risk_text", [
    "Tilstanden innebærer risiko for videre nedbrytning av treverket.",
    "Mangelen medfører økt risiko for snøras og personskade.",
    "Beslaget øker risikoen for vanninntrengning og senere fuktskade.",
    "Forholdet vil kunne føre til råte i den bærende konstruksjonen.",
    "Det foreligger fare for funksjonssvikt ved fortsatt bruk.",
    "Dette gir økt sannsynlighet for vannskade over tid.",
    "Forholdet kan over tid belaste underliggende membran.",
    "Fuktskaden gir økt risiko for ytterligere oppsvelling.",
])
def test_semantic_risiko_recognizes_real_and_unseen_wording(risk_text):
    quote = "Observasjon: Eldre utførelse. " + risk_text
    span = {
        "evidence_id": "evidence_risk_variant_001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_risk_variant_01", "kind": "report_point", "title": "Taktekking",
        "section_context": "UTVENDIG", "professional_subject": "Taktekking", "point_label": "20.2",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Taktekking", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_risiko_present(segment)


def test_generic_inspection_methodology_does_not_satisfy_point_bound_risiko():
    quote = (
        "Hvordan kontrollen er utført Kontrollen vurderer forhold som kan gi økt risiko for "
        "kondens og fuktskader. Konklusjon bygningsdel: TG2 Avvik som bør utbedres."
    )
    span = {
        "evidence_id": "evidence_risk_boilerplate_01", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_risk_boilerplate_1", "kind": "report_point", "title": "Vegger",
        "section_context": "10. VASKEROM", "professional_subject": "Våtrom", "point_label": "10.1",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Vegger", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert not _semantic_risiko_present(segment)


def test_age_cause_is_not_mislabeled_as_observation_repeated_as_cause():
    quote = "Vurdering av avvik: Riss i puss. Årsak: Alder. Konsekvens: Fare for fuktskade."
    span = {
        "evidence_id": "evidence_age_cause_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_age_cause_0001", "kind": "report_point", "title": "Veggkonstruksjon",
        "section_context": "UTVENDIG", "professional_subject": "Vegg", "point_label": "18.2",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Veggkonstruksjon", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_age_0001"],
        rule_category=RuleCategory.AARSAK, decision=AssessmentDecision.DEFICIENT,
        explanation="The cause repeats an observation.", evidence_ids=[span["evidence_id"]],
        proposed_finding_type="OBSERVATION_AS_AARSAK",
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.SATISFIED
    assert normalized.proposed_finding_type is None


@pytest.mark.parametrize(("consequence", "expected_type"), [
    ("Har noe setningsskader og trenger utbedringer.", "TILTAK_AS_KONSEKVENS"),
    ("Risiko for videre utvikling av skade hvis forholdene vedvarer.", "RISIKO_AS_KONSEKVENS"),
    ("Økt fuktbelastning på mur.", "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS"),
])
def test_consequence_semantics_are_not_accepted_as_measure_or_risk_only(consequence, expected_type):
    quote = f"Vurdering av avvik: Avvik. Årsak: Utførelse. Konsekvens: {consequence}"
    span = {
        "evidence_id": "evidence_consequence_01", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_consequence_01", "kind": "report_point", "title": "Grunnmur",
        "section_context": "TOMTEFORHOLD", "professional_subject": "Grunnmur", "point_label": "1.3",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Grunnmur", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_consequence_01"],
        rule_category=RuleCategory.KONSEKVENS, decision=AssessmentDecision.SATISFIED,
        explanation="The consequence is sufficient.", evidence_ids=[span["evidence_id"]],
        proposed_finding_type=None,
    )
    records = [SimpleNamespace(rule_id=expected_type, content={"error_type": expected_type})]
    normalized = _normalize_semantic_candidate(candidate, segment, records)
    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == expected_type


def test_compound_tgiu_output_is_split_into_governed_atomic_candidates():
    candidate = AssessmentCandidate(
        segment_id="segment_tgiu_atomic_01", retrieval_ids=["retrieval_tgiu_01"],
        rule_category=RuleCategory.METHODOLOGY, decision=AssessmentDecision.DEFICIENT,
        explanation="Both independent TGIU requirements are missing.",
        evidence_ids=["evidence_tgiu_atomic_01"],
        proposed_finding_type="TGIU_MISSING_REASON and TGIU_MISSING_FURTHER_INVESTIGATION",
    )
    rules = [
        SimpleNamespace(rule_id="TGIU_MISSING_REASON", content={}),
        SimpleNamespace(rule_id="TGIU_MISSING_FURTHER_INVESTIGATION", content={}),
    ]
    split = _split_compound_tgiu_candidate(candidate, rules)
    assert [item.proposed_finding_type for item in split] == [
        "TGIU_MISSING_REASON", "TGIU_MISSING_FURTHER_INVESTIGATION",
    ]


class Extractor:
    def extract_candidates(self, **_kwargs):
        return {
            "facts": [],
            "segments": [{
                "candidate_id": "legality",
                "kind": "legality",
                "title": "Ferdigattest",
                "professional_subject": "lovlighet ferdigattest",
                "point_label": "L1",
                "tg_grade": None,
                "confidence": 0.99,
                "evidence": {
                    "exact_quote": "Ingen ferdigattest er fremlagt.",
                    "page": 1,
                    "claimed_char_start": None,
                    "claimed_char_end": None,
                },
            }],
            "abstentions": [],
        }, {"model_name": "fake-a2", "temperature": 0.0}


class ResolvedResolver:
    def resolve(self, rule_category, facts):
        list(facts)
        return RegimeResolution(
            rule_category=rule_category,
            status=RegimeResolutionStatus.RESOLVED,
            regime_id="test-only-regime",
            controlling_fact_ids=[],
            explanation="Synthetic test authorization.",
        )


class NeverCalledModel:
    def __init__(self):
        self.calls = 0

    def assess(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("model must not run while regime resolution is pending")


class DeficiencyModel:
    def __init__(self, finding_type="L-FA-01", bad_evidence=False, use_alternate_evidence=False):
        self.finding_type = finding_type
        self.bad_evidence = bad_evidence
        self.use_alternate_evidence = use_alternate_evidence
        self.calls = 0

    def assess(self, segment, category, rules):
        self.calls += 1
        selected = segment.evidence_spans[-1] if self.use_alternate_evidence else segment.evidence
        evidence_ids = ["unknown-evidence"] if self.bad_evidence else [selected.evidence_id]
        return AssessmentCandidate(
            segment_id=segment.segment_id,
            retrieval_ids=[record.retrieval_id for record in rules],
            rule_category=category,
            decision=AssessmentDecision.DEFICIENT,
            explanation="The required consequence is absent.",
            evidence_ids=evidence_ids,
            proposed_finding_type=self.finding_type,
        )


class SatisfiedModel:
    def assess(self, segment, category, rules):
        return AssessmentCandidate(
            segment_id=segment.segment_id,
            retrieval_ids=[record.retrieval_id for record in rules],
            rule_category=category,
            decision=AssessmentDecision.SATISFIED,
            explanation="Governed requirement is satisfied in substance.",
            evidence_ids=[(segment.bound_body_spans[0] if segment.bound_body_spans else segment.evidence).evidence_id],
            proposed_finding_type=None,
        )


class AbstainingModel(SatisfiedModel):
    def assess(self, segment, category, rules):
        candidate = super().assess(segment, category, rules)
        return candidate.model_copy(update={"decision": AssessmentDecision.ABSTAIN})


def _catalog(tmp_path: Path, *, corrupt=False):
    content = {
        "rules": [{
            "id": "L-FA-01",
            "error_type": "L-FA-01, MISSING_FERDIGATTEST",
            "topic": "ferdigattest",
            "title": "Manglende ferdigattest",
            "requirements": {"must_include_consequence": True},
        }]
    }
    raw = json.dumps(content, ensure_ascii=False).encode()
    (tmp_path / "rules.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    manifest = {"version": "test", "files": [{"path": "rules.json", "sha256": digest}]}
    manifest_path = tmp_path / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if corrupt:
        (tmp_path / "rules.json").write_text("{}", encoding="utf-8")
    return ManifestGovernedCatalog(
        tmp_path,
        manifest_path,
        approved_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )


def _understanding():
    return DocumentUnderstandingService(Extractor()).analyze(REPORT, "test.pdf")


def _retriever(catalog, resolver=None):
    return ManifestVerifiedRuleRetriever(
        catalog,
        resolver=resolver,
        category_assets={RuleCategory.LEGALITY: ("rules.json",)},
    )


def test_a3_rejects_asset_that_no_longer_matches_manifest(tmp_path):
    catalog = _catalog(tmp_path, corrupt=True)
    segment = _understanding().segments[0]
    with pytest.raises(GovernedAssetError, match="hash mismatch"):
        _retriever(catalog).retrieve(
            segment, RuleCategory.LEGALITY, [], document_hash="a" * 64
        )


def test_a3_proves_asset_and_chunk_provenance(tmp_path):
    result = _retriever(_catalog(tmp_path), ResolvedResolver()).retrieve(
        _understanding().segments[0], RuleCategory.LEGALITY, [], document_hash="a" * 64
    )
    assert result.asset_verifications[0].verified is True
    assert result.records
    assert any(record.rule_id == "L-FA-01" for record in result.records)
    assert all(record.asset_sha256 == result.asset_verifications[0].actual_sha256 for record in result.records)
    assert all(record.json_pointer and len(record.content_sha256) == 64 for record in result.records)
    assert all(record.applicability.value == "regime_resolved" for record in result.records)


def test_a4_pending_regime_abstains_before_model_invocation(tmp_path):
    model = NeverCalledModel()
    result = PhaseA4ShadowService(_retriever(_catalog(tmp_path)), model).analyze(
        _understanding(), [RuleCategory.LEGALITY]
    )
    assert model.calls == 0
    assert result.analysis_state.value == "limited"
    assert result.assessments == []
    assert result.validation_decisions == []
    assert any(item.reason_code == "pending_governed_decision" for item in result.abstentions)
    assert result.shadow_only is True
    assert result.customer_publication_authorized is False


def test_a4_admits_only_evidence_bound_governed_finding(tmp_path):
    model = DeficiencyModel()
    result = PhaseA4ShadowService(
        _retriever(_catalog(tmp_path), ResolvedResolver()), model
    ).analyze(_understanding(), [RuleCategory.LEGALITY])
    assert model.calls == 1
    assert result.analysis_state.value == "complete_with_findings"
    assert result.validation_decisions[0].admission == FindingAdmission.ACCEPTED
    assert result.validation_decisions[0].accepted_finding_id
    assert result.finding_lineage[0].accepted_finding_id == result.validation_decisions[0].accepted_finding_id
    assert result.finding_lineage[0].public_projection_status == "projected"


def test_governed_finding_aliases_share_one_canonical_stable_identity(tmp_path):
    service = lambda finding_type: PhaseA4ShadowService(
        _retriever(_catalog(tmp_path), ResolvedResolver()),
        DeficiencyModel(finding_type=finding_type),
    ).analyze(_understanding(), [RuleCategory.LEGALITY])
    canonical = service("L-FA-01").validation_decisions[0]
    alias = service("MISSING_FERDIGATTEST").validation_decisions[0]
    assert canonical.admission == alias.admission == FindingAdmission.ACCEPTED
    assert canonical.canonical_finding_identity == alias.canonical_finding_identity == "L-FA-01"
    assert canonical.accepted_finding_id == alias.accepted_finding_id


@pytest.mark.parametrize(
    "model,reason",
    [
        (DeficiencyModel(finding_type="UNREGISTERED"), "finding_type_not_governed_by_retrieved_rules"),
        (DeficiencyModel(bad_evidence=True), "unknown_evidence_reference"),
    ],
)
def test_a4_rejects_unsupported_or_unbound_findings(tmp_path, model, reason):
    result = PhaseA4ShadowService(
        _retriever(_catalog(tmp_path), ResolvedResolver()), model
    ).analyze(_understanding(), [RuleCategory.LEGALITY])
    decision = result.validation_decisions[0]
    assert decision.admission == FindingAdmission.REJECTED
    assert reason in decision.reason_codes
    assert decision.accepted_finding_id is None


def test_applicability_plan_only_runs_structurally_relevant_categories():
    understanding = _understanding()
    plan = DeterministicApplicabilityPlanner().plan(understanding.segments)
    assert [(item.segment_id, item.rule_category) for item in plan] == [
        (understanding.segments[0].segment_id, RuleCategory.LEGALITY)
    ]


def test_tg3_cost_retrieval_uses_actual_manifest_governed_rules():
    payload = {
        "facts": [],
        "segments": [{
            "candidate_id": "terrain",
            "kind": "report_point",
            "title": "Terrengforhold",
            "professional_subject": "terreng",
            "point_label": "3",
            "tg_grade": "TG3",
            "confidence": 0.99,
            "evidence": {"exact_quote": "Terrengforhold TG3", "page": 1},
        }],
        "abstentions": [],
    }

    class Tg3Extractor:
        def extract_candidates(self, **_kwargs):
            return payload, {"model_name": "fake"}

    understanding = DocumentUnderstandingService(Tg3Extractor()).analyze(
        "[SIDE 1]\nTerrengforhold TG3\nTerrenget har motfall.\n", "tg3.pdf"
    )
    catalog = ManifestGovernedCatalog(ROOT / "files", ROOT / "files" / "MANIFEST.json")
    result = ManifestVerifiedRuleRetriever(catalog).retrieve(
        understanding.segments[0], RuleCategory.TG3_COST, [], document_hash=understanding.document_hash
    )
    assert result.asset_verifications[0].asset_path == "rag_scoring_model_validert_v1.6.15.json"
    ids = {record.rule_id for record in result.records}
    assert "E_METHOD.tg3_cost_missing" in ids
    assert "E_METHOD.tg3_cost_single_amount_only" in ids


def test_stable_finding_identity_and_complete_without_findings_rules(tmp_path):
    retriever = _retriever(_catalog(tmp_path), ResolvedResolver())
    understanding = _understanding()
    first = PhaseA4ShadowService(retriever, DeficiencyModel()).analyze(understanding, [RuleCategory.LEGALITY])
    second = PhaseA4ShadowService(retriever, DeficiencyModel()).analyze(understanding, [RuleCategory.LEGALITY])
    assert first.validation_decisions[0].accepted_finding_id == second.validation_decisions[0].accepted_finding_id
    segment = understanding.segments[0]
    alternate_span = segment.evidence.model_copy(update={"evidence_id": "source_alternate_valid_span"})
    alternate_understanding = understanding.model_copy(update={
        "segments": [segment.model_copy(update={"evidence_spans": [segment.evidence, alternate_span]})]
    })
    alternate_evidence = PhaseA4ShadowService(
        retriever, DeficiencyModel(use_alternate_evidence=True)
    ).analyze(alternate_understanding, [RuleCategory.LEGALITY])
    assert first.validation_decisions[0].accepted_finding_id == alternate_evidence.validation_decisions[0].accepted_finding_id

    satisfied = PhaseA4ShadowService(retriever, SatisfiedModel()).analyze(understanding, [RuleCategory.LEGALITY])
    assert satisfied.analysis_state.value == "complete_without_findings"
    abstained = PhaseA4ShadowService(retriever, AbstainingModel()).analyze(understanding, [RuleCategory.LEGALITY])
    assert abstained.analysis_state.value == "limited"

    blocked_understanding = understanding.model_copy(update={
        "segment_coverage": understanding.segment_coverage.model_copy(update={
            "completion_blockers": ["physical_boundary_uncertain:test-point"]
        })
    })
    structurally_blocked = PhaseA4ShadowService(retriever, SatisfiedModel()).analyze(
        blocked_understanding, [RuleCategory.LEGALITY]
    )
    assert structurally_blocked.analysis_state.value == "limited"
    assert structurally_blocked.customer_publication_authorized is False


def test_semantic_model_receives_complete_body_and_does_not_require_headings(tmp_path):
    captured = {}

    class FakeBedrock:
        def generate_json_with_claude(self, **kwargs):
            captured.update(kwargs)
            prompt = json.loads(kwargs["user_prompt"])
            segment = prompt["segment"]
            rules = prompt["retrieved_rules"]
            return {
                "segment_id": segment["segment_id"],
                "retrieval_ids": [item["retrieval_id"] for item in rules],
                "rule_category": prompt["rule_category"],
                "decision": "satisfied",
                "explanation": "A recommended measure is present in substance.",
                "evidence_ids": [segment["complete_bound_body"][0]["evidence_id"]],
                "proposed_finding_type": None,
            }

    report = "[SIDE 1]\nBad TG2\nFallet bør korrigeres ved rehabilitering.\n"

    class PointExtractor:
        def extract_candidates(self, **_kwargs):
            return {
                "facts": [],
                "segments": [{
                    "kind": "report_point", "title": "Bad", "professional_subject": "våtrom",
                    "point_label": "7.1", "tg_grade": "TG2", "confidence": 0.99,
                    "evidence": {"exact_quote": "Bad TG2", "page": 1},
                }],
                "abstentions": [],
            }, {"model_name": "fake"}

    segment = DocumentUnderstandingService(PointExtractor()).analyze(report, "point.pdf").segments[0]
    rule = _retriever(_catalog(tmp_path), ResolvedResolver()).retrieve(
        _understanding().segments[0], RuleCategory.LEGALITY, [], document_hash="a" * 64
    ).records[0]
    rule = rule.model_copy(update={
        "segment_id": segment.segment_id,
        "rule_category": RuleCategory.ANBEFALT_TILTAK,
    })
    candidate = BedrockSemanticAssessmentModel(FakeBedrock()).assess(
        segment, RuleCategory.ANBEFALT_TILTAK, [rule]
    )
    assert candidate.decision == AssessmentDecision.SATISFIED
    assert "Fallet bør korrigeres" in captured["user_prompt"]
    assert "Assess meaning" in captured["system_prompt"]
