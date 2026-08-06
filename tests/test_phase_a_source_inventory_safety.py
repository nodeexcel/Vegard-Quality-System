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

from app.services.pdf_extractor import PDFExtractor
from app.services.phase_a_assessment import PhaseA4ShadowService
from app.services.phase_a_contracts import (
    AssessmentCandidate,
    AssessmentDecision,
    InventoryRole,
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
from app.services.phase_a_source_inventory import PhysicalSourceInventoryBuilder


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_ivit_source_inventory_is_independent_and_contains_all_tgiu_points():
    text = PDFExtractor.extract_text(str(ROOT / "files/ivit-svak_arkat.pdf"))
    inventory = PhysicalSourceInventoryBuilder().build(text, _hash(text))
    primaries = [item for item in inventory.points if item.role == InventoryRole.PRIMARY]
    tgiu = [item.title for item in primaries if item.tg_grade == "TGIU"]
    assert inventory.structural_marker_counts["physical_primary_vurdering"] == 31
    assert inventory.structural_marker_counts["physical_primary_points"] == 34
    assert sorted(tgiu) == ["Oljetank", "Septiktank", "Tilliggende konstruksjoner våtrom"]


def test_physical_point_bodies_are_non_overlapping_and_do_not_depend_on_ai():
    report = """[SIDE 1]
Taktekking
Beskrivelse
Eldre tekking.
Vurdering av avvik:
• Det er avvik.
Årsak: Alder.
Konsekvens: Lekkasje kan oppstå.
Tiltak: Tekkingen bør skiftes.
Vinduer
Beskrivelse
Eldre vinduer.
Vurdering av avvik:
• Det er avvik.
Årsak: Alder.
Konsekvens: Treverket kan skades.
Tiltak: Vinduene bør vedlikeholdes.
"""
    inventory = PhysicalSourceInventoryBuilder().build(report, _hash(report))
    points = [item for item in inventory.points if item.role == InventoryRole.PRIMARY]
    assert [item.title for item in points] == ["Taktekking", "Vinduer"]
    assert points[0].char_end <= points[1].char_start
    assert "Vinduer" not in points[0].body.exact_quote
    assert "Tekkingen bør skiftes" in points[0].body.exact_quote


def test_point_body_continues_across_one_and_multiple_pages_until_next_primary():
    report = """[SIDE 1]
Taktekking
Beskrivelse
Start.
Vurdering av avvik:
Observasjon.
[SIDE 2]
Årsak: Alder.
Risiko: Fukt.
[SIDE 3]
Konsekvens: Skade.
Tiltak: Bør skiftes.
Vinduer
Beskrivelse
Eldre vinduer.
Vurdering av avvik:
Avvik.
"""
    inventory = PhysicalSourceInventoryBuilder().build(report, _hash(report))
    points = [item for item in inventory.points if item.role == InventoryRole.PRIMARY]
    assert [item.title for item in points] == ["Taktekking", "Vinduer"]
    complete_body = "\n".join(span.exact_quote for span in points[0].body_spans)
    assert len(points[0].body_spans) == 3
    assert "Tiltak: Bør skiftes" in complete_body
    assert "Vinduer" not in complete_body
    assert "[SIDE" not in complete_body


@pytest.mark.parametrize("wording", [
    "TG IU", "TGIU", "Ikke undersøkt", "Ikke inspisert",
    "Ikke tilgjengelig for undersøkelse", "Ikke mulig å undersøke",
    "Kunne ikke kontrolleres", "Utilgjengelig for inspeksjon",
])
def test_general_tgiu_detection_uses_unseen_titles_and_wording(wording):
    report = f"[SIDE 1]\nSkjult pumpesjakt\nBeskrivelse\n{wording}.\n"
    inventory = PhysicalSourceInventoryBuilder().build(report, _hash(report))
    points = [item for item in inventory.points if item.role == InventoryRole.PRIMARY]
    assert len(points) == 1
    assert points[0].title == "Skjult pumpesjakt"
    assert points[0].tg_grade == "TGIU"
    assert points[0].point_type == "tgiu"


def test_hms_vurdering_is_no_tg_and_navigation_is_not_primary():
    report = """[SIDE 1]
INNHOLD
Taktekking ........ 8
Helse, miljø og sikkerhet
Beskrivelse
Radon og rekkverk.
Vurdering av avvik:
Rekkverket er lavt.
"""
    inventory = PhysicalSourceInventoryBuilder().build(report, _hash(report))
    primary = [item for item in inventory.points if item.role == InventoryRole.PRIMARY]
    navigation = [item for item in inventory.points if item.role == InventoryRole.NAVIGATION]
    assert len(primary) == 1
    assert primary[0].point_type == "hms_no_tg"
    assert primary[0].tg_grade is None
    assert [item.title for item in navigation] == ["Taktekking"]


@pytest.mark.parametrize(("title", "body", "expected_type"), [
    ("Elektrisk anlegg", "Kontroll uten tilstandsgrad.", "electrical_no_tg"),
    ("Lovlighet", "Ferdigattest er ikke fremlagt.", "legality_no_tg"),
    ("Oppdragets rammer", "Metodikk og avgrensning.", "methodology_only"),
])
def test_no_tg_context_categories_are_not_coerced_to_tg2(title, body, expected_type):
    report = f"[SIDE 1]\n{title}\nBeskrivelse\n{body}\nVurdering av avvik:\nAvvik.\n"
    inventory = PhysicalSourceInventoryBuilder().build(report, _hash(report))
    point = next(item for item in inventory.points if item.role == InventoryRole.PRIMARY)
    assert point.title == title
    assert point.tg_grade is None
    assert point.point_type == expected_type


def test_navigation_marker_inside_point_does_not_truncate_cross_page_body():
    report = """[SIDE 1]
Taktekking
Beskrivelse
Start.
Vurdering av avvik:
Observasjon.
Se også ........ 9
[SIDE 2]
Årsak: Alder.
Tiltak: Bør skiftes.
Vinduer
Beskrivelse
Vurdering av avvik:
Avvik.
"""
    inventory = PhysicalSourceInventoryBuilder().build(report, _hash(report))
    points = [item for item in inventory.points if item.role == InventoryRole.PRIMARY]
    complete_body = "\n".join(span.exact_quote for span in points[0].body_spans)
    assert "Tiltak: Bør skiftes" in complete_body
    assert "Vinduer" not in complete_body


def test_cross_reference_is_not_used_as_primary_title():
    report = """[SIDE 1]
Taktekking
Punktet må sees i sammenheng med Takkonstruksjon/Loft
Beskrivelse
Eldre takstein.
Vurdering av avvik:
Avvik.
"""
    inventory = PhysicalSourceInventoryBuilder().build(report, _hash(report))
    primary = [item for item in inventory.points if item.role == InventoryRole.PRIMARY]
    assert [item.title for item in primary] == ["Taktekking"]


def test_summary_continuation_stays_linked_and_never_becomes_primary():
    report = """[SIDE 1]
1. Taktekking TG2
Full primary body.
Oppsummering av avvik
1. Taktekking
Summary starts.
[SIDE 2]
Summary continuation.
"""
    inventory = PhysicalSourceInventoryBuilder().build(report, _hash(report))
    primary = [item for item in inventory.points if item.role == InventoryRole.PRIMARY]
    summaries = [item for item in inventory.points if item.role == InventoryRole.SUMMARY]
    assert len(primary) == 1
    assert len(summaries) == 1
    assert summaries[0].linked_primary_id == primary[0].inventory_id
    complete_summary = "\n".join(span.exact_quote for span in summaries[0].body_spans)
    assert len(summaries[0].body_spans) == 2
    assert "Summary continuation" in complete_summary


def test_equal_ai_reconciliation_conflict_is_traceable_and_not_first_matched():
    report = """[SIDE 1]
Taktekking
Beskrivelse
Eldre takstein.
Vurdering av avvik:
Avvik.
"""

    class ConflictingExtractor:
        def extract_candidates(self, **_kwargs):
            candidates = []
            for title in ("Kandidat alfa", "Kandidat bravo"):
                candidates.append({
                    "kind": "report_point", "title": title,
                    "professional_subject": title, "tg_grade": "TG2",
                    "confidence": 0.9,
                    "evidence": {"exact_quote": "Vurdering av avvik:", "page": 1},
                })
            return {"facts": [], "segments": candidates, "abstentions": []}, {"model": "fake"}

    result = DocumentUnderstandingService(ConflictingExtractor()).analyze(report, "conflict.pdf")
    point = next(item for item in result.segments if item.kind.value == "report_point")
    reconciliation = next(item for item in result.coverage_reconciliation if item.inventory_role == InventoryRole.PRIMARY)
    assert point.title == "Taktekking"
    assert "ai_candidate_identity_conflict_rejected" in point.validation_notes
    assert reconciliation.status == "source_materialized"
    assert "identity conflicted" in reconciliation.reason


def test_bolavi_summary_is_linked_and_not_a_second_primary_point():
    text = PDFExtractor.extract_text(str(ROOT / "files/bolavi-egen-mangler_kostnadtg3.pdf"))
    inventory = PhysicalSourceInventoryBuilder().build(text, _hash(text))
    terrain_primary = [
        item for item in inventory.points
        if item.role == InventoryRole.PRIMARY and item.point_label == "3" and item.tg_grade == "TG3"
    ]
    terrain_summary = [
        item for item in inventory.points
        if item.role == InventoryRole.SUMMARY and item.point_label == "3" and item.tg_grade == "TG3"
    ]
    assert len(terrain_primary) == 1
    assert len(terrain_summary) == 1
    assert terrain_summary[0].linked_primary_id == terrain_primary[0].inventory_id


def test_real_report_summaries_are_all_linked_and_never_primary_assessments():
    for filename in (
        "ivit-svak_arkat.pdf",
        "bolavi-egen-mangler_kostnadtg3.pdf",
        "Tilstandsrapport_Fritidsbolig-God_rapport.pdf",
    ):
        text = PDFExtractor.extract_text(str(ROOT / "files" / filename))
        inventory = PhysicalSourceInventoryBuilder().build(text, _hash(text))
        summaries = [item for item in inventory.points if item.role == InventoryRole.SUMMARY]
        assert all(item.linked_primary_id for item in summaries), filename
        primary_ids = {
            item.inventory_id for item in inventory.points if item.role == InventoryRole.PRIMARY
        }
        assert all(item.linked_primary_id in primary_ids for item in summaries), filename
        primary_by_id = {
            item.inventory_id: item for item in inventory.points if item.role == InventoryRole.PRIMARY
        }
        assert len({item.linked_primary_id for item in summaries}) == len(summaries), filename
        for summary in summaries:
            leaf = summary.title.split(">")[-1].strip().casefold()
            linked_title = primary_by_id[summary.linked_primary_id].title.casefold()
            assert leaf in linked_title or linked_title in leaf, (filename, summary.title, linked_title)


def test_real_report_body_spans_are_reversible_and_exclude_extraction_artifacts():
    for filename in (
        "ivit-svak_arkat.pdf",
        "bolavi-egen-mangler_kostnadtg3.pdf",
        "Tilstandsrapport_Fritidsbolig-God_rapport.pdf",
    ):
        text = PDFExtractor.extract_text(str(ROOT / "files" / filename))
        inventory = PhysicalSourceInventoryBuilder().build(text, _hash(text))
        for point in inventory.points:
            assert point.body_spans, (filename, point.title)
            for span in point.body_spans:
                assert text[span.char_start:span.char_end] == span.exact_quote
                assert hashlib.sha256(span.exact_quote.encode()).hexdigest() == span.quote_sha256
                assert "[TABELLDATA]" not in span.exact_quote
                assert "[SIDE " not in span.exact_quote


def test_runtime_rejects_an_unapproved_manifest_even_when_asset_hashes_match(tmp_path):
    asset = tmp_path / "rules.json"
    asset.write_text('{"rules": []}', encoding="utf-8")
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text(json.dumps({
        "files": [{"path": "rules.json", "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()}]
    }), encoding="utf-8")
    with pytest.raises(GovernedAssetError, match="independently pinned"):
        ManifestGovernedCatalog(tmp_path, manifest)


class _Extractor:
    def extract_candidates(self, **_kwargs):
        return {
            "facts": [],
            "segments": [
                {
                    "kind": "legality", "title": "Ferdigattest", "professional_subject": "lovlighet",
                    "confidence": 0.99, "evidence": {"exact_quote": "Ingen ferdigattest.", "page": 1},
                },
                {
                    "kind": "legality", "title": "Bruksendring", "professional_subject": "lovlighet",
                    "confidence": 0.99, "evidence": {"exact_quote": "Bruksendring er ikke avklart.", "page": 1},
                },
            ],
            "abstentions": [],
        }, {"model": "fake"}


class _ResolvedCategoryPendingRules:
    def resolve(self, category, facts):
        return RegimeResolution(
            rule_category=category, status=RegimeResolutionStatus.RESOLVED,
            regime_id="category-only", explanation="Category resolved.",
        )

    def resolve_rule(self, category, rule_id, rule_content, facts):
        return RegimeResolution(
            rule_category=category, status=RegimeResolutionStatus.PENDING_GOVERNED_DECISION,
            explanation="Individual rule remains unresolved.",
        )


class _CountingModel:
    def __init__(self, fail_first=False):
        self.calls = 0
        self.fail_first = fail_first

    def assess(self, segment, category, rules):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise ValueError("malformed JSON")
        evidence = segment.evidence_spans[0]
        return AssessmentCandidate(
            segment_id=segment.segment_id,
            retrieval_ids=[item.retrieval_id for item in rules],
            rule_category=category,
            decision=AssessmentDecision.SATISFIED,
            explanation="Satisfied.",
            evidence_ids=[evidence.evidence_id],
        )


def _real_catalog(resolver):
    return ManifestVerifiedRuleRetriever(
        ManifestGovernedCatalog(ROOT / "files", ROOT / "files/MANIFEST.json"),
        resolver=resolver,
    )


def test_unresolved_individual_rule_blocks_model_even_if_category_is_resolved():
    understanding = DocumentUnderstandingService(_Extractor()).analyze(
        "[SIDE 1]\nIngen ferdigattest.\nBruksendring er ikke avklart.\n", "legal.pdf"
    )
    model = _CountingModel()
    result = PhaseA4ShadowService(_real_catalog(_ResolvedCategoryPendingRules()), model).analyze(
        understanding, [RuleCategory.LEGALITY]
    )
    assert model.calls == 0
    assert result.analysis_state.value == "limited"
    assert any(item.reason_code == "required_rule_regime_unresolved" for item in result.abstentions)


def test_one_model_failure_abstains_and_other_assessments_continue():
    class FullyResolved(_ResolvedCategoryPendingRules):
        def resolve_rule(self, category, rule_id, rule_content, facts):
            return self.resolve(category, facts)

    understanding = DocumentUnderstandingService(_Extractor()).analyze(
        "[SIDE 1]\nIngen ferdigattest.\nBruksendring er ikke avklart.\n", "legal.pdf"
    )
    model = _CountingModel(fail_first=True)
    result = PhaseA4ShadowService(_real_catalog(FullyResolved()), model).analyze(
        understanding, [RuleCategory.LEGALITY]
    )
    assert model.calls == 2
    assert len(result.assessments) == 1
    assert result.analysis_state.value == "limited"
    assert any(item.reason_code == "assessment_model_or_schema_failure" for item in result.abstentions)
    assert result.formal_acceptance_blockers == [
        "v46_tgiu_expected_behavior_requires_governed_resolution",
        "v46_tg2_anbefalt_tiltak_expected_behavior_requires_governed_resolution",
    ]
