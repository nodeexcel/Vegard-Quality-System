import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DATABASE_URL", "sqlite:///tmp.db")
os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("SECRET_KEY", "dummy")

from app.services.phase_a_contracts import (
    CandidateBatch,
    CandidateEvidence,
    DocumentFactCandidate,
    FactType,
    RegimeResolutionStatus,
    RuleCategory,
    SegmentCandidate,
    SegmentKind,
    UnderstandingStatus,
    ValidationStatus,
)
from app.services.phase_a_document_understanding import (
    DocumentUnderstandingService,
    PageSpan,
    split_page_spans,
)
from app.services.phase_a_regime import PendingGovernedRegimeResolver
from app.services.phase_a_pipeline import (
    PhaseAFeaturePolicy,
    PhaseANotEnabledError,
    PhaseAPipeline,
    PhaseAPublicationNotAuthorizedError,
)


REPORT_TEXT = """[PDF METADATA]
Totalt antall sider: 2

[SIDE 1]
Tilstandsrapport fra Eksempel Takst AS
Befaringsdato: 02.07.2026
Rapportdato: 03.07.2026
Rapporten erklærer NS 3600:2025.

[SIDE 2]
7.1 Bad - overflater TG2
VURDERING Gulvet har utilstrekkelig fall mot sluk.
ÅRSAK Forholdet skyldes opprinnelig utførelse.
RISIKO Vann kan bli liggende på gulvet.
KONSEKVENS Dette kan føre til fuktskade.
ANBEFALT TILTAK Det anbefales å etablere korrekt fall ved rehabilitering.
"""


class FakeExtractor:
    def __init__(self, payload, metadata=None):
        self.payload = payload
        self.metadata = metadata or {"model_name": "fake-a2", "temperature": 0.0}
        self.calls = 0

    def extract_candidates(self, **kwargs):
        self.calls += 1
        return self.payload, self.metadata


def _valid_payload():
    return {
        "facts": [
            {
                "candidate_id": "inspection-date",
                "fact_type": "inspection_date",
                "raw_value": "02.07.2026",
                "normalized_value": "2026-07-02",
                "confidence": 0.99,
                "evidence": {
                    "exact_quote": "Befaringsdato: 02.07.2026",
                    "page": 1,
                    "claimed_char_start": 0,
                    "claimed_char_end": 10
                }
            },
            {
                "candidate_id": "declared-standard",
                "fact_type": "declared_standard",
                "raw_value": "NS 3600:2025",
                "normalized_value": "NS 3600:2025",
                "confidence": 0.98,
                "evidence": {
                    "exact_quote": "Rapporten erklærer NS 3600:2025.",
                    "page": 1,
                    "claimed_char_start": None,
                    "claimed_char_end": None
                }
            }
        ],
        "segments": [
            {
                "candidate_id": "point-7-1",
                "kind": "report_point",
                "title": "Bad - overflater",
                "professional_subject": "våtrom",
                "point_label": "7.1",
                "tg_grade": "TG2",
                "confidence": 0.97,
                "evidence": {
                    "exact_quote": "7.1 Bad - overflater TG2",
                    "page": 2,
                    "claimed_char_start": None,
                    "claimed_char_end": None
                }
            }
        ],
        "abstentions": []
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a1_contracts_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        CandidateEvidence(exact_quote="text", page=1, unexpected=True)


def test_page_spans_preserve_global_source_offsets():
    pages = split_page_spans(REPORT_TEXT)
    assert [page.page for page in pages] == [1, 2]
    for page in pages:
        assert REPORT_TEXT[page.char_start:page.char_end] == page.text


def test_a2_validates_exact_evidence_and_recovers_bad_claimed_offsets():
    extractor = FakeExtractor(_valid_payload())
    result = DocumentUnderstandingService(extractor).analyze(REPORT_TEXT, "unknown-layout.pdf")

    assert result.status == UnderstandingStatus.COMPLETE
    assert extractor.calls == 1
    assert len(result.facts) == 3
    assert any(
        item.fact_type.value == "report_date"
        and item.normalized_value == "2026-07-03"
        and "deterministic_explicit_date_label" in item.validation_notes
        for item in result.facts
    )
    assert len(result.segments) == 1
    assert all(item.validation_status == ValidationStatus.VALIDATED for item in result.facts)
    assert result.segments[0].validation_status == ValidationStatus.VALIDATED
    inspection = next(item for item in result.facts if item.fact_type == FactType.INSPECTION_DATE)
    assert inspection.normalized_value == "2026-07-02"
    assert "offsets_recovered_from_exact_quote" in inspection.evidence.validation_notes
    assert REPORT_TEXT[inspection.evidence.char_start:inspection.evidence.char_end] == inspection.evidence.exact_quote
    assert len(result.trace_records) == 5
    assert result.segment_coverage.dispositions_count == 1
    assert result.candidate_dispositions[0].outcome.value == "admitted"


def test_a2_rejects_hallucinated_evidence_and_conflicting_normalized_date():
    payload = _valid_payload()
    payload["facts"][0]["normalized_value"] = "2026-08-02"
    payload["segments"][0]["evidence"]["exact_quote"] = "This text does not exist"
    result = DocumentUnderstandingService(FakeExtractor(payload)).analyze(REPORT_TEXT, "report.pdf")

    assert result.status == UnderstandingStatus.COMPLETE
    assert all(item.validation_status == ValidationStatus.VALIDATED for item in result.facts)
    assert ("report_date", "2026-08-02") not in {
        (item.fact_type.value, item.normalized_value) for item in result.facts
    }
    assert result.segments[0].validation_status == ValidationStatus.VALIDATED
    assert "ai_candidate_semantically_reconciled_without_exact_evidence" in result.segments[0].validation_notes
    assert not result.abstentions
    assert result.candidate_dispositions[0].outcome.value == "abstained"


def test_general_ai_provider_candidate_requires_structural_confirmation():
    payload = _valid_payload()
    payload["facts"].append({
        "candidate_id": "provider",
        "fact_type": "provider",
        "raw_value": "Eksempel Takst AS",
        "normalized_value": "Eksempel Takst AS",
        "confidence": 0.99,
        "evidence": {
            "exact_quote": "Tilstandsrapport fra Eksempel Takst AS",
            "page": 1,
            "claimed_char_start": None,
            "claimed_char_end": None,
        },
    })
    result = DocumentUnderstandingService(FakeExtractor(payload)).analyze(REPORT_TEXT, "report.pdf")
    provider = next(item for item in result.facts if item.fact_type == FactType.PROVIDER)
    assert provider.validation_status == ValidationStatus.AMBIGUOUS
    assert "provider_requires_structural_confirmation" in provider.validation_notes

    confirmed = DocumentUnderstandingService(
        FakeExtractor(payload),
        provider_identity_verifier=lambda candidate, report_text: (
            candidate.raw_value == "Eksempel Takst AS" and "Tilstandsrapport fra Eksempel Takst AS" in report_text
        ),
    ).analyze(REPORT_TEXT, "report.pdf")
    confirmed_provider = next(item for item in confirmed.facts if item.fact_type == FactType.PROVIDER)
    assert confirmed_provider.validation_status == ValidationStatus.VALIDATED
    assert "provider_structurally_confirmed" in confirmed_provider.validation_notes


def test_structural_provider_label_self_confirms_provider_fact():
    payload = _valid_payload()
    payload["facts"].append({
        "candidate_id": "provider",
        "fact_type": "provider",
        "raw_value": "Eksempel Takst AS",
        "normalized_value": "Eksempel Takst AS",
        "confidence": 0.99,
        "evidence": {
            "exact_quote": "Autorisert foretak: Eksempel Takst AS",
            "page": 1,
            "claimed_char_start": None,
            "claimed_char_end": None,
        },
    })
    report = REPORT_TEXT.replace(
        "Tilstandsrapport fra Eksempel Takst AS",
        "Autorisert foretak: Eksempel Takst AS",
    )
    result = DocumentUnderstandingService(FakeExtractor(payload)).analyze(report, "report.pdf")
    provider = next(item for item in result.facts if item.fact_type == FactType.PROVIDER)
    assert provider.validation_status == ValidationStatus.VALIDATED
    assert "provider_structurally_confirmed" in provider.validation_notes


def test_declared_standard_accepts_generic_ns_citation():
    payload = _valid_payload()
    payload["facts"][1] = {
        "candidate_id": "declared-standard",
        "fact_type": "declared_standard",
        "raw_value": "Norsk standard 3940:2023",
        "normalized_value": None,
        "confidence": 0.98,
        "evidence": {
            "exact_quote": "Arealmålinger og arealoppsett er basert på Norsk standard 3940:2023.",
            "page": 2,
            "claimed_char_start": None,
            "claimed_char_end": None,
        },
    }
    report = REPORT_TEXT + "\nArealmålinger og arealoppsett er basert på Norsk standard 3940:2023.\n"
    result = DocumentUnderstandingService(FakeExtractor(payload)).analyze(report, "report.pdf")
    standard = next(item for item in result.facts if item.fact_type == FactType.DECLARED_STANDARD)
    assert standard.validation_status == ValidationStatus.VALIDATED
    assert standard.normalized_value == "NS 3940:2023"


def test_provider_fast_path_is_optional_and_general_path_handles_unknown_layout():
    empty_fast_path = FakeExtractor({"facts": [], "segments": [], "abstentions": []})
    general = FakeExtractor(_valid_payload())
    result = DocumentUnderstandingService(
        general,
        fast_path_extractor=empty_fast_path,
    ).analyze(REPORT_TEXT, "previously-unseen-provider.pdf")

    assert empty_fast_path.calls == 1
    assert general.calls == 1
    assert result.status == UnderstandingStatus.COMPLETE
    assert len(result.segments) == 1


def test_report_point_anchor_miss_does_not_leave_abstention_when_source_inventory_materializes_point():
    payload = _valid_payload()
    payload["segments"][0]["evidence"]["exact_quote"] = "Bad - overflater TG2\nThis exact quote is not present"
    result = DocumentUnderstandingService(FakeExtractor(payload)).analyze(REPORT_TEXT, "report.pdf")

    assert result.segments[0].validation_status == ValidationStatus.VALIDATED
    assert "ai_candidate_semantically_reconciled_without_exact_evidence" in result.segments[0].validation_notes
    assert not any(
        item.stage == "segment_validation" and item.reason_code == "exact_quote_not_found"
        for item in result.abstentions
    )
    assert result.candidate_dispositions[0].outcome.value == "abstained"


def test_whitespace_normalized_match_maps_back_to_exact_original_source():
    report = "[SIDE 1]\n3. Terrengforhold   TG3 – Store avvik\nVURDERING\nMotfall mot grunnmur.\n"
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
            "evidence": {
                "exact_quote": "3. Terrengforhold TG3 – Store avvik\nVURDERING Motfall mot grunnmur.",
                "page": 1,
                "claimed_char_start": None,
                "claimed_char_end": None,
            },
        }],
        "abstentions": [],
    }
    result = DocumentUnderstandingService(FakeExtractor(payload)).analyze(
        report,
        "report.pdf",
        source_pdf_sha256="a" * 64,
    )
    segment = result.segments[0]
    assert segment.validation_status == ValidationStatus.VALIDATED
    assert segment.evidence.match_method == "exact"
    assert report[segment.evidence.char_start:segment.evidence.char_end] == segment.evidence.exact_quote
    assert segment.evidence.exact_quote == "3. Terrengforhold   TG3 – Store avvik\nVURDERING\nMotfall mot grunnmur."
    assert result.source_pdf_sha256 == "a" * 64
    assert result.extracted_text_sha256 == result.document_hash
    assert result.segment_coverage.completion_blockers == []


def test_report_point_keeps_anchor_and_binds_complete_body_for_semantic_assessment():
    result = DocumentUnderstandingService(FakeExtractor(_valid_payload())).analyze(REPORT_TEXT, "report.pdf")
    point = result.segments[0]
    assert point.evidence.exact_quote.startswith("7.1 Bad - overflater TG2\nVURDERING")
    assert len(point.bound_body_spans) == 1
    body = point.bound_body_spans[0]
    assert "ÅRSAK Forholdet skyldes opprinnelig utførelse." in body.exact_quote
    assert "ANBEFALT TILTAK Det anbefales" in body.exact_quote
    assert REPORT_TEXT[body.char_start:body.char_end] == body.exact_quote
    assert point.bound_body_sha256 == body.quote_sha256


def test_declared_tg_summary_mismatch_is_a_completion_blocker_not_a2_rejection():
    report = REPORT_TEXT.replace("[SIDE 2]", "TG1: 11\n\n[SIDE 2]")
    result = DocumentUnderstandingService(FakeExtractor(_valid_payload())).analyze(report, "report.pdf")
    assert result.status == UnderstandingStatus.COMPLETE_WITH_ABSTENTIONS
    assert "declared_tg_summary_mismatch:TG1:declared=11:bound=0" in result.segment_coverage.completion_blockers


def test_non_contiguous_candidate_is_represented_as_explicit_source_spans():
    report = (
        "[SIDE 1]\nKjøkken TG2 – Vesentlige avvik\nVURDERING\n"
        "Kobberrør og avløpsrør av plast.\n"
        "Det er ikke registrert lekkasjer.\n"
        "Det er ikke etablert automatisk vannstopp.\n"
    )
    payload = {
        "facts": [],
        "segments": [{
            "candidate_id": "kitchen-tg2",
            "kind": "report_point",
            "title": "Kjøkken",
            "professional_subject": "kjøkken",
            "point_label": "8.1",
            "tg_grade": "TG2",
            "confidence": 0.99,
            "evidence": {
                "exact_quote": (
                    "Kjøkken TG2 – Vesentlige avvik\nVURDERING\n"
                    "Kobberrør og avløpsrør av plast.\n"
                    "Det er ikke etablert automatisk vannstopp."
                ),
                "page": 1,
                "claimed_char_start": None,
                "claimed_char_end": None,
            },
        }],
        "abstentions": [],
    }
    result = DocumentUnderstandingService(FakeExtractor(payload)).analyze(report, "report.pdf")
    segment = result.segments[0]
    assert segment.validation_status == ValidationStatus.VALIDATED
    assert len(segment.evidence_spans) == 2
    assert all(span.match_method == "multi_span_normalized" for span in segment.evidence_spans)
    assert all(report[span.char_start:span.char_end] == span.exact_quote for span in segment.evidence_spans)


def test_primary_text_is_selected_when_table_extraction_duplicates_same_quote():
    quote = "Kjøkken TG1 – Svake avvik\nVURDERING\nInnredningen har normal bruksslitasje."
    report = f"[SIDE 1]\n{quote}\n\n[TABELLDATA]\n{quote}\n"
    payload = {
        "facts": [],
        "segments": [{
            "candidate_id": "kitchen-tg1",
            "kind": "report_point",
            "title": "Kjøkken",
            "professional_subject": "kjøkken",
            "point_label": "8.1",
            "tg_grade": "TG1",
            "confidence": 0.99,
            "evidence": {
                "exact_quote": quote,
                "page": 1,
                "claimed_char_start": None,
                "claimed_char_end": None,
            },
        }],
        "abstentions": [],
    }
    result = DocumentUnderstandingService(FakeExtractor(payload)).analyze(report, "report.pdf")
    segment = result.segments[0]
    assert segment.validation_status == ValidationStatus.VALIDATED
    assert segment.evidence.char_start < report.index("[TABELLDATA]")
    assert "physical_source_inventory_reversible_page_span" in segment.evidence.validation_notes


def test_segment_identity_is_source_derived_across_changed_ai_identity_and_wording():
    payload = _valid_payload()
    tg1 = payload["segments"][0]
    tg1["title"] = "Kjøkken"
    tg1["professional_subject"] = "kjøkken"
    tg1["tg_grade"] = "TG1"
    tg1["evidence"]["exact_quote"] = "Tilstandsrapport fra Eksempel Takst AS"
    tg1["evidence"]["page"] = 1
    first = DocumentUnderstandingService(FakeExtractor(_valid_payload())).analyze(REPORT_TEXT, "report.pdf")
    changed = _valid_payload()
    changed["segments"][0]["candidate_id"] = "entirely-different-ai-id"
    changed["segments"][0]["title"] = "AI wording changed"
    second = DocumentUnderstandingService(FakeExtractor(changed)).analyze(REPORT_TEXT, "report.pdf")
    assert first.segments[0].segment_id == second.segments[0].segment_id
    assert first.segments[0].bound_body_sha256 == second.segments[0].bound_body_sha256


def test_every_raw_candidate_has_disposition_and_duplicate_trace():
    payload = _valid_payload()
    payload["segments"].append(json.loads(json.dumps(payload["segments"][0])))
    result = DocumentUnderstandingService(FakeExtractor(payload)).analyze(REPORT_TEXT, "report.pdf")
    assert len(result.candidate_dispositions) == 2
    assert {item.outcome.value for item in result.candidate_dispositions} == {"admitted", "duplicate"}
    assert result.segment_coverage.raw_candidate_count == 2
    assert result.segment_coverage.unique_candidate_count == 1
    disposition_traces = [item for item in result.trace_records if item.entity_type == "candidate_disposition"]
    assert len(disposition_traces) == 2


def test_unresolved_tg3_point_blocks_complete_understanding_state():
    payload = _valid_payload()
    payload["segments"][0]["tg_grade"] = "TG3"
    payload["segments"][0]["evidence"]["exact_quote"] = "TG3 candidate not present in source"
    result = DocumentUnderstandingService(FakeExtractor(payload)).analyze(REPORT_TEXT, "report.pdf")
    assert result.status == UnderstandingStatus.COMPLETE
    assert result.candidate_dispositions[0].outcome.value == "abstained"
    assert result.segments[0].tg_grade == "TG2"
    assert "source_inventory_materialized_without_ai_candidate" in result.segments[0].validation_notes


def test_non_authoritative_structural_tg_does_not_override_validated_tg3_candidate():
    report = """[SIDE 1]
Taktekking
Beskrivelse
Eldre tekking.
Vurdering av avvik:
Avvik.
"""
    payload = {
        "facts": [],
        "segments": [{
            "candidate_id": "roof-tg3",
            "kind": "report_point",
            "title": "Taktekking",
            "professional_subject": "taktekking",
            "tg_grade": "TG3",
            "confidence": 0.99,
            "evidence": {"exact_quote": "Taktekking", "page": 1},
        }],
        "abstentions": [],
    }
    result = DocumentUnderstandingService(FakeExtractor(payload)).analyze(report, "report.pdf")
    point = next(item for item in result.segments if item.kind.value == "report_point")
    assert point.tg_grade == "TG3"
    assert "ai_candidate_matched" in point.validation_notes
    assert "non_authoritative_structural_tg_replaced_by_ai_candidate" in point.validation_notes


def test_deterministic_date_facts_distinguish_inspection_and_report_dates():
    report = """[SIDE 1]
Befarings - og eiendomsopplysninger
Befaring
Dato Til stede Rolle
10.6.2026 Andreas Natvig Takstingeniør
[SIDE 2]
Revisjoner
Versjon Ny versjon Kommentar
1 16.06.2026
For gyldighet på rapporten se forside
"""
    result = DocumentUnderstandingService(FakeExtractor({"facts": [], "segments": [], "abstentions": []})).analyze(report, "report.pdf")
    facts = {(item.fact_type.value, item.normalized_value) for item in result.facts}
    assert ("inspection_date", "2026-06-10") in facts
    assert ("report_date", "2026-06-16") in facts


def test_extractor_failure_fails_closed_without_leaking_an_unvalidated_result():
    class BrokenExtractor:
        def extract_candidates(self, **kwargs):
            raise RuntimeError("model unavailable with sensitive detail")

    result = DocumentUnderstandingService(BrokenExtractor()).analyze(REPORT_TEXT, "report.pdf")
    assert result.status == UnderstandingStatus.FAILED
    assert all("deterministic_explicit_date_label" in item.validation_notes for item in result.facts)
    assert result.segments
    assert all("source_inventory_materialized_without_ai_candidate" in item.validation_notes for item in result.segments)
    assert result.abstentions[0].reason_code == "candidate_extraction_failed"
    assert "sensitive detail" not in result.abstentions[0].explanation


def test_regime_interface_remains_pending_for_every_rule_category():
    facts = DocumentUnderstandingService(FakeExtractor(_valid_payload())).analyze(
        REPORT_TEXT, "report.pdf"
    ).facts
    resolver = PendingGovernedRegimeResolver()
    for category in RuleCategory:
        resolution = resolver.resolve(category, facts)
        assert resolution.status == RegimeResolutionStatus.PENDING_GOVERNED_DECISION
        assert resolution.regime_id is None
        assert resolution.controlling_fact_ids == []


def test_a1_a2_integration_boundary_is_disabled_and_shadow_only():
    service = DocumentUnderstandingService(FakeExtractor(_valid_payload()))
    disabled = PhaseAPipeline(service)
    with pytest.raises(PhaseANotEnabledError):
        disabled.understand_document(REPORT_TEXT, "report.pdf")

    shadow = PhaseAPipeline(service, PhaseAFeaturePolicy(enabled=True, shadow_only=True))
    result = shadow.understand_document(REPORT_TEXT, "report.pdf")
    assert result.status == UnderstandingStatus.COMPLETE
    with pytest.raises(PhaseAPublicationNotAuthorizedError):
        shadow.build_customer_payload(result)

    incorrectly_publishable = PhaseAPipeline(
        service,
        PhaseAFeaturePolicy(enabled=True, shadow_only=False),
    )
    with pytest.raises(PhaseAPublicationNotAuthorizedError):
        incorrectly_publishable.understand_document(REPORT_TEXT, "report.pdf")


def test_v46_runtime_and_signed_reference_artifacts_are_frozen():
    registry = json.loads(
        (ROOT / "tests/fixtures/v46_signed_baseline_registry.json").read_text(encoding="utf-8")
    )
    assert _sha256(ROOT / registry["active_manifest"]["path"]) == registry["active_manifest"]["sha256"]
    for entry in registry["runtime_code_files"]:
        assert _sha256(ROOT / entry["path"]) == entry["sha256"], entry["path"]

    for entry in registry["signed_reference_artifacts"]:
        path = ROOT / entry["path"]
        assert _sha256(path) == entry["sha256"], entry["path"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        output = payload["analysis_output"]
        expected = entry["expected"]
        invariants = output.get("policy_invariants") or []
        findings = output.get("all_findings") or []
        assert output["analysis_mode"] == expected["analysis_mode"]
        assert len(payload["dommer_b_full"]["points"]) == expected["point_count"]
        assert len(findings) == expected["finding_count"]
        assert [str(item.get("point_id")) for item in findings] == expected["finding_point_ids"]
        assert len(invariants) == expected["invariant_count"]
        assert all(bool(item.get("passed")) for item in invariants) is expected["all_invariants_pass"]
