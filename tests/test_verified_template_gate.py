import asyncio
import copy
import io
import os
from types import SimpleNamespace

from starlette.datastructures import UploadFile

os.environ.setdefault("DATABASE_URL", "sqlite:///tmp.db")
os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("SECRET_KEY", "dummy")

from app.api.v1 import reports


def _bmtf_text() -> str:
    return """EIERSKIFTERAPPORT
DEL 1
1. Bad TG2 - Vesentlige avvik
VURDERING
Tekst
ÅRSAK
Tekst
RISIKO
Tekst
KONSEKVENS
Tekst
ANBEFALT TILTAK
Tekst
"""


def _bolavi_text() -> str:
    return """Tilstandsrapport
Rapporten er bygget med befar.io
OM TILSTANDSRAPPORT 2 av 53
Formål med rapporten
Rammeverk og standarder
Godkjent rapport
Rapportens gyldighet
Uavhengighet
FORKLARING AV TILSTANDSGRADER
OPPSUMMERING
BYGNINGSSAKKYNDIG
10. BAD
TG 2
"""


def test_governed_detector_routes_only_signed_templates():
    assert reports._verified_dommer_b_template("report.pdf", _bmtf_text()) == "bmtf_eierskifterapport"
    assert reports._verified_dommer_b_template("bolavi-egen-rapport.pdf", _bolavi_text()) == "bolavi"
    assert reports._verified_dommer_b_template("fremtind.pdf", "Fremtind\nVurdering av avvik") is None
    assert reports._verified_dommer_b_template("unknown.pdf", "Tilstandsrapport") is None


def test_bolavi_gate_rejects_filename_email_and_structural_near_misses():
    unrelated = "Tilstandsrapport\nDette er en helt annen rapporttype."
    assert reports._verified_dommer_b_template("renamed-bolavi.pdf", unrelated) is None
    assert reports._verified_dommer_b_template("unrelated.pdf", unrelated + "\nKontakt test@bolavi.no") is None
    near_miss = _bolavi_text().replace("Rapportens gyldighet", "")
    assert reports._verified_dommer_b_template("bolavi-near-miss.pdf", near_miss) is None


def test_unverified_upload_safe_stops_before_analyzer(monkeypatch):
    monkeypatch.setattr(reports.PDFExtractor, "get_pdf_metadata", lambda *_: {"total_pages": 1})
    monkeypatch.setattr(
        reports.PDFExtractor,
        "extract_text",
        lambda *_: "Fremtind\nVurdering av avvik\n" + ("rapporttekst " * 20),
    )

    def analyzer_must_not_run(*args, **kwargs):
        raise AssertionError("Analyzer was invoked after unverified format determination")

    monkeypatch.setattr(reports.AIAnalyzer, "analyze_report", analyzer_must_not_run)
    monkeypatch.setattr(reports.AIAnalyzer, "analyze_report_dommer_b_fallback", analyzer_must_not_run)

    response = asyncio.run(
        reports.upload_report(
            file=UploadFile(
                filename="fremtind.pdf",
                file=io.BytesIO(b"%PDF-1.4\n" + (b"x" * 200)),
            ),
            db=SimpleNamespace(),
            current_user=SimpleNamespace(id=123, credits=100),
        )
    )

    assert response.status_code == 200
    payload = __import__("json").loads(response.body)
    assert payload == {
        "status": "safe_stop",
        "message": "Rapporten kunne ikke analyseres ennå.",
    }
    forbidden = {
        "ai_analysis",
        "scoring_result",
        "detected_points",
        "extracted_text",
        "findings",
        "components",
        "deductions",
    }
    assert forbidden.isdisjoint(payload)


def test_verified_public_projection_is_no_score_and_does_not_mutate_canonical_feedback():
    canonical = {
        "feedback_v11": {
            "score": {"total": None, "category_deductions": [{"category": "A", "deduction": None}]},
            "gate": {"active": False},
            "points_overview": [{
                "display_index": 1, "point_id": "7.4", "title": "Bad", "tg": "TG2",
                "status": "FOUND", "summary": "Avvik funnet", "deduction_band": "Middels trekk",
                "potential_deduction_total": 3, "finding_ids": ["A_INTERNAL_90001"],
                "where": {"page": 12},
            }],
            "findings": [{
                "finding_id": "A_INTERNAL_90001", "rule_id": "A.INTERNAL", "point_id": "7.4",
                "message": "Forbedringspunkt", "what_to_change": "Presiser teksten",
                "potential_deduction": 3, "deduction_band": "Middels trekk",
                "gate_effect": {"blocks_96_gate": False},
                "evidence": {"page": 12, "snippet": "Rapporttekst"},
            }],
        }
    }
    before = copy.deepcopy(canonical)
    projected = reports._build_verified_public_feedback(canonical)
    payload = {
        "id": 1,
        "filename": "signed.pdf",
        "status": "completed",
        "components": [],
        "public_feedback": projected,
    }
    scan = reports._scan_final_public_payload(payload)

    assert canonical == before
    assert scan["passed"] is True
    serialized = __import__("json").dumps(payload)
    for forbidden_text in ("90001", "deduction", "score", "gate", "finding_id", "rule_id"):
        assert forbidden_text not in serialized


def test_final_public_scan_fails_on_nested_internal_ids_and_scoring_keys():
    payload = {
        "public_feedback": {"points": [{"native_label": "90001"}]},
        "nested": {"gate_effect": {"blocks_96_gate": False}},
    }
    scan = reports._scan_final_public_payload(payload)
    assert scan["passed"] is False
    assert scan["internal_id_matches"][0]["value"] == "90001"
    assert scan["forbidden_scoring_or_diagnostic_keys"][0]["key"] == "gate_effect"
