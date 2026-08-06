import hashlib
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DATABASE_URL", "sqlite:///tmp.db")
os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("SECRET_KEY", "dummy")

from app.services.phase_a_assessment import BedrockSemanticAssessmentModel, PhaseA4ShadowService
from app.services.phase_a_applicability import DeterministicApplicabilityPlanner
from app.services.phase_a_contracts import (
    AssessmentCandidate,
    AssessmentDecision,
    FindingAdmission,
    RegimeResolution,
    RegimeResolutionStatus,
    RuleCategory,
)
from app.services.phase_a_document_understanding import DocumentUnderstandingService
from app.services.phase_a_governed_retrieval import (
    GovernedAssetError,
    ManifestGovernedCatalog,
    ManifestVerifiedRuleRetriever,
)


REPORT = "[SIDE 1]\nLovlighet\nIngen ferdigattest er fremlagt.\n"


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
    def __init__(self, finding_type="L-FA-01", bad_evidence=False):
        self.finding_type = finding_type
        self.bad_evidence = bad_evidence
        self.calls = 0

    def assess(self, segment, category, rules):
        self.calls += 1
        evidence_ids = ["unknown-evidence"] if self.bad_evidence else [segment.evidence.evidence_id]
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
    return ManifestGovernedCatalog(tmp_path, manifest_path)


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
    assert result.finding_lineage[0].public_projection_status == "pending"


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

    satisfied = PhaseA4ShadowService(retriever, SatisfiedModel()).analyze(understanding, [RuleCategory.LEGALITY])
    assert satisfied.analysis_state.value == "complete_without_findings"
    abstained = PhaseA4ShadowService(retriever, AbstainingModel()).analyze(understanding, [RuleCategory.LEGALITY])
    assert abstained.analysis_state.value == "limited"


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
