import asyncio
import io
from types import SimpleNamespace

from starlette.datastructures import UploadFile

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


def test_governed_detector_routes_only_signed_templates():
    assert reports._verified_dommer_b_template("report.pdf", _bmtf_text()) == "bmtf_eierskifterapport"
    assert reports._verified_dommer_b_template("bolavi-egen-rapport.pdf", "Rapport") == "bolavi"
    assert reports._verified_dommer_b_template("report.pdf", "Kontakt test@bolavi.no") == "bolavi"
    assert reports._verified_dommer_b_template("fremtind.pdf", "Fremtind\nVurdering av avvik") is None
    assert reports._verified_dommer_b_template("unknown.pdf", "Tilstandsrapport") is None


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
