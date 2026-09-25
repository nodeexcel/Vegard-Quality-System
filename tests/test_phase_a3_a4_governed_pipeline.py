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
    DeterministicAssessmentValidator,
    PhaseA4ShadowService,
    _adjudication_instruction_for_categories,
    _assessment_segments_with_linked_summaries,
    _split_compound_tgiu_candidate,
    _normalize_semantic_candidate,
    _should_refresh_konsekvens_replay,
    _should_refresh_risk_replay,
    _should_refresh_tiltak_replay,
    _semantic_anbefalt_tiltak_present,
    _semantic_aarsak_rationale_present,
    _semantic_diagnostics_payload,
    _semantic_konsekvens_supporting_quotes,
    _semantic_konsekvens_present,
    _semantic_legality_present,
    _semantic_risiko_supporting_quotes,
    _semantic_risiko_present,
    _semantic_governance_context_payload,
    _validate_tgiu_candidate_coverage,
)
from app.services.phase_a_applicability import DeterministicApplicabilityPlanner
from app.services.phase_a_contracts import (
    AssessmentCandidate,
    AssessmentDecision,
    FindingAdmission,
    RegimeResolution,
    RegimeResolutionStatus,
    RuleCategory,
    RuleRetrievalRecord,
    StructuredAssessment,
    ValidatedSegment,
)
from app.services.phase_a_document_understanding import DocumentUnderstandingService
from app.services.phase_a_governed_retrieval import (
    GovernedAssetError,
    ManifestGovernedCatalog,
    ManifestVerifiedRuleRetriever,
)
from app.services.validert_files import get_dommer_b_system_prompt_text


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


def test_semantic_risiko_recognizes_hidden_wetroom_solution_uncertainty_as_technical_risk():
    quote = (
        "RISIKO\nManglande dokumentasjon gir usikkerheit om materialval, oppbygging og utføring "
        "av skjulte våtromsløysingar."
    )
    span = {
        "evidence_id": "evidence_risk_variant_002", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_risk_variant_02", "kind": "report_point", "title": "Dokumentasjon for våtrom",
        "section_context": "VÅTROM", "professional_subject": "Dokumentasjon for våtrom", "point_label": "7.4",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Dokumentasjon for våtrom", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_risiko_present(segment)


@pytest.mark.parametrize("quote", [
    "Vurdering av avvik: Mer enn halvparten av forventet funksjonstid for røranlegg er oppnådd.",
    "Vurdering av avvik: Det er ikke påvist tilfredsstillende el-tilkobling av varmtvannstank.",
    "Vurdering av avvik: Det er registrert fuktmerker på undertak ved gjennomføring.",
])
def test_semantic_aarsak_rationale_signal_recognizes_general_rationale_classes(quote):
    span = {
        "evidence_id": "evidence_aarsak_signal_001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_aarsak_signal_01", "kind": "report_point", "title": "Komponent",
        "section_context": "UTVENDIG", "professional_subject": "Komponent", "point_label": "10.1",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Komponent", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_aarsak_rationale_present(segment)


@pytest.mark.parametrize("quote", [
    "Vurdering av avvik: Det er sprekk i bakplate i ovnen.",
    "Vurdering av avvik: Terrenget er flatt og faller inn mot bygningen.",
    "Vurdering av avvik: Eiendommen ligger i et flomutsatt område.",
])
def test_semantic_aarsak_rationale_signal_recognizes_observation_rationale_variants(quote):
    span = {
        "evidence_id": "evidence_aarsak_signal_002", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_aarsak_signal_02", "kind": "report_point", "title": "Komponent",
        "section_context": "UTVENDIG", "professional_subject": "Komponent", "point_label": "10.2",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Komponent", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_aarsak_rationale_present(segment)


@pytest.mark.parametrize("quote", [
    "Konsekvens/tiltak: Det bør etableres tilfredsstillende el-tilkobling etter gjeldende forskrift.",
    "Konsekvens/tiltak: Gjennomføringer i tak må kontrolleres for å kartlegge årsak til misfarging/fuktmerker.",
    "Konsekvens/tiltak: Det anbefales å etablere kjøkkenventilator med avtrekk ut i det fri over komfyr.",
])
def test_semantic_anbefalt_tiltak_signal_recognizes_combined_field_actions(quote):
    span = {
        "evidence_id": "evidence_tiltak_signal_001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_tiltak_signal_01", "kind": "report_point", "title": "Komponent",
        "section_context": "UTVENDIG", "professional_subject": "Komponent", "point_label": "10.1",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Komponent", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_anbefalt_tiltak_present(segment)


def test_semantic_anbefalt_tiltak_signal_recognizes_hold_under_observation_wording():
    quote = "Konsekvens/tiltak: Ingen behov for å utbedre dette, men anbefales å holdes under oppsikt."
    span = {
        "evidence_id": "evidence_tiltak_signal_002", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_tiltak_signal_02", "kind": "report_point", "title": "Tak",
        "section_context": "UTVENDIG", "professional_subject": "Tak", "point_label": "11",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Tak", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_anbefalt_tiltak_present(segment)


@pytest.mark.parametrize("quote", [
    "Konsekvens/tiltak: Det kan ikke utelukkes at skjulte fuktforhold eller skader foreligger i konstruksjonen.",
    "Konsekvens/tiltak: Konsekvens er merker eller skader på karm og dørblad.",
    "Risiko/Konsekvens: Over tid kan dette medføre dårligere inneklima, høyere luftfuktighet og biologisk vekst i utsatte konstruksjoner.",
])
def test_semantic_konsekvens_signal_recognizes_hidden_damage_or_practical_effect(quote):
    span = {
        "evidence_id": "evidence_konsekvens_signal_001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_konsekvens_signal_01", "kind": "report_point", "title": "Komponent",
        "section_context": "UTVENDIG", "professional_subject": "Komponent", "point_label": "10.1",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Komponent", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_konsekvens_present(segment)


def test_semantic_risiko_does_not_treat_room_use_impact_alone_as_technical_risk():
    quote = (
        "Risiko/Konsekvens: Slike forhold kan påvirke funksjon og bruk av rommet. "
        "Anbefalte tiltak: Utbedring bør vurderes ved behov."
    )
    span = {
        "evidence_id": "evidence_risk_use_only_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_risk_use_only_0001", "kind": "report_point", "title": "Etasjeskiller",
        "section_context": "INNVENDIG", "professional_subject": "Etasjeskiller", "point_label": "3",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Etasjeskiller", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_risiko_supporting_quotes(segment) == []
    assert _semantic_risiko_present(segment) is False


def test_semantic_konsekvens_does_not_treat_documentation_uncertainty_alone_as_substantive_consequence():
    quote = (
        "Risiko/Konsekvens: Manglende dokumentasjon medfører at utførelse, lekkasjesikring og "
        "inspeksjonsmuligheter ikke kan verifiseres. Eventuelle skjulte feil, mangler eller lekkasjer "
        "kan derfor ikke utelukkes, noe som medfører økt usikkerhet ved vurderingen av installasjonens "
        "tilstand."
    )
    span = {
        "evidence_id": "evidence_konsekvens_limitation_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_konsekvens_limitation_0001", "kind": "report_point", "title": "Sanitaer og ror",
        "section_context": "BAD", "professional_subject": "Sanitaer og ror", "point_label": "3.1.4",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Sanitaer og ror", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_konsekvens_supporting_quotes(segment) == []
    assert _semantic_konsekvens_present(segment) is False


def test_semantic_legality_signal_recognizes_buyer_use_and_follow_up_context():
    quote = (
        "Det foreligger godkjente tegninger, men de stemmer ikke med dagens bruk. "
        "En kjøper av eiendommen oppfordres derfor til å sjekke planbestemmelser "
        "som gjelder eiendommen for å skaffe informasjon om disse inneholder "
        "bestemmelser av betydning for kjøpers bruk og utvikling av eiendommen."
    )
    span = {
        "evidence_id": "evidence_legality_signal_001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_legality_signal_01", "kind": "report_point", "title": "Lovlighet",
        "section_context": "", "professional_subject": "Lovlighet", "point_label": "30",
        "tg_grade": None, "point_type": "legality_no_tg", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Lovlighet", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_legality_present(segment)


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


def test_missing_risiko_stays_missing_for_operational_adjustment_only_point():
    quote = (
        "Vurdering av avvik: Det er påvist monteringsavvik og skjevheter på enkelte dører. "
        "Konsekvens/tiltak: Enkelte dører må justeres. Dør til bod går i anslaget ved funksjonsprøving."
    )
    span = {
        "evidence_id": "evidence_risk_operational_only_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_risk_operational_only_01", "kind": "report_point", "title": "Innvendige dører",
        "section_context": "INNVENDIG", "professional_subject": "Innvendige dører", "point_label": "11.1",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Innvendige dører", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id,
        retrieval_ids=[],
        rule_category=RuleCategory.RISIKO,
        decision=AssessmentDecision.DEFICIENT,
        explanation="Missing risk.",
        evidence_ids=[span["evidence_id"]],
        proposed_finding_type="MISSING (risiko)",
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == "MISSING (risiko)"


def test_missing_consequence_normalizes_to_satisfied_for_functional_impairment_wording():
    quote = (
        "Vurdering av avvik: Det er påvist monteringsavvik og skjevheter på enkelte dører. "
        "Konsekvens/tiltak: Enkelte dører må justeres. Dør til bod går i anslaget ved funksjonsprøving."
    )
    span = {
        "evidence_id": "evidence_consequence_operational_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_consequence_operational_01", "kind": "report_point", "title": "Innvendige dører",
        "section_context": "INNVENDIG", "professional_subject": "Innvendige dører", "point_label": "11.1",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Innvendige dører", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id,
        retrieval_ids=[],
        rule_category=RuleCategory.KONSEKVENS,
        decision=AssessmentDecision.DEFICIENT,
        explanation="Missing consequence.",
        evidence_ids=[span["evidence_id"]],
        proposed_finding_type="MISSING (konsekvens)",
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.SATISFIED
    assert normalized.proposed_finding_type is None


def test_satisfied_risiko_normalizes_to_missing_for_surface_flaking_use_only_wording():
    quote = (
        "Konsekvens/tiltak: Det anbefales å kontrollere fallforholdene. "
        "Det oppstår flassing av maling ved bruk av baderommet over tid."
    )
    span = {
        "evidence_id": "evidence_risk_flaking_only_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_risk_flaking_only_01", "kind": "report_point", "title": "Overflater gulv",
        "section_context": "BAD", "professional_subject": "Overflater gulv", "point_label": "1.3",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Overflater gulv", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id,
        retrieval_ids=[],
        rule_category=RuleCategory.RISIKO,
        decision=AssessmentDecision.SATISFIED,
        explanation="Risk present.",
        evidence_ids=[span["evidence_id"]],
        proposed_finding_type=None,
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == "MISSING (risiko)"


def test_missing_konsekvens_normalizes_to_satisfied_for_measurement_only_text():
    quote = (
        "Konsekvens/tiltak: Det er målt 31mm retningsavvik på gulv i gang. "
        "25 mm retningsavvik på gulv på soverom. Dette anses som vesentlig skjevheter."
    )
    span = {
        "evidence_id": "evidence_konsekvens_measurement_only_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_konsekvens_measurement_only_01", "kind": "report_point", "title": "Etasjeskiller",
        "section_context": "INNVENDIG", "professional_subject": "Etasjeskiller", "point_label": "3.0",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Etasjeskiller", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id,
        retrieval_ids=[],
        rule_category=RuleCategory.KONSEKVENS,
        decision=AssessmentDecision.DEFICIENT,
        explanation="Missing consequence.",
        evidence_ids=[span["evidence_id"]],
        proposed_finding_type="MISSING (konsekvens)",
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.SATISFIED
    assert normalized.proposed_finding_type is None


def test_refresh_risk_replay_targets_safety_airflow_and_present_state_boundaries():
    payload = {
        "complete_bound_body": [
            {"exact_quote": "Det anbefales å montere håndløper for å bedre sikkerhet og tilgjengelighet."},
        ]
    }
    assert not _should_refresh_risk_replay(payload)

    payload = {
        "complete_bound_body": [
            {"exact_quote": "Det oppstår flassing av maling ved bruk av baderommet over tid."},
        ]
    }
    assert not _should_refresh_risk_replay(payload)


def test_refresh_konsekvens_replay_targets_purpose_airflow_and_present_state_boundaries():
    payload = {
        "complete_bound_body": [
            {"exact_quote": "Dagtanken bør fjernes for å unngå potensiell miljø- og forurensningsrisiko."},
        ]
    }
    assert not _should_refresh_konsekvens_replay(payload)

    payload = {
        "complete_bound_body": [
            {"exact_quote": "Det er ikke tilluft til vaskerommet, og ventilasjon bør etableres for å sikre tilstrekkelig luftutskifting."},
        ]
    }
    assert not _should_refresh_konsekvens_replay(payload)


def test_adjudication_instruction_locks_imperative_form_boundary():
    instruction = _adjudication_instruction_for_categories([RuleCategory.ANBEFALT_TILTAK])
    assert "må utføres" in instruction
    assert "må skiftes" in instruction
    assert "må påregnes" in instruction
    assert "does not do so automatically" in instruction


def test_risk_replay_targets_safety_wording_in_measure_prose():
    payload = {
        "complete_bound_body": [
            {
                "exact_quote": "Det anbefales å montere håndløper for å bedre sikkerhet og tilgjengelighet."
            }
        ]
    }
    assert not _should_refresh_risk_replay(payload)


def test_konsekvens_replay_targets_purpose_wording_without_standalone_effect():
    payload = {
        "complete_bound_body": [
            {
                "exact_quote": "Det anbefales å etablere ventilasjon/tilluftsløsning for å sikre tilstrekkelig luftutskifting."
            }
        ]
    }
    assert not _should_refresh_konsekvens_replay(payload)


def test_tiltak_replay_targets_direct_execution_orders_only():
    payload = {
        "complete_bound_body": [
            {"exact_quote": "Lokal utbedring må utføres. Enkelte dører må justeres."},
        ]
    }
    assert not _should_refresh_tiltak_replay(payload)

    payload = {
        "complete_bound_body": [
            {"exact_quote": "Utbedringer må påregnes og tiltak bør vurderes ved behov."},
        ]
    }
    assert not _should_refresh_tiltak_replay(payload)


def test_missing_aarsak_normalizes_to_satisfied_when_same_point_already_states_rationale():
    quote = "Vurdering av avvik: Våtrommet er oppført etter byggeforskrift fra før 1997. Det foreligger ingen dokumentasjon."
    span = {
        "evidence_id": "evidence_age_cause_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_age_cause_0001", "kind": "report_point", "title": "Generell",
        "section_context": "BAD", "professional_subject": "Våtrom", "point_label": "1.1",
        "tg_grade": "TG3", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Generell", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_age_0001"],
        rule_category=RuleCategory.AARSAK, decision=AssessmentDecision.DEFICIENT,
        explanation="Cause is missing.", evidence_ids=[span["evidence_id"]],
        proposed_finding_type="MISSING (aarsak)",
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.SATISFIED
    assert normalized.proposed_finding_type is None


@pytest.mark.parametrize(("consequence", "expected_type"), [
    ("Har noe setningsskader og trenger utbedringer.", "TILTAK_AS_KONSEKVENS"),
    ("Risiko for videre utvikling av skade hvis forholdene vedvarer.", "RISIKO_AS_KONSEKVENS"),
    ("Økt fuktbelastning på mur.", "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS"),
])
def test_satisfied_consequence_stays_satisfied_when_same_point_contains_real_consequence(consequence, expected_type):
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
    assert normalized.decision == AssessmentDecision.SATISFIED
    assert normalized.proposed_finding_type is None


def test_satisfied_consequence_normalizes_to_tiltak_as_konsekvens_when_only_action_purpose_is_present():
    quote = "Konsekvens/tiltak Det anbefales å etablere ventilasjon/tilluftsløsning for å sikre tilstrekkelig luftutskifting."
    span = {
        "evidence_id": "evidence_consequence_02", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_consequence_02", "kind": "report_point", "title": "Avtrekk/ventilasjon",
        "section_context": "TOALETTROM", "professional_subject": "Ventilasjon", "point_label": "7.2",
        "tg_grade": "TG3", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Avtrekk/ventilasjon", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_consequence_02"],
        rule_category=RuleCategory.KONSEKVENS, decision=AssessmentDecision.SATISFIED,
        explanation="Consequence is sufficient.", evidence_ids=[span["evidence_id"]],
        proposed_finding_type=None,
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == "TILTAK_AS_KONSEKVENS"


def test_technical_development_consequence_normalizes_to_satisfied_when_same_point_states_practical_effects():
    quote = (
        "Konsekvens/tiltak "
        "Avvik rundt innsettingsdetaljer kan føre til utettheter, med risiko for "
        "fuktinntrenging, trekk og varmetap."
    )
    span = {
        "evidence_id": "evidence_consequence_effects_01", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_consequence_effects_01", "kind": "report_point", "title": "Ytterdører",
        "section_context": "UTVENDIG", "professional_subject": "Ytterdører", "point_label": "3.2",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Ytterdører", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_consequence_effects_01"],
        rule_category=RuleCategory.KONSEKVENS, decision=AssessmentDecision.DEFICIENT,
        explanation="Only technical development is stated.", evidence_ids=[span["evidence_id"]],
        proposed_finding_type="TECHNICAL_DEVELOPMENT_AS_KONSEKVENS",
    )
    assert _semantic_konsekvens_present(segment) is True
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.SATISFIED
    assert normalized.proposed_finding_type is None


def test_non_triggered_methodology_abstain_normalizes_to_satisfied():
    quote = "Garasje\nAnvendelse"
    span = {
        "evidence_id": "evidence_methodology_01", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_methodology_01", "kind": "report_point", "title": "Garasje",
        "section_context": "", "professional_subject": "Garasje", "point_label": "31",
        "tg_grade": None, "point_type": "methodology_only", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Garasje", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_methodology_01"],
        rule_category=RuleCategory.METHODOLOGY, decision=AssessmentDecision.ABSTAIN,
        explanation="No methodology deficiency is triggered.", evidence_ids=[span["evidence_id"]],
        proposed_finding_type=None,
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.SATISFIED
    assert normalized.proposed_finding_type is None


def test_methodology_only_point_with_concrete_deviation_normalizes_to_garasje_rule():
    quote = (
        "Takshingel er ikke egnet for å brukes på slike lave takvinkler. "
        "Bygget er ikke tilstandsvurdert ihht Forskrift til avhendingslova og NS3600. "
        "Dette er kun en enkel beskrivelse."
    )
    span = {
        "evidence_id": "evidence_methodology_02", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_methodology_02", "kind": "report_point", "title": "Garasje",
        "section_context": "", "professional_subject": "Garasje", "point_label": None,
        "tg_grade": None, "point_type": "methodology_only", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Garasje", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_methodology_02"],
        rule_category=RuleCategory.METHODOLOGY, decision=AssessmentDecision.SATISFIED,
        explanation="No methodology deficiency is triggered.", evidence_ids=[span["evidence_id"]],
        proposed_finding_type=None,
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == "E_METHOD.garasje_avvik_uten_arkat"


def test_non_triggered_legality_abstain_normalizes_to_satisfied():
    quote = "El.billader er montert."
    span = {
        "evidence_id": "evidence_legality_01", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_legality_01", "kind": "report_point", "title": "Lovlighet",
        "section_context": "", "professional_subject": "Lovlighet", "point_label": "30",
        "tg_grade": None, "point_type": "legality_no_tg", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Lovlighet", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_legality_01"],
        rule_category=RuleCategory.LEGALITY, decision=AssessmentDecision.ABSTAIN,
        explanation="No legality deficiency is triggered.", evidence_ids=[span["evidence_id"]],
        proposed_finding_type=None,
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.SATISFIED
    assert normalized.proposed_finding_type is None


def test_risk_wrong_role_is_normalized_to_missing_when_only_use_impact_or_cause_is_present():
    quote = (
        "Risiko/Konsekvens\n"
        "Slike forhold kan påvirke funksjon og bruk av rommet. "
        "Skjevhetene kan ha sammenheng med setninger fra opprinnelig byggeår."
    )
    span = {
        "evidence_id": "evidence_risk_normalize_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_risk_normalize_0001", "kind": "report_point", "title": "Etasjeskiller",
        "section_context": "INNVENDIG", "professional_subject": "Etasjeskiller", "point_label": "3",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Etasjeskiller", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_risk_normalize_0001"],
        rule_category=RuleCategory.RISIKO, decision=AssessmentDecision.DEFICIENT,
        explanation="This is consequence/cause content rather than future technical risk.",
        evidence_ids=[span["evidence_id"]],
        proposed_finding_type="AARSAK_AS_RISIKO",
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == "MISSING (risiko)"


def test_missing_consequence_is_normalized_to_tiltak_as_konsekvens_when_only_measure_is_present():
    quote = (
        "Konsekvens/tiltak\n"
        "Taket bør kontrolleres og vedlikeholdes. Slitte deler må skiftes ut ved behov."
    )
    span = {
        "evidence_id": "evidence_konsekvens_normalize_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_konsekvens_normalize_0001", "kind": "report_point", "title": "Takkonstruksjon/Loft",
        "section_context": "UTVENDIG", "professional_subject": "Takkonstruksjon", "point_label": "11",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Takkonstruksjon/Loft", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_konsekvens_normalize_0001"],
        rule_category=RuleCategory.KONSEKVENS, decision=AssessmentDecision.DEFICIENT,
        explanation="Consequence is missing.", evidence_ids=[span["evidence_id"]],
        proposed_finding_type="MISSING (konsekvens)",
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == "TILTAK_AS_KONSEKVENS"


def test_direct_execution_order_normalizes_tiltak_to_imperative_form():
    quote = "Konsekvens/tiltak Lokal utbedring må utføres. Det anbefales å utføre vedlikehold."
    span = {
        "evidence_id": "evidence_tiltak_imperative_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_tiltak_imperative_0001", "kind": "report_point", "title": "Yttervegger",
        "section_context": "UTVENDIG", "professional_subject": "Vegg", "point_label": "12",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Yttervegger", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_tiltak_imperative_0001"],
        rule_category=RuleCategory.ANBEFALT_TILTAK, decision=AssessmentDecision.SATISFIED,
        explanation="Measure is sufficient.", evidence_ids=[span["evidence_id"]],
        proposed_finding_type=None,
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == "TILTAK_IMPERATIVE_FORM"


def test_expectation_wording_does_not_normalize_tiltak_to_imperative_form():
    quote = "Konsekvens/tiltak Det må påregnes noe vedlikehold og at enkelte vinduer må skiftes ut."
    span = {
        "evidence_id": "evidence_tiltak_imperative_0002", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_tiltak_imperative_0002", "kind": "report_point", "title": "Vinduer",
        "section_context": "UTVENDIG", "professional_subject": "Vinduer", "point_label": "10",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Vinduer", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id, retrieval_ids=["retrieval_tiltak_imperative_0002"],
        rule_category=RuleCategory.ANBEFALT_TILTAK, decision=AssessmentDecision.SATISFIED,
        explanation="Measure is sufficient.", evidence_ids=[span["evidence_id"]],
        proposed_finding_type=None,
    )
    normalized = _normalize_semantic_candidate(candidate, segment, [])
    assert normalized.decision == AssessmentDecision.SATISFIED
    assert normalized.proposed_finding_type is None


def test_semantic_adjudicator_requires_meaning_not_headings_or_exact_phrases():
    prompt = BedrockSemanticAssessmentModel.ADJUDICATION_PROMPT
    assert "Observasjonsverb som" in prompt
    assert "Rapportens faktiske TG er routing premise" in prompt
    assert "NS 3600:2025-kriterier skal ikke tilbakeanvendes" in prompt
    assert "bestemt fagperson og eksakt tidspunkt er ikke universelle semantiske krav" in prompt
    assert "TGIU_MISSING_REASON" in prompt


def test_runtime_adjudication_instruction_preserves_approved_semantic_boundaries():
    instruction = _adjudication_instruction_for_categories(
        [
            RuleCategory.AARSAK,
            RuleCategory.RISIKO,
            RuleCategory.KONSEKVENS,
            RuleCategory.ANBEFALT_TILTAK,
            RuleCategory.METHODOLOGY,
            RuleCategory.LEGALITY,
        ]
    )
    assert "Technical root-cause diagnosis is not a universal requirement." in instruction
    assert "future technical risk stated anywhere in the bound point counts" in instruction
    assert "use impact, room function impact, or costly remediation alone is not technical risk" in instruction
    assert "return MISSING for Risiko rather than CONSEQUENCE_AS_RISIKO" in instruction
    assert "return MISSING rather than AARSAK_AS_RISIKO" in instruction
    assert "If the point only says that hidden damage, defects, or leaks cannot be excluded because documentation" in instruction
    assert "Increased assessment uncertainty, need for further investigation" in instruction
    assert "do not emit TILTAK_AS_KONSEKVENS merely because the field also contains a recommendation" in instruction
    assert "return SATISFIED, not ABSTAIN" in instruction
    assert "bound point itself communicates the governed buyer-relevant legality gap" in instruction


def test_semantic_prompts_are_loaded_from_approved_governed_asset():
    expected = get_dommer_b_system_prompt_text().strip()
    assert BedrockSemanticAssessmentModel.SYSTEM_PROMPT == expected
    assert BedrockSemanticAssessmentModel.ADJUDICATION_PROMPT == expected


def test_semantic_prompt_asset_preserves_approved_no_regrading_and_edition_boundary_rules():
    prompt = BedrockSemanticAssessmentModel.SYSTEM_PROMPT
    assert "Rapportens faktiske TG er routing premise" in prompt
    assert "Ikke regrader punktet" in prompt
    assert "NS 3600:2025-kriterier skal ikke tilbakeanvendes" in prompt
    assert "bestemt fagperson og eksakt tidspunkt er ikke universelle semantiske krav" in prompt


def test_semantic_governance_context_uses_approved_examples_and_aarsak_criterion_context():
    quote = "Det registreres at enkelte vinduer går tregt ved åpning og lukking."
    span = {
        "evidence_id": "evidence_aarsak_context_0001",
        "exact_quote": quote,
        "page": 1,
        "char_start": 0,
        "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact",
        "validation_status": "validated",
        "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_aarsak_context_0001",
        "kind": "report_point",
        "title": "Vinduer",
        "section_context": "UTVENDIG",
        "professional_subject": "Vinduer",
        "point_label": "18.6",
        "tg_grade": "TG2",
        "point_type": "graded",
        "confidence": 1.0,
        "candidate_evidence": {"exact_quote": quote, "page": 1},
        "evidence": span,
        "evidence_spans": [span],
        "bound_body_spans": [span],
        "validation_status": "validated",
        "validation_notes": [],
    })
    rules = [
        SimpleNamespace(
            retrieval_id="ret_field",
            asset_path="arkat_semantic_rules_v1_3_0.json",
            rule_id="arkat_semantic_rules_v1_3_0.json#/field_definitions/aarsak",
            json_pointer="/field_definitions/aarsak",
            regime_explanation="Original report date 2026-08-11 resolves FULL_2026; applicable TG-methodology edition is NS 3600:2025.",
            content={},
        ),
        SimpleNamespace(
            retrieval_id="ret_edition",
            asset_path="arkat_semantic_rules_v1_3_0.json",
            rule_id="arkat_semantic_rules_v1_3_0.json#/edition_scope",
            json_pointer="/edition_scope",
            regime_explanation="Original report date 2026-08-11 resolves FULL_2026; applicable TG-methodology edition is NS 3600:2025.",
            content={},
        ),
    ]

    payload = _semantic_governance_context_payload(RuleCategory.AARSAK, segment, rules)

    assert payload["approved_prompt_asset"]["asset_path"] == "dommer_b_system_prompt_v14.md"
    assert payload["semantic_rule_asset"]["asset_path"] == "arkat_semantic_rules_v1_3_0.json"
    assert payload["canonical_examples_asset"]["asset_path"] == "arkat_canonical_examples_v1_3_0.json"
    assert payload["canonical_examples_asset"]["selected_examples"]
    assert payload["criterion_context"]["resolved_ns_edition"] == "NS3600:2025"
    assert any(
        item["json_pointer"] == "/edition_scope"
        for item in payload["criterion_context"]["retrieval_sources"]
    )


def test_semantic_diagnostics_payload_exposes_generic_recheck_signals():
    quote = (
        "Vurdering av avvik: Mer enn halvparten av forventet funksjonstid for røranlegg er oppnådd. "
        "Konsekvens/tiltak: Når vannrørene har oppnådd mer enn halvparten av funksjonstid blir det økt risiko "
        "for lekkasjer og vannskader. Oppgradering/vedlikehold må påregnes."
    )
    span = {
        "evidence_id": "evidence_diagnostics_0001", "exact_quote": quote, "page": 1,
        "char_start": 0, "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_diagnostics_0001", "kind": "report_point", "title": "Innvendige vannledninger",
        "section_context": "TEKNISKE INSTALLASJONER", "professional_subject": "Rør", "point_label": "20.1",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Innvendige vannledninger", "page": 1},
        "evidence": span, "evidence_spans": [span], "bound_body_spans": [span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_diagnostics_payload(RuleCategory.AARSAK, segment)["rationale_signal"] is True
    assert _semantic_diagnostics_payload(RuleCategory.RISIKO, segment)["future_risk_signal"] is True
    assert _semantic_diagnostics_payload(RuleCategory.RISIKO, segment)["risk_false_positive_signal"] is False
    assert _semantic_diagnostics_payload(RuleCategory.ANBEFALT_TILTAK, segment)["action_signal"] is True

    consequence_quote = (
        "Konsekvens/tiltak: Det kan ikke utelukkes at skjulte fuktforhold eller skader foreligger i konstruksjonen."
    )
    consequence_span = {
        "evidence_id": "evidence_diagnostics_0002", "exact_quote": consequence_quote, "page": 1,
        "char_start": 0, "char_end": len(consequence_quote),
        "quote_sha256": hashlib.sha256(consequence_quote.encode()).hexdigest(),
        "match_method": "exact", "validation_status": "validated", "validation_notes": [],
    }
    consequence_segment = ValidatedSegment.model_validate({
        "segment_id": "segment_diagnostics_0002", "kind": "report_point", "title": "Krypkjeller",
        "section_context": "INNVENDIG", "professional_subject": "Krypkjeller", "point_label": "17.1",
        "tg_grade": "TG2", "point_type": "graded", "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Krypkjeller", "page": 1},
        "evidence": consequence_span, "evidence_spans": [consequence_span], "bound_body_spans": [consequence_span],
        "validation_status": "validated", "validation_notes": [],
    })
    assert _semantic_diagnostics_payload(RuleCategory.KONSEKVENS, consequence_segment)["consequence_signal"] is True
    assert _semantic_diagnostics_payload(RuleCategory.KONSEKVENS, consequence_segment)["consequence_limitation_only_signal"] is False


def test_aarsak_product_boundary_does_not_turn_tg_mismatch_into_missing_or_abstain():
    quote = "TG3 er satt på grunn av normal bruksslitasje over tid."
    span = {
        "evidence_id": "evidence_aarsak_boundary_0001",
        "exact_quote": quote,
        "page": 1,
        "char_start": 0,
        "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact",
        "validation_status": "validated",
        "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_aarsak_boundary_0001",
        "kind": "report_point",
        "title": "Taktekking",
        "section_context": "UTVENDIG",
        "professional_subject": "Taktekking",
        "point_label": "20.2",
        "tg_grade": "TG3",
        "point_type": "graded",
        "confidence": 1.0,
        "candidate_evidence": {"exact_quote": quote, "page": 1},
        "evidence": span,
        "evidence_spans": [span],
        "bound_body_spans": [span],
        "validation_status": "validated",
        "validation_notes": [],
    })
    rules = [
        SimpleNamespace(
            retrieval_id="ret_field",
            asset_path="arkat_semantic_rules_v1_3_0.json",
            rule_id="arkat_semantic_rules_v1_3_0.json#/field_definitions/aarsak",
            json_pointer="/field_definitions/aarsak",
            regime_explanation="Original report date 2026-08-11 resolves FULL_2026; applicable TG-methodology edition is NS 3600:2025.",
            content={},
        ),
    ]

    payload = _semantic_governance_context_payload(RuleCategory.AARSAK, segment, rules)

    assert payload["criterion_context"]["resolved_ns_edition"] == "NS3600:2025"
    assert payload["criterion_context"]["routing_premise"] == "Use the report's actual TG as the routing premise. Do not re-grade."
    assert "criterion_resolution" not in payload["criterion_context"]
    assert "point_specific_criterion" not in payload["criterion_context"]
    assert "tg_specific_aarsak_support" not in payload["criterion_context"]
    assert "fail_closed_instruction" not in payload["criterion_context"]


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


def test_applicability_skips_aggregate_multi_issue_container_points():
    def evidence(evidence_id, quote):
        return {
            "evidence_id": evidence_id,
            "exact_quote": quote,
            "page": 1,
            "char_start": 0,
            "char_end": len(quote),
            "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
            "match_method": "exact",
            "validation_status": "validated",
            "validation_notes": [],
        }

    aggregate_quote = (
        "TG 2 11. KJØKKEN\n"
        "1. Avvik/Årsak: Første forhold\n"
        "Risiko/Konsekvens\n"
        "2. Avvik/Årsak: Andre forhold\n"
        "Risiko/Konsekvens\n"
    )
    ordinary_quote = "TG 2 10.1 VEGGER OG HIMLINGER\nAvvik/Årsak: Slitt overflate."
    aggregate = ValidatedSegment.model_validate({
        "segment_id": "segment_aggregate_container_01",
        "kind": "report_point",
        "title": "KJØKKEN",
        "section_context": "",
        "professional_subject": "Kjøkken",
        "point_label": "11",
        "tg_grade": "TG2",
        "point_type": "graded",
        "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "KJØKKEN", "page": 1},
        "evidence": evidence("evidence_aggregate_01", aggregate_quote),
        "evidence_spans": [evidence("evidence_aggregate_01", aggregate_quote)],
        "bound_body_spans": [evidence("evidence_aggregate_01", aggregate_quote)],
        "validation_status": "validated",
        "validation_notes": [],
    })
    ordinary = ValidatedSegment.model_validate({
        "segment_id": "segment_regular_point_01",
        "kind": "report_point",
        "title": "VEGGER OG HIMLINGER",
        "section_context": "10. VASKEROM",
        "professional_subject": "Våtrom",
        "point_label": "10.1",
        "tg_grade": "TG2",
        "point_type": "graded",
        "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "VEGGER OG HIMLINGER", "page": 1},
        "evidence": evidence("evidence_regular_01", ordinary_quote),
        "evidence_spans": [evidence("evidence_regular_01", ordinary_quote)],
        "bound_body_spans": [evidence("evidence_regular_01", ordinary_quote)],
        "validation_status": "validated",
        "validation_notes": [],
    })

    plan = DeterministicApplicabilityPlanner().plan([aggregate, ordinary])

    assert {item.segment_id for item in plan} == {"segment_regular_point_01"}


def test_tgiu_missing_reason_is_not_deterministically_rewritten_when_reason_is_explicit_in_point_body():
    quote = "Hulltaking ikke mulig da vegger er i Ytong og ikke inneholder hulrom."
    span = {
        "evidence_id": "evidence_tgiu_reason_0001",
        "exact_quote": quote,
        "page": 1,
        "char_start": 0,
        "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact",
        "validation_status": "validated",
        "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_tgiu_reason_0001",
        "kind": "report_point",
        "title": "Tilliggende konstruksjoner våtrom",
        "section_context": "1. ETASJE > VASKEROM",
        "professional_subject": "Våtrom",
        "point_label": "1.8",
        "tg_grade": "TGIU",
        "point_type": "tgiu",
        "confidence": 1.0,
        "candidate_evidence": {"exact_quote": quote, "page": 1},
        "evidence": span,
        "evidence_spans": [span],
        "bound_body_spans": [span],
        "validation_status": "validated",
        "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id,
        retrieval_ids=["retrieval_tgiu_reason_0001"],
        rule_category=RuleCategory.METHODOLOGY,
        decision=AssessmentDecision.DEFICIENT,
        explanation="The point explains why inspection was not possible.",
        evidence_ids=[span["evidence_id"]],
        proposed_finding_type="TGIU_MISSING_REASON",
    )

    normalized = _normalize_semantic_candidate(candidate, segment, [])

    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == "TGIU_MISSING_REASON"


def test_tgiu_missing_reason_is_not_normalized_away_for_bare_not_inspected_text():
    quote = "Septiktank er ikke inspisert."
    span = {
        "evidence_id": "evidence_tgiu_bare_0001",
        "exact_quote": quote,
        "page": 1,
        "char_start": 0,
        "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact",
        "validation_status": "validated",
        "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_tgiu_bare_0001",
        "kind": "report_point",
        "title": "Septiktank",
        "section_context": "TOMTEFORHOLD",
        "professional_subject": "Septiktank",
        "point_label": "Septiktank",
        "tg_grade": "TGIU",
        "point_type": "tgiu",
        "confidence": 1.0,
        "candidate_evidence": {"exact_quote": quote, "page": 1},
        "evidence": span,
        "evidence_spans": [span],
        "bound_body_spans": [span],
        "validation_status": "validated",
        "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id,
        retrieval_ids=["retrieval_tgiu_bare_0001"],
        rule_category=RuleCategory.METHODOLOGY,
        decision=AssessmentDecision.DEFICIENT,
        explanation="No reason is given beyond the fact of non-inspection.",
        evidence_ids=[span["evidence_id"]],
        proposed_finding_type="TGIU_MISSING_REASON",
    )

    normalized = _normalize_semantic_candidate(candidate, segment, [])

    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == "TGIU_MISSING_REASON"


def test_tgiu_missing_reason_is_not_deterministically_rewritten_when_report_states_no_information_object_exists():
    quote = "Det foreligger ingen opplysninger om at det er nedgravd oljetank på eiendommen."
    span = {
        "evidence_id": "evidence_tgiu_noinfo_0001",
        "exact_quote": quote,
        "page": 1,
        "char_start": 0,
        "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact",
        "validation_status": "validated",
        "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_tgiu_noinfo_0001",
        "kind": "report_point",
        "title": "Oljetank",
        "section_context": "TOMTEFORHOLD",
        "professional_subject": "Oljetank",
        "point_label": "Oljetank",
        "tg_grade": "TGIU",
        "point_type": "tgiu",
        "confidence": 1.0,
        "candidate_evidence": {"exact_quote": quote, "page": 1},
        "evidence": span,
        "evidence_spans": [span],
        "bound_body_spans": [span],
        "validation_status": "validated",
        "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id,
        retrieval_ids=["retrieval_tgiu_noinfo_0001"],
        rule_category=RuleCategory.METHODOLOGY,
        decision=AssessmentDecision.DEFICIENT,
        explanation="The point does not explain why the oil tank was not investigated.",
        evidence_ids=[span["evidence_id"]],
        proposed_finding_type="TGIU_MISSING_REASON",
    )

    normalized = _normalize_semantic_candidate(candidate, segment, [])

    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == "TGIU_MISSING_REASON"


def test_tgiu_rule_coverage_requires_exactly_one_candidate_per_rule():
    candidates = [
        AssessmentCandidate(
            segment_id="segment_tgiu",
            retrieval_ids=["retrieval_reason"],
            rule_category=RuleCategory.METHODOLOGY,
            decision=AssessmentDecision.SATISFIED,
            explanation="Reason is semantically satisfied.",
            evidence_ids=["evidence_reason"],
            proposed_finding_type=None,
        ),
        AssessmentCandidate(
            segment_id="segment_tgiu",
            retrieval_ids=["retrieval_follow_up"],
            rule_category=RuleCategory.METHODOLOGY,
            decision=AssessmentDecision.DEFICIENT,
            explanation="Further investigation is missing.",
            evidence_ids=["evidence_reason"],
            proposed_finding_type="TGIU_MISSING_FURTHER_INVESTIGATION",
        ),
    ]
    rules = [
        SimpleNamespace(rule_id="TGIU_MISSING_REASON", retrieval_id="retrieval_reason"),
        SimpleNamespace(rule_id="TGIU_MISSING_FURTHER_INVESTIGATION", retrieval_id="retrieval_follow_up"),
    ]

    _validate_tgiu_candidate_coverage(candidates, rules)


def test_tgiu_rule_coverage_rejects_missing_or_duplicated_rule_decisions():
    rules = [
        SimpleNamespace(rule_id="TGIU_MISSING_REASON", retrieval_id="retrieval_reason"),
        SimpleNamespace(rule_id="TGIU_MISSING_FURTHER_INVESTIGATION", retrieval_id="retrieval_follow_up"),
    ]
    duplicated = [
        AssessmentCandidate(
            segment_id="segment_tgiu",
            retrieval_ids=["retrieval_reason"],
            rule_category=RuleCategory.METHODOLOGY,
            decision=AssessmentDecision.DEFICIENT,
            explanation="Reason missing.",
            evidence_ids=["evidence_reason"],
            proposed_finding_type="TGIU_MISSING_REASON",
        ),
        AssessmentCandidate(
            segment_id="segment_tgiu",
            retrieval_ids=["retrieval_reason"],
            rule_category=RuleCategory.METHODOLOGY,
            decision=AssessmentDecision.SATISFIED,
            explanation="Duplicate coverage.",
            evidence_ids=["evidence_reason"],
            proposed_finding_type=None,
        ),
    ]

    with pytest.raises(ValueError, match="tgiu semantic coverage invalid"):
        _validate_tgiu_candidate_coverage(duplicated, rules)


def test_tg3_cost_satisfied_is_normalized_to_missing_when_point_bound_evidence_has_no_cost():
    quote = "Etasjeskille/gulv mot grunn\nKonsekvens/tiltak\nUtbedring må vurderes ved senere renovering."
    span = {
        "evidence_id": "evidence_tg3_cost_missing_0001",
        "exact_quote": quote,
        "page": 1,
        "char_start": 0,
        "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact",
        "validation_status": "validated",
        "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_tg3_cost_missing_0001",
        "kind": "report_point",
        "title": "Etasjeskille/gulv mot grunn",
        "section_context": "INNVENDIG",
        "professional_subject": "Etasjeskille/gulv mot grunn",
        "point_label": "14.2",
        "tg_grade": "TG3",
        "point_type": "graded",
        "confidence": 1.0,
        "candidate_evidence": {"exact_quote": quote, "page": 1},
        "evidence": span,
        "evidence_spans": [span],
        "bound_body_spans": [span],
        "validation_status": "validated",
        "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id,
        retrieval_ids=["retrieval_tg3_cost_missing_0001"],
        rule_category=RuleCategory.TG3_COST,
        decision=AssessmentDecision.SATISFIED,
        explanation="A cost amount is present.",
        evidence_ids=[span["evidence_id"]],
        proposed_finding_type=None,
    )

    normalized = _normalize_semantic_candidate(candidate, segment, [])

    assert normalized.decision == AssessmentDecision.DEFICIENT
    assert normalized.proposed_finding_type == "E_METHOD.tg3_cost_missing"


def test_tg3_cost_missing_is_normalized_to_satisfied_when_point_bound_interval_exists():
    quote = "Kostnadsestimat: 20 000 - 100 000"
    span = {
        "evidence_id": "evidence_tg3_cost_pass_0001",
        "exact_quote": quote,
        "page": 1,
        "char_start": 0,
        "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact",
        "validation_status": "validated",
        "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_tg3_cost_pass_0001",
        "kind": "report_point",
        "title": "Terrengforhold",
        "section_context": "UTVENDIG",
        "professional_subject": "Terrengforhold",
        "point_label": "3",
        "tg_grade": "TG3",
        "point_type": "graded",
        "confidence": 1.0,
        "candidate_evidence": {"exact_quote": quote, "page": 1},
        "evidence": span,
        "evidence_spans": [span],
        "bound_body_spans": [span],
        "validation_status": "validated",
        "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id,
        retrieval_ids=["retrieval_tg3_cost_pass_0001"],
        rule_category=RuleCategory.TG3_COST,
        decision=AssessmentDecision.DEFICIENT,
        explanation="Cost is missing.",
        evidence_ids=[span["evidence_id"]],
        proposed_finding_type="E_METHOD.tg3_cost_missing",
    )

    normalized = _normalize_semantic_candidate(candidate, segment, [])

    assert normalized.decision == AssessmentDecision.SATISFIED
    assert normalized.proposed_finding_type is None


def test_tg3_cost_missing_is_normalized_to_satisfied_when_point_bound_bounded_amount_exists():
    quote = "Kostnadsestimat: Under 20 000"
    span = {
        "evidence_id": "source_tg3_cost_bounded_0001",
        "exact_quote": quote,
        "page": 1,
        "char_start": 0,
        "char_end": len(quote),
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "match_method": "exact",
        "validation_status": "validated",
        "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_tg3_cost_bounded_0001",
        "kind": "report_point",
        "title": "Avtrekk",
        "section_context": "KJØKKEN",
        "professional_subject": "Kjøkken",
        "point_label": "5",
        "tg_grade": "TG3",
        "point_type": "graded",
        "confidence": 1.0,
        "candidate_evidence": {"exact_quote": quote, "page": 1},
        "evidence": span,
        "evidence_spans": [span],
        "bound_body_spans": [span],
        "validation_status": "validated",
        "validation_notes": [],
    })
    candidate = AssessmentCandidate(
        segment_id=segment.segment_id,
        retrieval_ids=["retrieval_tg3_cost_bounded_0001"],
        rule_category=RuleCategory.TG3_COST,
        decision=AssessmentDecision.DEFICIENT,
        explanation="Cost is missing.",
        evidence_ids=[span["evidence_id"]],
        proposed_finding_type="E_METHOD.tg3_cost_missing",
    )

    normalized = _normalize_semantic_candidate(candidate, segment, [])

    assert normalized.decision == AssessmentDecision.SATISFIED
    assert normalized.proposed_finding_type is None


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


class LegalityBoundaryDeficiencyModel:
    def assess(self, segment, category, rules):
        return AssessmentCandidate(
            segment_id=segment.segment_id,
            retrieval_ids=[record.retrieval_id for record in rules],
            rule_category=category,
            decision=AssessmentDecision.DEFICIENT,
            explanation="Legality deficiency asserted without buyer-relevant point-bound gap.",
            evidence_ids=[segment.evidence.evidence_id],
            proposed_finding_type="L-BU-01",
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
    manifest = ROOT / "files/candidates/a3_a4_v2/MANIFEST.a3_a4_candidate.json"
    catalog = ManifestGovernedCatalog(
        ROOT / "files",
        manifest,
        approved_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    result = ManifestVerifiedRuleRetriever(catalog).retrieve(
        understanding.segments[0], RuleCategory.TG3_COST, [], document_hash=understanding.document_hash
    )
    assert result.asset_verifications[0].asset_path == "rag_scoring_model_validert_v1.6.15.json"
    ids = {record.rule_id for record in result.records}
    assert "E_METHOD.tg3_cost_missing" in ids
    assert "E_METHOD.tg3_cost_single_amount_only" in ids


def test_anbefalt_tiltak_retrieval_keeps_governed_field_definition_for_imperative_validation():
    text = "[SIDE 1]\nInnvendige dører TG2\nKonsekvens/tiltak: Dør må justeres.\n"

    class TiltakExtractor:
        def extract_candidates(self, **_kwargs):
            return {
                "facts": [
                    {
                        "fact_type": "report_date",
                        "value": "2026-09-02",
                        "confidence": 0.99,
                        "evidence": {"exact_quote": "Rapportdato: 02.09.2026", "page": 1},
                    },
                    {
                        "fact_type": "declared_standard",
                        "value": "NS 3600:2025",
                        "confidence": 0.99,
                        "evidence": {"exact_quote": "NS 3600:2025", "page": 1},
                    },
                ],
                "segments": [{
                    "kind": "report_point",
                    "title": "Innvendige dører",
                    "professional_subject": "Innvendige dører",
                    "point_label": "11.1",
                    "tg_grade": "TG2",
                    "confidence": 0.99,
                    "evidence": {"exact_quote": "Innvendige dører TG2", "page": 1},
                }],
                "abstentions": [],
            }, {"model_name": "fake"}

    understanding = DocumentUnderstandingService(TiltakExtractor()).analyze(text, "tiltak.pdf")
    manifest = ROOT / "files/candidates/a3_a4_v2/MANIFEST.a3_a4_candidate.json"
    catalog = ManifestGovernedCatalog(
        ROOT / "files",
        manifest,
        approved_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    result = ManifestVerifiedRuleRetriever(catalog).retrieve(
        understanding.segments[0],
        RuleCategory.ANBEFALT_TILTAK,
        understanding.facts,
        document_hash=understanding.document_hash,
    )
    pointers = {record.json_pointer for record in result.records}
    assert "/field_definitions/anbefalt_tiltak" in pointers
    field_rule = next(record for record in result.records if record.json_pointer == "/field_definitions/anbefalt_tiltak")
    assert "TILTAK_IMPERATIVE_FORM" in json.dumps(field_rule.content, ensure_ascii=False)


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
    assert abstained.analysis_state.value == "complete_without_findings"

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


def test_legality_l_bu_01_requires_buyer_relevant_point_bound_signal(tmp_path):
    segment = _understanding().segments[0].model_copy(update={
        "title": "Lovlighet",
        "professional_subject": "Lovlighet",
        "point_type": "legality_no_tg",
        "candidate_evidence": {"exact_quote": "Lovlighet", "page": 1},
        "evidence": _understanding().segments[0].evidence.model_copy(update={
            "exact_quote": "Det foreligger godkjente og byggemeldte tegninger, men det er avvik fra disse."
        }),
        "evidence_spans": [_understanding().segments[0].evidence.model_copy(update={
            "exact_quote": "Det foreligger godkjente og byggemeldte tegninger, men det er avvik fra disse."
        })],
        "bound_body_spans": [_understanding().segments[0].evidence.model_copy(update={
            "exact_quote": "Det foreligger godkjente og byggemeldte tegninger, men det er avvik fra disse."
        })],
    })
    rules = [
        RuleRetrievalRecord(
            retrieval_id="retrieval_legality_0001",
            segment_id=segment.segment_id,
            rule_category=RuleCategory.LEGALITY,
            asset_path="rules.json",
            asset_sha256="a" * 64,
            rule_id="L-BU-01",
            json_pointer="/rules/0",
            content_sha256="b" * 64,
            content={"semantic_error_type": "L-BU-01"},
            relevance_score=1.0,
            applicability="regime_resolved",
            regime_status=RegimeResolutionStatus.RESOLVED,
            regime_id="NS3600_2025",
            controlling_fact_ids=[],
            regime_explanation="resolved",
            retrieval_reason="test fixture",
        )
    ]
    assessment = StructuredAssessment(
        assessment_id="assess_legality_0001",
        segment_id=segment.segment_id,
        retrieval_ids=["retrieval_legality_0001"],
        rule_category=RuleCategory.LEGALITY,
        decision=AssessmentDecision.DEFICIENT,
        explanation="Legality deficiency asserted without buyer-relevant point-bound gap.",
        evidence_ids=[segment.evidence.evidence_id],
        proposed_finding_type="L-BU-01",
    )
    decision = DeterministicAssessmentValidator().validate(
        assessment,
        segment,
        rules,
        RegimeResolutionStatus.RESOLVED,
    )
    assert decision.admission == FindingAdmission.REJECTED
    assert "buyer_relevant_legality_gap_not_point_bound" in decision.reason_codes


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
    assert captured["system_prompt"] == get_dommer_b_system_prompt_text().strip()


def test_tgiu_adjudication_replay_reuses_multi_candidate_authoritative_decisions():
    evidence = {
        "evidence_id": "evidence_tgiu_0001",
        "exact_quote": "Fuktmåling ved hulltaking er ikke foretatt siden det er murvegger mot baderommet.",
        "page": 1,
        "char_start": 0,
        "char_end": 78,
        "quote_sha256": hashlib.sha256(
            "Fuktmåling ved hulltaking er ikke foretatt siden det er murvegger mot baderommet.".encode()
        ).hexdigest(),
        "match_method": "exact",
        "validation_status": "validated",
        "validation_notes": [],
    }
    segment = ValidatedSegment.model_validate({
        "segment_id": "segment_tgiu_replay_01",
        "kind": "report_point",
        "title": "Tilliggende konstruksjoner våtrom",
        "section_context": "KJELLER > VASKEROM",
        "professional_subject": "Tilliggende konstruksjoner våtrom",
        "point_label": None,
        "tg_grade": "TGIU",
        "point_type": "tgiu",
        "confidence": 1.0,
        "candidate_evidence": {"exact_quote": "Tilliggende konstruksjoner våtrom", "page": 1},
        "evidence": evidence,
        "evidence_spans": [evidence],
        "bound_body_spans": [evidence],
        "validation_status": "validated",
        "validation_notes": [],
    })
    rules = [
        RuleRetrievalRecord(
            retrieval_id="retrieval_tgiu_reason_0001",
            segment_id=segment.segment_id,
            rule_category=RuleCategory.METHODOLOGY,
            asset_path="rules.json",
            asset_sha256="a" * 64,
            rule_id="TGIU_MISSING_REASON",
            json_pointer="/rules/0",
            content_sha256="b" * 64,
            content={"id": "TGIU_MISSING_REASON"},
            relevance_score=1.0,
            applicability="regime_resolved",
            regime_status=RegimeResolutionStatus.RESOLVED,
            regime_id="FULL_2026",
            controlling_fact_ids=[],
            regime_explanation="resolved",
            retrieval_reason="test fixture",
        ),
        RuleRetrievalRecord(
            retrieval_id="retrieval_tgiu_further_0002",
            segment_id=segment.segment_id,
            rule_category=RuleCategory.METHODOLOGY,
            asset_path="rules.json",
            asset_sha256="a" * 64,
            rule_id="TGIU_MISSING_FURTHER_INVESTIGATION",
            json_pointer="/rules/1",
            content_sha256="c" * 64,
            content={"id": "TGIU_MISSING_FURTHER_INVESTIGATION"},
            relevance_score=1.0,
            applicability="regime_resolved",
            regime_status=RegimeResolutionStatus.RESOLVED,
            regime_id="FULL_2026",
            controlling_fact_ids=[],
            regime_explanation="resolved",
            retrieval_reason="test fixture",
        ),
    ]

    class FailIfCalled:
        def generate_json_with_claude(self, **_kwargs):
            raise AssertionError("live model should not be called when TGIU replay is available")

    segment_payload = {
        "segment_id": segment.segment_id,
        "kind": "report_point",
        "title": segment.title,
        "point_label": segment.point_label,
        "tg_grade": segment.tg_grade,
        "point_type": segment.point_type,
        "section_context": segment.section_context,
        "professional_subject": segment.professional_subject,
        "semantic_focus_excerpt": evidence["exact_quote"],
        "complete_bound_body": [{"evidence_id": evidence["evidence_id"], "page": 1, "exact_quote": evidence["exact_quote"]}],
    }
    retrieved_rules = [rule.model_dump(mode="json") for rule in rules]
    candidates = [
        {
            "segment_id": segment.segment_id,
            "retrieval_ids": [rules[0].retrieval_id],
            "rule_category": "methodology",
            "decision": "satisfied",
            "explanation": "The point states why inspection was not performed.",
            "evidence_ids": [evidence["evidence_id"]],
            "proposed_finding_type": None,
        },
        {
            "segment_id": segment.segment_id,
            "retrieval_ids": [rules[1].retrieval_id],
            "rule_category": "methodology",
            "decision": "deficient",
            "explanation": "No concrete follow-up investigation is recommended.",
            "evidence_ids": [evidence["evidence_id"]],
            "proposed_finding_type": "TGIU_MISSING_FURTHER_INVESTIGATION",
        },
    ]
    replay_artifact = {
        "model_invocations": [
            {
                "phase": "initial_semantic_assessment",
                "prompt": {
                    "segment": segment_payload,
                    "rule_category": "methodology",
                    "retrieved_rules": retrieved_rules,
                },
                "response": {"candidates": candidates},
            },
            {
                "phase": "governed_semantic_adjudication",
                "prompt": {
                    "segment": segment_payload,
                    "rule_category": "methodology",
                    "retrieved_rules": retrieved_rules,
                    "rule_pairs": [
                        {"retrieval_id": rules[0].retrieval_id, "rule_id": rules[0].rule_id},
                        {"retrieval_id": rules[1].retrieval_id, "rule_id": rules[1].rule_id},
                    ],
                },
                "response": {"candidates": candidates},
            },
        ]
    }
    model = BedrockSemanticAssessmentModel(FailIfCalled(), replay_artifacts=[replay_artifact])
    out = model.assess_many(segment, RuleCategory.METHODOLOGY, rules)
    assert [candidate.decision for candidate in out] == [
        AssessmentDecision.SATISFIED,
        AssessmentDecision.DEFICIENT,
    ]
    assert [candidate.proposed_finding_type for candidate in out] == [
        None,
        "TGIU_MISSING_FURTHER_INVESTIGATION",
    ]
