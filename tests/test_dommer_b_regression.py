import json
import os
import re
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

from app.services.ai_analyzer import (  # noqa: E402
    _detect_report_date_from_document_identity,
    _drop_age_only_false_positives,
    _drop_false_electrical_tg_forbidden_findings,
    _drop_missing_tiltak_when_raw_action_present,
    _drop_buyer_only_consequence_public_claims,
    _ensure_generic_backstop_findings,
    _ensure_semantic_tg3_cost_backstop,
    _effective_point_tg,
    _extract_local_tg_for_point_id,
    _extract_compressed_mixed_block_by_terms,
    _extract_compressed_mixed_wetroom_block_by_title,
    _normalize_report_text_for_analysis,
    _normalize_tg3_cost_text as normalize_text,
    _drop_buyer_only_consequence_public_claims,
    _drop_tg3_missing_tiltak_for_semantic_tg2_not_applicable,
    _mark_duplicate_f001_informational,
    _drop_legacy_consequence_unclear_when_semantic_missing,
    _finalize_category_summary_public_contracts,
    _normalize_category_summary_consequence_wording,
    _normalize_zero_score_language_findings,
    _remove_untraceable_tg3_cost_summary_claims,
    _sanitize_feedback_missing_tiltak_findings,
    _sanitize_feedback_v11_legacy_consequence_unclear,
    _sanitize_bmtf_feedback_v11_p_codes,
    _sanitize_user_facing_text_contracts,
    _scrub_age_only_category_summary_without_finding,
    _sync_category_breakdown_with_score_by_category,
)
from app.services.arkat_semantic_pipeline import (  # noqa: E402
    _evaluate_arkat_point,
    _extract_fields_for_point,
    _finalize_arkat_fields,
    _best_action_sentence_from_text,
    _normalize_arkat_eval_result,
    _sanitize_arkat_field_values,
    _strip_embedded_summary_tables_for_arkat_fields,
)


TEST_SET_PATH = ROOT / "files" / "dommer_b_test_set_v1_3.md"
HORTEN_REPORT_PATH = ROOT / "files" / "dommer_b_real_report_1806_full.json"
FR_REPORT_PATH = ROOT / "files" / "dommer_b_real_report_1807_full.json"
BG_REPORT_PATH = ROOT / "files" / "dommer_b_real_report_1808_full.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _score_by_category(analysis_output: dict) -> dict:
    return {
        str(row.get("category_id") or row.get("category") or "").strip(): row.get("deduction")
        for row in analysis_output.get("score_by_category", [])
        if isinstance(row, dict)
    }


def _points_by_id(report_payload: dict) -> dict:
    points = (report_payload.get("dommer_b_full") or {}).get("points") or []
    return {point.get("point_id"): point for point in points if isinstance(point, dict)}


def _field_result_pairs(evaluation: dict) -> dict:
    return {
        key: (value.get("status"), value.get("error_type"))
        for key, value in (evaluation.get("field_results") or {}).items()
        if isinstance(value, dict)
    }


def _parse_dommer_b_markdown_cases() -> list[dict]:
    markdown = TEST_SET_PATH.read_text(encoding="utf-8")
    case_re = re.compile(
        r"^Test case\s+(\d+)\s+.*?\n"
        r"Input:\n```json\n(.*?)\n```\n"
        r"Expected output:\n```json\n(.*?)\n```",
        re.MULTILINE | re.DOTALL,
    )
    cases = []
    for case_id, input_json, expected_json in case_re.findall(markdown):
        cases.append(
            {
                "case_id": int(case_id),
                "input": json.loads(input_json),
                "expected": json.loads(expected_json),
            }
        )
    return cases


def _run_deterministic_dommer_b_case(case: dict) -> dict:
    payload = case["input"]
    point_id = payload["point_id"]
    raw_point_text = payload["raw_point_text"]
    fields = dict(payload.get("extracted_fields") or {})

    def no_section(_text: str, _field: str) -> str:
        return ""

    if all(not str(value or "").strip() for value in fields.values()):
        fields = _extract_fields_for_point(
            payload["report_format"],
            raw_point_text,
            no_section,
            normalize_text,
        )
    fields = _sanitize_arkat_field_values(fields, normalize_text, point_id)
    fields = _finalize_arkat_fields(
        fields,
        normalize_text,
        point_id,
        raw_point_text,
        payload["tg_grade"],
    )
    return _evaluate_arkat_point(
        point_id=point_id,
        point_label=payload["point_label"],
        tg_grade=payload["tg_grade"],
        report_format=payload["report_format"],
        ns_version=payload["ns_version"],
        raw_point_text=raw_point_text,
        extracted_fields=fields,
        report_context=payload.get("report_context") or {},
        normalize_text=normalize_text,
        allow_llm=False,
    )


def test_markdown_test_set_is_parseable_and_schema_complete():
    cases = _parse_dommer_b_markdown_cases()
    assert len(cases) >= 9

    required_input_keys = {
        "point_id",
        "point_label",
        "tg_grade",
        "report_format",
        "ns_version",
        "raw_point_text",
        "extracted_fields",
        "report_context",
    }
    required_fields = {"aarsak", "risiko", "konsekvens", "anbefalt_tiltak"}

    for case in cases:
        payload = case["input"]
        expected = case["expected"]
        assert required_input_keys <= set(payload), case["case_id"]
        assert required_fields <= set(payload["extracted_fields"]), case["case_id"]
        assert expected["point_id"] == payload["point_id"], case["case_id"]
        assert expected["tg_grade"] == payload["tg_grade"], case["case_id"]
        assert required_fields <= set(expected["field_results"]), case["case_id"]



def test_exact_point_tg_wins_over_merged_tg3_summary():
    point = {
        "point_id": "15.1",
        "tg": "TG3",
        "tg_source": "fremtind_summary",
        "exact_span_text": "15.1 VVS-installasjoner (samlet) Det er ikke fremlagt dokumentasjon.",
        "effective_span_text": (
            "OPPSUMMERING AV AVVIK SOM HAR FÅTT TG3 3 Terrengforhold ... "
            "15.1 VVS-installasjoner. TG 2 15.1 VVS-INSTALLASJONER (SAMLET)"
        ),
    }

    assert _effective_point_tg(point, "") == "TG2"


def test_local_point_tg_prefers_header_attached_marker_over_summary_tg():
    text = (
        "15.1 VVS-installasjoner (samlet). Årsak: alder og manglende dokumentasjon. "
        "OPPSUMMERING AV AVVIK SOM HAR FÅTT TG3 3 Terrengforhold ... "
        "TG 2 15.1 VVS-INSTALLASJONER (SAMLET)"
    )

    assert _extract_local_tg_for_point_id("15.1", text) == "TG2"


def test_semantic_tg3_cost_backstop_adds_missing_terrain_cost_only():
    analysis_output = {
        "meta": {"ns_version": "NS 3600:2025"},
        "all_findings": [],
        "arkat_semantic_pipeline": {
            "ns_version": "NS3600:2025",
            "report_format": "semi_structured",
            "points": [
                {
                    "point_id": "P01E_TERRAIN",
                    "title": "Utforming terreng",
                    "tg_grade": "TG3",
                    "raw_point_text": (
                        "Konklusjon bygningsdel: TG 3. Flatt terreng ved grunnmur kan gi "
                        "fuktbelastning og skade konstruksjonen. Det anbefales å etablere fall fra bygningen."
                    ),
                    "extracted_fields": {},
                },
                {
                    "point_id": "15.1",
                    "title": "VVS-installasjoner",
                    "tg_grade": "TG3",
                    "raw_point_text": "TG 2 15.1 VVS-INSTALLASJONER (SAMLET). Det anbefales kontroll.",
                    "extracted_fields": {},
                },
            ],
        },
    }

    _ensure_semantic_tg3_cost_backstop("", analysis_output)

    cost_findings = [
        item for item in analysis_output["all_findings"]
        if "kostnad" in json.dumps(item, ensure_ascii=False).lower()
    ]
    assert [item["exact_point_id"] for item in cost_findings] == ["P01E_TERRAIN"]


def test_zero_deduction_category_summary_guard_requires_visible_finding():
    analysis_output = {
        "score_by_category": [
            {"category_id": "D", "category_name": "Klarspråk og struktur", "deduction": 0, "max_deduction": 15},
        ],
        "category_breakdown": [
            {"category": "D - Klarspråk og struktur", "deduction_band": "Ikke scoretrekk", "summary": "Det finnes enkelte språk/faguttrykk-issues."},
        ],
        "all_findings": [],
    }

    _sync_category_breakdown_with_score_by_category(analysis_output)
    _finalize_category_summary_public_contracts(analysis_output)

    assert analysis_output["category_breakdown"][0]["summary"] == "Ingen scoretrekk i denne kategorien."


def test_tabelldata_strip_drops_unanchored_cross_page_prose():
    raw = """
TG 2 9.1.2 Gulvets overflate
Støpt gulv mot grunn.
[TABELLDATA]
| EIERSKIFTERAPPORT™
Alder på kjøkkenet er ikke opplyst.
Merknader:
-Det mangler endetetting på varerør.
-Benkeplaten har dels skader/avskalling, som gir risiko for svelling.
9. Rom under terreng
"""

    cleaned = _strip_embedded_summary_tables_for_arkat_fields(raw, "9.1.2")

    assert "Støpt gulv mot grunn" in cleaned
    assert "[TABELLDATA]" not in cleaned
    assert "kjøkkenet" not in cleaned.lower()
    assert "varerør" not in cleaned.lower()
    assert "Benkeplaten" not in cleaned


def test_tabelldata_strip_preserves_same_point_continuation():
    raw = """
TG 2 7.1.3 Membran, tettesjiktet og sluk
Membranen er fra Ukjent
[TABELLDATA]
TG 2 | 7.1.2 Overflate gulv
TG 2 | 7.1.3 Membran, tettesjiktet og sluk
[SIDE 15]
EIERSKIFTERAPPORT™
Tettesjikt av våtromsplater på vegger og belegg på gulv
Merknader:
-Mer enn halvparten av våtrommets forventede levetid er passert.
"""

    cleaned = _strip_embedded_summary_tables_for_arkat_fields(raw, "7.1.3")

    assert "Membranen er fra Ukjent" in cleaned
    assert "Tettesjikt av våtromsplater" in cleaned
    assert "Merknader:" in cleaned
    assert "7.1.2 Overflate gulv" not in cleaned
    assert "[TABELLDATA]" not in cleaned

V12_CONSEQUENCE_CANARIES = [
    "En lekkasje fra varmtvannsbereder vil ikke nødvendigvis kunne oppdages tidlig nok før det gjør skader på andre bygningsdeler",
    "Dette kan gi negative konsekvenser over tid både bygningsmessig og helsemessig",
    "Kan føre til råte over tid",
]


@pytest.mark.parametrize("consequence_text", V12_CONSEQUENCE_CANARIES)
def test_dommer_b_v12_direct_consequence_canaries_are_correct(consequence_text: str):
    actual = _normalize_arkat_eval_result(
        None,
        point_id="canary",
        point_label="Canary",
        tg_grade="TG2",
        extracted_fields={
            "aarsak": "Konkret årsak er beskrevet.",
            "risiko": "Konkret risiko er beskrevet.",
            "konsekvens": consequence_text,
            "anbefalt_tiltak": "MISSING",
        },
        raw_point_text=consequence_text,
        ns_version="NS3600:2018",
        report_context={},
        normalize_text=normalize_text,
    )

    assert actual["field_results"]["konsekvens"] == {"status": "CORRECT", "error_type": None, "explanation": ""}


@pytest.mark.parametrize("consequence_text", V12_CONSEQUENCE_CANARIES)
def test_dommer_b_v12_raw_text_consequence_fallback_before_missing(consequence_text: str):
    parsed_missing = {
        "field_results": {
            "aarsak": {"status": "CORRECT", "error_type": None, "explanation": ""},
            "risiko": {"status": "CORRECT", "error_type": None, "explanation": ""},
            "konsekvens": {"status": "MISSING", "error_type": "MISSING (konsekvens)", "explanation": ""},
            "anbefalt_tiltak": {"status": "NOT_APPLICABLE", "error_type": None, "explanation": ""},
        },
        "tgiu_findings": {"findings": []},
        "has_errors": True,
    }

    actual = _normalize_arkat_eval_result(
        parsed_missing,
        point_id="canary",
        point_label="Canary",
        tg_grade="TG2",
        extracted_fields={
            "aarsak": "Konkret årsak er beskrevet.",
            "risiko": "Konkret risiko er beskrevet.",
            "konsekvens": "MISSING",
            "anbefalt_tiltak": "MISSING",
        },
        raw_point_text=consequence_text,
        ns_version="NS3600:2018",
        report_context={},
        normalize_text=normalize_text,
    )

    assert actual["field_results"]["konsekvens"] == {"status": "CORRECT", "error_type": None, "explanation": ""}


def test_tgiu_normalization_forces_arkat_fields_not_applicable():
    parsed = {
        "point_id": "5.1",
        "tg_grade": "TGIU",
        "field_results": {
            "aarsak": {"status": "MISSING", "error_type": "MISSING (aarsak)", "explanation": ""},
            "risiko": {"status": "MISSING", "error_type": "MISSING (risiko)", "explanation": ""},
            "konsekvens": {"status": "MISSING", "error_type": "MISSING (konsekvens)", "explanation": ""},
            "anbefalt_tiltak": {"status": "MISSING", "error_type": "MISSING (anbefalt_tiltak)", "explanation": ""},
        },
        "tgiu_findings": {
            "findings": [
                {"error_type": "TGIU_MISSING_REASON", "explanation": "Mangler begrunnelse."}
            ]
        },
        "has_errors": True,
    }
    actual = _normalize_arkat_eval_result(
        parsed,
        point_id="5.1",
        point_label="Loft",
        tg_grade="TGIU",
        extracted_fields={"aarsak": "", "risiko": "", "konsekvens": "", "anbefalt_tiltak": ""},
        raw_point_text="Loftkonstruksjonen er lukket, ingen tilkomst for vurdering av bygningsdelen.",
        ns_version="NS3600:2018",
        report_context={},
        normalize_text=normalize_text,
    )

    for field_name in ("aarsak", "risiko", "konsekvens", "anbefalt_tiltak"):
        assert actual["field_results"][field_name] == {
            "status": "NOT_APPLICABLE",
            "error_type": None,
            "explanation": "",
        }
    assert [item["error_type"] for item in actual["tgiu_findings"]["findings"]] == ["TGIU_MISSING_REASON"]
    assert actual["has_errors"] is True


@pytest.mark.parametrize("case_id", range(1, 10))
def test_markdown_deterministic_regression_cases(case_id: int):
    cases = {case["case_id"]: case for case in _parse_dommer_b_markdown_cases()}
    case = cases[case_id]
    expected = case["expected"]
    actual = _run_deterministic_dommer_b_case(case)

    assert _field_result_pairs(actual) == _field_result_pairs(expected)
    assert actual["has_errors"] == expected["has_errors"]
    actual_tgiu = sorted(
        item.get("error_type")
        for item in (actual.get("tgiu_findings") or {}).get("findings", [])
        if isinstance(item, dict)
    )
    expected_tgiu = sorted(
        item.get("error_type")
        for item in (expected.get("tgiu_findings") or {}).get("findings", [])
        if isinstance(item, dict)
    )
    assert actual_tgiu == expected_tgiu


def test_fr_1807_report_regression_score_legal_and_extraction():
    report = _load_json(FR_REPORT_PATH)
    output = report["analysis_output"]
    categories = _score_by_category(output)
    points = _points_by_id(report)

    assert output["score_total"] == 50
    assert categories["A"] == 40
    assert categories["F"] == 10

    legal_ids = {
        finding.get("rule_id")
        for finding in output.get("all_findings", [])
        if isinstance(finding, dict)
    }
    assert {"L-BU-01", "L-AV-01"} <= legal_ids

    top_ids = [finding.get("finding_id") for finding in output.get("top_issues", [])]
    assert top_ids == ["A_ARKAT_TG3_MISSING_01", "A_KONSEKVENS_MISSING_02", "L-BU-01"]

    p09g = points["P09G_OTHER_INSTALLATIONS"]["extracted_fields"]
    assert p09g["anbefalt_tiltak"] == "Varmepumpen har en alder og tilstand som tilsier at den bør skiftes."
    assert "Elektrisk anlegg" not in p09g["anbefalt_tiltak"]

    p10i = points["P10I_SPECIAL_ROOM_SURFACES"]["extracted_fields"]
    assert "Det anbefales ikke å ta i bruk kjølerommet igjen" in p10i["aarsak"]
    assert "Områder hvor det har kondensert" in p10i["aarsak"]


def test_bg_1808_report_regression_score_and_merknader_bullets():
    report = _load_json(BG_REPORT_PATH)
    output = report["analysis_output"]
    categories = _score_by_category(output)
    points = _points_by_id(report)

    assert output["score_total"] == 59
    assert categories["A"] == 40
    assert categories["F"] == 1

    point_21 = points["2.1"]
    fields_21 = point_21["extracted_fields"]
    eval_21 = _field_result_pairs(point_21["evaluation"])
    assert fields_21["aarsak"] == "Ingen tegn til musebånd/musesikring under nyere panel."
    assert fields_21["risiko"] == "Det er større spalter som gjør konstruksjon sårbar for at mus kan trenge inn."
    assert fields_21["konsekvens"] == "Mus kan trenge inn, som videre kan gi følgeskader."
    assert fields_21["anbefalt_tiltak"] == "Det anbefales å etablere musesikring mellom underganger i nedre del av vegg for å sikre mot mus."
    assert eval_21["konsekvens"] == ("CORRECT", None)

    point_61 = points["6.1"]
    fields_61 = point_61["extracted_fields"]
    eval_61 = _field_result_pairs(point_61["evaluation"])
    assert fields_61["aarsak"] == "Det mangler rekkverk på terrassetrapp og platting."
    assert fields_61["risiko"] == "MISSING"
    assert fields_61["konsekvens"] == "Mangel på rekkverk gir økt fare for fallulykker."
    assert "rekkverk" in fields_61["anbefalt_tiltak"]
    assert eval_61["risiko"] == ("MISSING", "MISSING (risiko)")

    contaminated_terms = ("trekledning", "terrassebord", "endeved", "råte", "redusere levetiden")
    assert not any(term in normalize_text(fields_61["risiko"]).lower() for term in contaminated_terms)


def _technical_development_point_ids(report_path: Path) -> list[str]:
    output = _load_json(report_path)["analysis_output"]
    flagged = []
    for finding in output.get("all_findings", []):
        blob = " ".join(
            str(finding.get(key, ""))
            for key in ("finding_id", "rule_id", "title", "message")
        )
        if "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS" in blob:
            flagged.append(str(finding.get("point_id") or finding.get("exact_point_id") or ""))
    return sorted(point_id for point_id in flagged if point_id)


def test_technical_development_flags_in_current_real_reports():
    assert _technical_development_point_ids(HORTEN_REPORT_PATH) == ["7.2.3"]
    assert _technical_development_point_ids(FR_REPORT_PATH) == []
    assert _technical_development_point_ids(BG_REPORT_PATH) == []



def test_dommer_b_tg3_action_fallback_prefers_explicit_tiltak_sentence():
    raw = (
        "Det er ufagmessige beslag og kledning med skader. "
        "Kjøper må påregne at forholdet kan gi følgeskader. "
        "Det må påregnes tiltak, herunder utskiftning av kledning, utbedring av detaljer rundt vinduer og dører, "
        "samt kontroll og eventuell utbedring av underliggende konstruksjon."
    )
    fields = _finalize_arkat_fields(
        {"aarsak": "Det er ufagmessige beslag og kledning med skader.", "risiko": "Konstruksjonen kan få fuktskader.", "konsekvens": "Følgeskader kan oppstå.", "anbefalt_tiltak": "MISSING"},
        normalize_text,
        "2.1",
        raw,
        "TG3",
    )

    assert fields["anbefalt_tiltak"].startswith("Det må påregnes tiltak")

    actual = _normalize_arkat_eval_result(
        {"field_results": {"aarsak": {"status": "CORRECT"}, "risiko": {"status": "CORRECT"}, "konsekvens": {"status": "CORRECT"}, "anbefalt_tiltak": {"status": "MISSING", "error_type": "MISSING (anbefalt_tiltak)"}}, "tgiu_findings": {"findings": []}, "has_errors": True},
        point_id="2.1",
        point_label="Yttervegger",
        tg_grade="TG3",
        extracted_fields={**fields, "anbefalt_tiltak": "MISSING"},
        raw_point_text=raw,
        ns_version="NS3600:2018",
        report_context={},
        normalize_text=normalize_text,
    )

    assert actual["field_results"]["anbefalt_tiltak"]["status"] == "CORRECT"


def test_dommer_b_hidden_damage_consequence_raw_fallback_before_missing():
    raw = (
        "Tettesjiktet har passert forventet brukstid. "
        "Dette øker faren for at fukt trenger inn og forårsaker skjulte skader i konstruksjon. "
        "Våtrommet vurderes derfor som modent for oppgradering eller rehabilitering for å sikre tilfredsstillende funksjon fremover."
    )
    actual = _normalize_arkat_eval_result(
        {"field_results": {"aarsak": {"status": "CORRECT"}, "risiko": {"status": "CORRECT"}, "konsekvens": {"status": "MISSING", "error_type": "MISSING (konsekvens)"}, "anbefalt_tiltak": {"status": "NOT_APPLICABLE"}}, "tgiu_findings": {"findings": []}, "has_errors": True},
        point_id="7.1.3",
        point_label="Membran, tettesjiktet og sluk",
        tg_grade="TG2",
        extracted_fields={"aarsak": "Alder er årsaken.", "risiko": "Fukt kan trenge inn.", "konsekvens": "MISSING", "anbefalt_tiltak": "MISSING"},
        raw_point_text=raw,
        ns_version="NS3600:2018",
        report_context={},
        normalize_text=normalize_text,
    )

    assert actual["field_results"]["konsekvens"] == {"status": "CORRECT", "error_type": None, "explanation": ""}


def test_dommer_b_crawlspace_recovers_cause_and_risk_from_raw_text():
    raw = (
        "Terrengfall ved muren gir mulighet for vanninnsig og det burde vært flere ventiler. "
        "Forholdene gir risiko for fuktproblemer i krypkjeller med følgeskader som konsekvens. "
        "Det anbefales å forbedre ventilering og terrengfall."
    )
    fields = _finalize_arkat_fields(
        {"aarsak": "MISSING", "risiko": "MISSING", "konsekvens": "Følgeskader som konsekvens.", "anbefalt_tiltak": "Det anbefales å forbedre ventilering og terrengfall."},
        normalize_text,
        "1.2",
        raw,
        "TG2",
    )

    assert fields["aarsak"].startswith("Terrengfall ved muren gir mulighet for vanninnsig")
    assert fields["risiko"].startswith("Forholdene gir risiko for fuktproblemer")

    actual = _normalize_arkat_eval_result(
        None,
        point_id="1.2",
        point_label="Krypkjeller",
        tg_grade="TG2",
        extracted_fields=fields,
        raw_point_text=raw,
        ns_version="NS3600:2018",
        report_context={},
        normalize_text=normalize_text,
    )

    assert actual["field_results"]["aarsak"]["status"] == "CORRECT"
    assert actual["field_results"]["risiko"]["status"] == "CORRECT"



def test_bmtf_feedback_v11_sanitizer_removes_all_fremtind_style_p_codes():
    payload = {
        "points_overview": [
            {
                "point_id": "P01A_BUILDING_SITE",
                "canonical_id": "P03A_WINDOWS",
                "finding_ids": ["f-v16-P01E_TERRAIN-001"],
                "children": [{"point_id": "P01F_CRAWLSPACE", "finding_ids": ["f-v16-P01F_CRAWLSPACE-009"]}],
            }
        ],
        "findings": [
            {"finding_id": "f-v16-P02A_EXTERIOR_WALLS-002", "point_id": "P02A_EXTERIOR_WALLS", "message": "Se P03A_WINDOWS."}
        ],
    }

    actual = _sanitize_bmtf_feedback_v11_p_codes(payload, "BMTF unlabeled prose report")
    blob = json.dumps(actual, ensure_ascii=False)

    assert not re.search(r"P\d{2}[A-Z]_[A-Z0-9_]+", blob)
    assert "bmtf-exterior-walls" in blob


def test_tg3_missing_tiltak_removed_when_dommer_b_says_tg2_not_applicable():
    analysis_output = {
        "arkat_semantic_pipeline": {
            "points": [
                {
                    "point_id": "7.2.3",
                    "tg_grade": "TG2",
                    "evaluation": {"field_results": {"anbefalt_tiltak": {"status": "NOT_APPLICABLE"}}},
                }
            ]
        },
        "all_findings": [
            {"finding_id": "TG3_MISSING_TILTAK_7_2_3", "point_id": "7.2.3", "title": "TG3 mangler anbefalt tiltak"}
        ],
        "top_issues": [
            {"finding_id": "A_ARKAT.TG3.tiltak_missing_001", "message": "TG3-punkt 7.2.3 mangler konkret anbefalt tiltak"}
        ],
        "findings": [
            {"component_id": "7.2.3", "deductions": [{"rule_id": "TG3_MISSING_RECOMMENDED_ACTION", "reason": "Punkt 7.2.3 mangler tiltak"}]}
        ],
    }

    _drop_tg3_missing_tiltak_for_semantic_tg2_not_applicable(analysis_output)

    assert analysis_output["all_findings"] == []
    assert analysis_output["top_issues"] == []
    assert analysis_output["findings"][0]["deductions"] == []


def test_category_summary_uses_concrete_consequence_wording_not_buyer_oriented_requirement():
    analysis_output = {"category_breakdown": [{"summary": "Konsekvenser kunne vært mer kjøperorienterte"}]}

    _normalize_category_summary_consequence_wording(analysis_output)
    summary = analysis_output["category_breakdown"][0]["summary"]

    assert "kjøperorienterte" not in summary.lower()
    assert "konkrete følger" in summary


def test_dommer_b_wetroom_tg3_raw_fallback_recovers_consequence_and_action():
    raw = (
        "Det registreres hulrom under flere fliser og det er påvist, eller det kan forventes skader som vil kunne kreve tiltak. "
        "Vann som eventuelt blir liggende vil gi slitasje på gulv og fuger, samt øke risiko for å skli på gulvet. "
        "Det måles stedvis motfall på gulv og det er fare for større vannsamlinger og tilstøtende bygningsdeler vil være utsatt dersom en lekkasje oppstår. "
        "Estimert utbedringskostnad må ses i sammenheng med punkt 7.2.3 Membran, da utbedring av fall normalt krever utskifting av membran og tilhørende overflater."
    )
    fields = _finalize_arkat_fields(
        {"aarsak": "Manglende fall rundt sluk.", "risiko": "Fare for større vannsamlinger.", "konsekvens": "MISSING", "anbefalt_tiltak": "MISSING"},
        normalize_text,
        "7.2.2",
        raw,
        "TG3",
    )

    actual = _normalize_arkat_eval_result(
        None,
        point_id="7.2.2",
        point_label="Vaskerom - Overflate gulv",
        tg_grade="TG3",
        extracted_fields=fields,
        raw_point_text=raw,
        ns_version="NS3600:2018",
        report_context={},
        normalize_text=normalize_text,
    )

    assert actual["field_results"]["konsekvens"]["status"] == "CORRECT"
    assert actual["field_results"]["anbefalt_tiltak"]["status"] == "CORRECT"



def test_bmtf_public_sanitizer_removes_parent_canonical_ids_and_detected_points_codes():
    payload = {
        "points_overview": [
            {"canonical_id": "P01_GROUND_AND_FOUNDATIONS", "children": [{"point_id": "P11G_SAFETY_RAILINGS"}]}
        ],
        "points": [
            {"point_id": "P07A_WETROOM_INSTANCE", "canonical_point_id": "P09F_ELECTRICAL_INSTALLATION"}
        ],
    }

    actual = _sanitize_bmtf_feedback_v11_p_codes(payload, "BMTF unlabeled prose report")
    blob = json.dumps(actual, ensure_ascii=False)

    assert not re.search(r"(?i)\bp\d{2}[a-z]?(?:[_\-.]?[a-z0-9]+)+\b", blob)
    assert "bmtf-ground-and-foundations" in blob
    assert "bmtf-safety-railings" in blob
    assert "bmtf-wetroom-instance" in blob
    assert "bmtf-electrical-installation" in blob

def test_bmtf_feedback_v11_sanitizer_removes_lowercase_hyphen_p_codes():
    payload = {
        "points_overview": [{"point_id": "p01f-crawlspace", "finding_ids": ["f-v16-p11g-safety-railings-008", "f-v16-p01e-terrain-004"]}],
        "findings": [{"finding_id": "f-v16-p02a-exterior-walls-002", "message": "Se p03a-windows."}],
    }

    actual = _sanitize_bmtf_feedback_v11_p_codes(payload, "BMTF unlabeled prose report")
    blob = json.dumps(actual, ensure_ascii=False)

    assert not re.search(r"(?i)\bp\d{2}[a-z](?:[_\-.]?[a-z0-9]+)+\b", blob)
    assert "bmtf-exterior-walls" in blob


def test_user_facing_text_sanitizer_removes_legacy_buyer_consequence_terms():
    analysis_output = {
        "meta": {"ns_standard_version": "NS3600:2018"},
        "all_findings": [
            {
                "title": "Konsekvens ikke kjøperorientert nok",
                "evidence_snippets": ["Eksempel mangler tydelig kjøperkonsekvens"],
                "suggested_rewrite_text": "Skriv fra kjøperperspektiv.",
            }
        ],
    }

    _sanitize_user_facing_text_contracts(analysis_output)
    blob = json.dumps(analysis_output, ensure_ascii=False).lower()

    assert "kjøperorientert" not in blob
    assert "kjøperkonsekvens" not in blob
    assert "kjøperperspektiv" not in blob
    assert "konkrete følger" in blob


def test_ns2018_user_facing_text_sanitizer_removes_2025_tg2_gate_text():
    analysis_output = {
        "meta": {"ns_standard_version": "NS3600:2018"},
        "disclaimers": [
            "Rapporten vurderes mot gjeldende forskrift og NS 3600:2025 for rapportdato 27.03.2026.",
            "96%-gate er blokkert på grunn av manglende anbefalt tiltak i TG2-punkter som påkrevd i NS3600:2025-regime.",
        ],
    }

    _sanitize_user_facing_text_contracts(analysis_output)
    blob = json.dumps(analysis_output, ensure_ascii=False)

    assert "NS 3600:2025" not in blob
    assert "2025-regime" not in blob
    assert "TG2-punkter" not in blob
    assert "NS 3600:2018" in blob


def test_duplicate_f001_legality_summary_is_removed_from_public_output_and_not_extra_score():
    analysis_output = {
        "all_findings": [
            {"finding_id": "F_001", "category": "F", "title": "Lovlighetsmangler uten tilstrekkelig konsekvens"},
            {"rule_id": "L-BU-01"},
            {"rule_id": "L-AV-01"},
        ],
        "gate": {"active": True, "blocked_96": True, "max_score_if_blocked": 95, "blocked_by_count": 3, "blocked_by": ["F_001", "L-BU-01", "L-AV-01"]},
        "how_to_improve": [{"title": "Lovlighetsmangler uten tilstrekkelig konsekvens"}],
        "score_by_category": [{"category_id": "F", "deduction": 16}],
        "score_total": 44,
        "trygghetsscore": 44,
    }

    _mark_duplicate_f001_informational(analysis_output)
    blob = json.dumps(analysis_output, ensure_ascii=False)

    assert "F_001" not in blob
    assert "Lovlighetsmangler uten tilstrekkelig konsekvens" not in blob
    assert analysis_output["how_to_improve"] == []
    assert analysis_output["gate"]["blocked_by"] == ["L-BU-01", "L-AV-01"]
    assert analysis_output["gate"]["blocked_by_count"] == 2
    assert analysis_output["score_by_category"][0]["deduction"] == 11
    assert analysis_output["score_total"] == 49

def test_bmtf_feedback_v11_sanitizer_removes_fremtind_p11g_codes():
    payload = {
        "points_overview": [{"children": [{"point_id": "P11G_SAFETY_RAILINGS", "canonical_id": "P11G_SAFETY_RAILINGS", "finding_ids": ["f-v16-P11G_SAFETY_RAILINGS-009"]}]}],
        "findings": [{"finding_id": "f-v16-P11G_SAFETY_RAILINGS-009", "rule_id": "L_RK_01_REKKVERK", "point_id": "P11G_SAFETY_RAILINGS"}],
    }

    actual = _sanitize_bmtf_feedback_v11_p_codes(payload, "Innvendige rekkverk og håndrekker er vurdert.")
    blob = json.dumps(actual, ensure_ascii=False)

    assert "P11G_SAFETY_RAILINGS" not in blob
    assert "f-v16-P11G" not in blob
    assert "REKKVERK" in blob


def test_ns2018_category_summary_does_not_claim_2025_tg2_tiltak_regime():
    analysis_output = {
        "meta": {"ns_standard_version": "NS3600:2018"},
        "score_by_category": [{"category_id": "A", "category_name": "ARKAT", "deduction": 5, "max_deduction": 40}],
        "category_breakdown": [{"category": "A - ARKAT", "summary": "Systematisk mangel på konkrete konsekvenser og anbefalt tiltak i TG2-punkter. Alle TG2-punkter mangler tydelig forklaring av hva avviket betyr for kjøper i praksis."}],
    }

    _sync_category_breakdown_with_score_by_category(analysis_output)
    summary = analysis_output["category_breakdown"][0]["summary"]

    assert "NS 3600:2025-regime" not in summary
    assert "anbefalt tiltak" not in summary.lower()
    assert "kreves" not in summary.lower()


def test_category_summary_does_not_claim_missing_tg3_tiltak_when_final_dommer_b_is_correct():
    analysis_output = {
        "meta": {"ns_standard_version": "NS3600:2018"},
        "score_by_category": [{"category_id": "A", "category_name": "ARKAT", "deduction": 7, "max_deduction": 40}],
        "category_breakdown": [{"category": "A - ARKAT", "summary": "ARKAT-kvalitet har betydelige mangler, særlig manglende anbefalt tiltak og kostnadsanslag for TG3, samt upresise konsekvenser for flere TG2-punkter."}],
        "arkat_semantic_pipeline": {
            "points": [
                {"point_id": "2.1", "tg_grade": "TG3", "evaluation": {"field_results": {"anbefalt_tiltak": {"status": "CORRECT"}}}},
                {"point_id": "7.2.2", "tg_grade": "TG3", "evaluation": {"field_results": {"anbefalt_tiltak": {"status": "CORRECT"}}}},
            ]
        },
    }

    _sync_category_breakdown_with_score_by_category(analysis_output)
    summary = analysis_output["category_breakdown"][0]["summary"]

    assert "manglende anbefalt tiltak" not in summary.lower()
    assert "kostnadsanslag for TG3" not in summary
    assert "upresise konsekvenser" in summary.lower()


def test_category_summary_keeps_missing_tg3_tiltak_when_final_dommer_b_is_missing():
    analysis_output = {
        "meta": {"ns_standard_version": "NS3600:2018"},
        "score_by_category": [{"category_id": "A", "category_name": "ARKAT", "deduction": 7, "max_deduction": 40}],
        "category_breakdown": [{"category": "A - ARKAT", "summary": "Manglende anbefalt tiltak ved TG3 og manglende konsekvens."}],
        "arkat_semantic_pipeline": {
            "points": [
                {"point_id": "7.2.3", "tg_grade": "TG3", "evaluation": {"field_results": {"anbefalt_tiltak": {"status": "MISSING"}}}},
            ]
        },
    }

    _sync_category_breakdown_with_score_by_category(analysis_output)
    summary = analysis_output["category_breakdown"][0]["summary"]

    assert "manglende anbefalt tiltak" in summary.lower()
    assert "TG3" in summary




def test_legacy_konsekvens_unclear_removed_when_semantic_consequence_missing():
    analysis_output = {
        "arkat_semantic_pipeline": {
            "points": [
                {"point_id": "1.2", "evaluation": {"field_results": {"konsekvens": {"status": "MISSING"}}}},
                {"point_id": "4.1", "evaluation": {"field_results": {"konsekvens": {"status": "MISSING"}}}},
                {"point_id": "3.1", "evaluation": {"field_results": {"konsekvens": {"status": "CORRECT"}}}},
            ]
        },
        "all_findings": [
            {"finding_id": "A_ARKAT.konsekvens_unclear_002", "message": "Punkt 1.2 Krypekjeller - konsekvens er ikke tydelig praktisk presisert for kjøper."},
            {"finding_id": "A_ARKAT.konsekvens_unclear_003", "message": "Punkt 4.1 Takkonstruksjon - konsekvens er ikke tydelig praktisk presisert for kjøper."},
            {"finding_id": "A_ARKAT.konsekvens_unclear_004", "message": "Punkt 3.1 Vinduer - konsekvens er ikke tydelig praktisk presisert for kjøper."},
            {"finding_id": "A_ARKAT_1_2_KONSEKVENS_MISSING_KONSEKVENS", "point_id": "1.2", "rule_id": "A_ARKAT_SEMANTIC.KONSEKVENS.MISSING_KONSEKVENS"},
        ],
    }

    _drop_legacy_consequence_unclear_when_semantic_missing(analysis_output)
    blob = json.dumps(analysis_output, ensure_ascii=False)

    assert "A_ARKAT.konsekvens_unclear_002" not in blob
    assert "A_ARKAT.konsekvens_unclear_003" not in blob
    assert "A_ARKAT.konsekvens_unclear_004" in blob
    assert "A_ARKAT_1_2_KONSEKVENS_MISSING_KONSEKVENS" in blob


def test_feedback_v11_legacy_konsekvens_unclear_removed_when_semantic_missing():
    analysis_output = {
        "arkat_semantic_pipeline": {
            "points": [{"point_id": "4.1", "evaluation": {"field_results": {"konsekvens": {"status": "MISSING"}}}}]
        }
    }
    payload = {
        "findings": [
            {"finding_id": "f-v16-4.1-003", "rule_id": "A_ARKAT.konsekvens_unclear_003", "point_id": "4.1", "message": "Konsekvens ikke tydelig praktisk presisert"},
            {"finding_id": "semantic-4.1", "rule_id": "A_ARKAT_SEMANTIC.KONSEKVENS.MISSING_KONSEKVENS", "point_id": "4.1"},
        ],
        "points_overview": [{"point_id": "4.1", "finding_ids": ["f-v16-4.1-003", "semantic-4.1"]}],
    }

    _sanitize_feedback_v11_legacy_consequence_unclear(payload, analysis_output)

    assert [item["finding_id"] for item in payload["findings"]] == ["semantic-4.1"]
    assert payload["points_overview"][0]["finding_ids"] == ["semantic-4.1"]



def test_final_category_summary_contract_removes_late_tg3_cost_claim_variant():
    analysis_output = {
        "score_by_category": [{"category_id": "A", "deduction": 5}],
        "category_breakdown": [{"category": "A - ARKAT", "summary": "ARKAT-kvalitet: Hovedutfordringer med manglende kostnadsanslag for TG3, samt enkelte konsekvenser."}],
        "all_findings": [],
    }

    _finalize_category_summary_public_contracts(analysis_output)

    summary = analysis_output["category_breakdown"][0]["summary"]
    assert "kostnadsanslag for TG3" not in summary
    assert "konsekvenser" in summary

def test_untraceable_tg3_cost_summary_claim_is_removed_unless_visible_finding_exists():
    summary = "ARKAT-kvalitet har betydelige mangler, særlig manglende anbefalt tiltak og kostnadsanslag for TG3, samt upresise konsekvenser for flere TG2-punkter"

    cleaned = _remove_untraceable_tg3_cost_summary_claims(summary, {"all_findings": []})
    kept = _remove_untraceable_tg3_cost_summary_claims(
        summary,
        {"all_findings": [{"finding_id": "A_TG3_COST", "title": "TG3 mangler kostnadsanslag"}]},
    )

    assert "kostnadsanslag for TG3" not in cleaned
    assert "upresise konsekvenser" in cleaned
    assert "kostnadsanslag for TG3" in kept

def test_zero_score_d_long_sentence_finding_is_informational():
    analysis_output = {
        "score_by_category": [{"category_id": "D", "deduction": 0}],
        "all_findings": [
            {"finding_id": "D_001", "category": "D", "title": "Lange setninger", "deduction_band": "Lavt trekk", "points": 1},
            {"finding_id": "A_001", "category": "A", "title": "Annet", "deduction_band": "Lavt trekk"},
        ],
        "top_issues": [{"finding_id": "D_LANGUAGE.long_sentences_001", "category": "D", "title": "Lange setninger", "deduction_band": "Lavt trekk"}],
    }

    _normalize_zero_score_language_findings(analysis_output)

    d_finding = analysis_output["all_findings"][0]
    assert d_finding["deduction_band"] == "Ikke scoretrekk"
    assert d_finding["points"] == 0
    assert d_finding["score_impact"] == "informational_language_observation"
    assert analysis_output["top_issues"][0]["deduction_band"] == "Ikke scoretrekk"
    assert analysis_output["all_findings"][1]["deduction_band"] == "Lavt trekk"


def test_nonzero_score_d_long_sentence_finding_keeps_band():
    analysis_output = {
        "score_by_category": [{"category_id": "D", "deduction": 1}],
        "all_findings": [{"finding_id": "D_001", "category": "D", "title": "Lange setninger", "deduction_band": "Lavt trekk"}],
    }

    _normalize_zero_score_language_findings(analysis_output)

    assert analysis_output["all_findings"][0]["deduction_band"] == "Lavt trekk"


def test_compressed_mixed_public_sanitizer_removes_fremtind_p_codes():
    payload = {
        "points": [{"point_id": "P01D_DRAINAGE", "canonical_point_id": "P07A_WETROOM_INSTANCE"}],
        "all_findings": [
            {"title": "Punkt P01D_DRAINAGE: konsekvens vurdert som MISSING"},
            {"exact_point_id": "P07D_SURFACES_SPESIALROM_1_ETASJE_TOALETTROM_OVERFLATER_OG_KON"},
        ],
        "feedback_v11": {"findings": [{"rule_id": "A_ARKAT.P01D_DRAINAGE", "message": "P07A_WETROOM_INSTANCE"}]},
    }

    actual = _sanitize_bmtf_feedback_v11_p_codes(payload, "Fremtind rapport. Sammendrag av boligens tilstand.")
    blob = json.dumps(actual, ensure_ascii=False)

    assert "P01D_DRAINAGE" not in blob
    assert "P07A_WETROOM_INSTANCE" not in blob
    assert "P07D_SURFACES" not in blob
    assert "TOALETTROM" not in blob
    assert "fremtind-drainage" in blob
    assert "fremtind-wetroom-instance" in blob
    assert "fremtind-surfaces-spesialrom-1-etasje-toalettrom-overflater-og-kon" in blob

def test_bmtf_railings_backstop_does_not_surface_fremtind_p_code():
    report_text = (
        "Rapportdato: 2026-03-27. "
        "Innvendige rekkverk og håndrekker er ikke i henhold til dagens forskrifter."
    )
    analysis_output = {"all_findings": []}

    _ensure_generic_backstop_findings(report_text, analysis_output, detected_points=[])

    railings = [
        item for item in analysis_output["all_findings"]
        if isinstance(item, dict) and item.get("rule_id") == "L-RK-01"
    ]
    assert len(railings) == 1
    finding = railings[0]
    assert finding["finding_id"] == "L_RK_01_REKKVERK"
    assert finding["point_id"] == ""
    assert finding["exact_point_id"] == ""
    assert "P11G" not in json.dumps(finding, ensure_ascii=False)


def test_buyer_only_consequence_rule_is_not_publicly_emitted():
    analysis_output = {
        "all_findings": [
            {
                "finding_id": "A_ARKAT_KONSEKVENS_NOT_BUYER_ORIENTED_1_1",
                "rule_id": "gate_konsekvens_not_buyer_oriented",
                "title": "Konsekvens ikke kjøperorientert",
                "message": "Konsekvens beskriver teknisk skadeutvikling eller bygningsrisiko i stedet for hva forholdet betyr for kjøper.",
                "rewrite_strategy": "consequence_buyer_orientation_required",
            },
            {"finding_id": "A_VALID", "title": "Konsekvens mangler", "message": "Konsekvensfeltet mangler."},
        ],
        "top_issues": [
            {"rule_id": "gate_konsekvens_not_buyer_oriented", "message": "hva forholdet betyr for kjøper"},
            {"rule_id": "A_VALID", "message": "Behold denne."},
        ],
        "how_to_improve": [
            {"rewrite_strategy": "consequence_buyer_orientation_required", "message": "Presiser for kjøper."},
            {"message": "Behold forbedringsråd."},
        ],
        "findings": [
            {
                "component_id": "1.1",
                "deductions": [
                    {"rule_id": "gate_konsekvens_not_buyer_oriented", "reason": "Konsekvens ikke kjøperorientert"},
                    {"rule_id": "A_VALID", "reason": "Behold deduction."},
                ],
            }
        ],
    }

    _drop_buyer_only_consequence_public_claims(analysis_output)
    blob = json.dumps(analysis_output, ensure_ascii=False).lower()

    assert "consequence_buyer_orientation_required" not in blob
    assert "gate_konsekvens_not_buyer_oriented" not in blob
    assert "hva forholdet betyr for kjøper" not in blob
    assert "teknisk skadeutvikling" not in blob
    assert "a_valid" in blob
    assert analysis_output["findings"][0]["component_id"] == "1.1"


def test_compressed_mixed_block_extraction_uses_component_body_not_summary_or_hms():
    report_text = """
[SIDE 5]
Sammendrag av boligens tilstand
Tomteforhold > Fuktsikring og drenering Gå til side
Tomteforhold > Terrengforhold Gå til side
[TABELLDATA]
HELSE, MILJØ OG SIKKERHET
FORHOLD SOM ÅPENBART KAN MEDFØRE FARE FOR HELSE, MILJØ OG SIKKERHET
Det mangler håndløper på vegg i det innvendige trappeløpet.
[SIDE 19]
TOMTEFORHOLD
Fuktsikring og drenering
Punktet må sees i sammenheng ‘Rom under terreng’
Beskrivelse
Dagens drenering er fra boligens byggeår. Dreneringens funksjon og kapasitet forringes over tid.
Vurdering av avvik:
• Mer enn halvparten av forventet levetid på drenering er overskredet.
Konsekvens/tiltak
• Tiltak for redrenering rundt boligen kan ikke utelukkes.
Grunnmur og fundamenter
Beskrivelse
Bygningen har grunnmur i lettklinkerblokker.
"""

    block = _extract_compressed_mixed_block_by_terms(
        _normalize_report_text_for_analysis(report_text),
        ["Fuktsikring og drenering"],
        require_tg=False,
    )

    assert "Dagens drenering" in block
    assert "redrenering" in block
    assert "håndløper" not in block
    assert "FORHOLD SOM ÅPENBART" not in block
    assert "Gå til side" not in block


def test_compressed_mixed_document_identity_date_is_detected():
    assert _detect_report_date_from_document_identity(
        "fremtind-sarpsborg-20-05-26.pdf",
        "fremtind_sarpsborg_20_05_26",
    ) == "2026-05-20"


def test_user_facing_text_sanitizer_removes_buyer_only_consequence_variants():
    analysis_output = {
        "meta": {"ns_standard_version": "NS3600:2018"},
        "all_findings": [
            {
                "message": "Punkt 7.1.3: mangler konsekvens for kjøper (bruk/sikkerhet/økonomi/videre skade).",
                "recommended_fix_text": "Presiser den praktiske konsekvensen for kjøper (f.eks. økte kostnader).",
                "comment": "Konsekvens må beskrive hva forholdet betyr for kjøper, ikke bare teknisk skadeutvikling.",
            }
        ],
        "category_breakdown": [
            {
                "category": "A - ARKAT",
                "summary": "Systematisk mangel på konkrete konsekvens. Flere punkter beskriver kun tekniske forhold uten å forklare praktisk betydning for kjøper",
            }
        ],
    }

    _sanitize_user_facing_text_contracts(analysis_output)
    _normalize_category_summary_consequence_wording(analysis_output)
    blob = json.dumps(analysis_output, ensure_ascii=False)

    assert "mangler konsekvens for kjøper" not in blob
    assert "Presiser den praktiske konsekvensen for kjøper" not in blob
    assert "uten å forklare praktisk betydning for kjøper" not in blob
    assert "hva forholdet betyr for kjøper" not in blob
    assert "konkret konsekvens, enten bygningsteknisk eller praktisk for kjøper" in blob


def test_feedback_missing_tiltak_sanitizer_does_not_create_tg3_finding_for_tg2():
    findings = [
        {
            "finding_id": "raw-7.1.3",
            "rule_id": "A_ARKAT.ANBEFALT_TILTAK.MISSING",
            "point_id": "7.1.3",
            "message": "anbefalt tiltak mangler",
            "what_to_change": "Skriv anbefalt tiltak",
        },
        {
            "finding_id": "raw-7.2.3",
            "rule_id": "A_ARKAT.ANBEFALT_TILTAK.MISSING",
            "point_id": "7.2.3",
            "message": "anbefalt tiltak mangler",
            "what_to_change": "Skriv anbefalt tiltak",
        },
    ]

    _sanitize_feedback_missing_tiltak_findings(findings, {"7.1.3": "TG2", "7.2.3": "TG3"})
    blob = json.dumps(findings, ensure_ascii=False)

    assert "TG3_MISSING_TILTAK_7_1_3" not in blob
    assert "TG3_MISSING_TILTAK_7_2_3" in blob
    assert len(findings) == 1


def test_compressed_mixed_special_room_kjolerom_blocks_are_path_anchored():
    report_text = """
SPESIALROM
1. ETASJE > TOALETTROM
Overflater og konstruksjon
Beskrivelse
Toalettrom med toalett og servant.
KJELLER > KJØLEROM
Overflater og konstruksjon
Beskrivelse
Kjølerom
Vurdering av avvik:
• Det er påvist indikasjoner på feil utførelse av konstruksjoner i rommet.
Konsekvens/tiltak
• Det må gjennomføres ytterligere undersøkelser.
[TABELLDATA]
[SIDE 16]
KJELLER > KJØLEROM
Teknisk anlegg
Beskrivelse
Kjøleraggregat.
Vurdering av avvik:
• Det er påvist/opplyst at kjøleromsaggregat ikke fungerer.
Konsekvens/tiltak
• Kjøleaggregat må skiftes ut/utbedres.
Kostnadsestimat: 20 000 - 100 000
"""

    surfaces = _extract_compressed_mixed_wetroom_block_by_title(
        _normalize_report_text_for_analysis(report_text),
        "Spesialrom > Kjeller > Kjølerom > Overflater og konstruksjon",
    )
    technical = _extract_compressed_mixed_wetroom_block_by_title(
        _normalize_report_text_for_analysis(report_text),
        "Spesialrom > Kjeller > Kjølerom > Teknisk anlegg",
    )

    assert "feil utførelse" in surfaces
    assert "Kjøleraggregat" not in surfaces
    assert "Kjøleraggregat" in technical
    assert "feil utførelse" not in technical


def test_buyer_only_scrubber_does_not_drop_dommer_b_point_objects():
    payload = {
        "arkat_semantic_pipeline": {
            "points": [
                {
                    "point_id": "fremtind-drainage",
                    "title": "Tomteforhold > Fuktsikring og drenering",
                    "evaluation": {
                        "field_results": {
                            "konsekvens": {
                                "status": "MISSING",
                                "explanation": "Konsekvens må beskrive hva forholdet betyr for kjøper, ikke bare teknisk skadeutvikling.",
                            }
                        }
                    },
                }
            ]
        },
        "all_findings": [
            {"finding_id": "legacy", "message": "Konsekvens må beskrive hva forholdet betyr for kjøper, ikke bare teknisk skadeutvikling."}
        ],
    }

    _sanitize_user_facing_text_contracts(payload)
    _drop_buyer_only_consequence_public_claims(payload)
    blob = json.dumps(payload, ensure_ascii=False)

    assert payload["arkat_semantic_pipeline"]["points"][0]["point_id"] == "fremtind-drainage"
    assert payload["all_findings"][0]["finding_id"] == "legacy"
    assert "hva forholdet betyr for kjøper" not in blob
    assert "Konsekvens bør beskrive konkrete følger" in blob


def test_category_summary_removes_unverified_action_and_schematic_claims():
    analysis_output = {
        "meta": {"ns_standard_version": "NS3600:2018"},
        "score_by_category": [{"category_id": "A", "category_name": "ARKAT", "deduction": 7}],
        "category_breakdown": [
            {
                "category": "A - ARKAT",
                "summary": "manglende anbefalt tiltak og sjablongmessig , samt flere TG2-punkter med upresise konsekvenser",
            }
        ],
        "arkat_semantic_pipeline": {
            "points": [
                {"point_id": "2.1", "tg_grade": "TG3", "evaluation": {"field_results": {"anbefalt_tiltak": {"status": "CORRECT"}}}},
                {"point_id": "7.2.2", "tg_grade": "TG3", "evaluation": {"field_results": {"anbefalt_tiltak": {"status": "CORRECT"}}}},
                {"point_id": "3.1", "tg_grade": "TG2", "evaluation": {"field_results": {"anbefalt_tiltak": {"status": "NOT_APPLICABLE"}}}},
            ]
        },
        "all_findings": [{"finding_id": "A_CONS", "title": "Upresis konsekvens"}],
    }

    _sync_category_breakdown_with_score_by_category(analysis_output)
    _finalize_category_summary_public_contracts(analysis_output)
    summary = analysis_output["category_breakdown"][0]["summary"].lower()

    assert "manglende anbefalt tiltak" not in summary
    assert "sjablong" not in summary
    assert "sjablon" not in summary
    assert "upresise konsekvenser" in summary


def test_category_summary_rewrites_exact_buyer_only_consequence_phrase():
    analysis_output = {
        "meta": {"ns_standard_version": "NS3600:2018"},
        "score_by_category": [{"category_id": "A", "category_name": "ARKAT", "deduction": 5}],
        "category_breakdown": [
            {"category": "A - ARKAT", "summary": "Flere TG2-punkter mangler tydelig konsekvens for kjøper"}
        ],
        "all_findings": [{"finding_id": "A_CONS", "title": "Manglende tydelig konkret konsekvens"}],
    }

    _sync_category_breakdown_with_score_by_category(analysis_output)
    _finalize_category_summary_public_contracts(analysis_output)
    summary = analysis_output["category_breakdown"][0]["summary"]

    assert "konsekvens for kjøper" not in summary.lower()
    assert "mangler tydelig konkret konsekvens" in summary.lower()


def test_category_summary_removes_false_partial_tg3_cost_claim_when_tg3_costs_are_present():
    analysis_output = {
        "meta": {"ns_standard_version": "NS3600:2018"},
        "score_by_category": [{"category_id": "A", "category_name": "ARKAT", "deduction": 5}],
        "category_breakdown": [
            {"category": "A - ARKAT", "summary": "Flere TG2-punkter mangler full ARK-struktur og TG3-punkt mangler kostnadsanslag"}
        ],
        "arkat_semantic_pipeline": {
            "points": [
                {
                    "point_id": "fremtind-wetroom-instance",
                    "tg_grade": "TG3",
                    "raw_point_text": "Kostnadsestimat: 200 000 - 500 000",
                    "evaluation": {"field_results": {"konsekvens": {"status": "CORRECT"}}},
                },
                {
                    "point_id": "fremtind-wetroom-instance-v-trom-kjeller-vaskerom-generell",
                    "tg_grade": "TG3",
                    "raw_point_text": "Kostnadsestimat: 100 000 - 200 000",
                    "evaluation": {"field_results": {"konsekvens": {"status": "CORRECT"}}},
                },
                {
                    "point_id": "fremtind-special-room-surfaces",
                    "tg_grade": "TG3",
                    "raw_point_text": "Kostnadsestimat: 20 000 - 100 000",
                    "evaluation": {"field_results": {"konsekvens": {"status": "CORRECT"}}},
                },
            ]
        },
        "all_findings": [],
    }

    _sync_category_breakdown_with_score_by_category(analysis_output)
    _finalize_category_summary_public_contracts(analysis_output)
    summary = analysis_output["category_breakdown"][0]["summary"].lower()

    assert "manglende kostnadsanslag" not in summary
    assert "mangler kostnadsanslag" not in summary
    assert "flere tg2-punkter mangler full ark-struktur" in summary


def test_category_summary_removes_stale_action_cleanup_fragments():
    analysis_output = {
        "meta": {"ns_standard_version": "NS3600:2018"},
        "score_by_category": [{"category_id": "A", "category_name": "ARKAT", "deduction": 5}],
        "category_breakdown": [
            {"category": "A - ARKAT", "summary": "Hovedutfordringer med ved TG3 og enkelte Konsekvenser bør beskrive konkrete følger tydeligere"},
            {"category": "A - ARKAT", "summary": "Mange som kreves i NS 3600:2018-regime. Konsekvenser bør beskrive konkrete følger tydeligere"},
            {"category": "A - ARKAT", "summary": "ARKAT-kvalitet: Hovedutfordringer med , samt enkelte Konsekvenser bør beskrive konkrete følger tydeligere"},
            {"category": "A - ARKAT", "summary": "Mange TG2-punkter mangler konkrete konsekvens"},
        ],
        "arkat_semantic_pipeline": {
            "points": [
                {"point_id": "2.1", "tg_grade": "TG3", "evaluation": {"field_results": {"anbefalt_tiltak": {"status": "CORRECT"}}}},
                {"point_id": "3.1", "tg_grade": "TG2", "evaluation": {"field_results": {"anbefalt_tiltak": {"status": "NOT_APPLICABLE"}}}},
            ]
        },
    }

    _sync_category_breakdown_with_score_by_category(analysis_output)
    _finalize_category_summary_public_contracts(analysis_output)
    blob = json.dumps(analysis_output["category_breakdown"], ensure_ascii=False)

    assert "ved TG3" not in blob
    assert "som kreves i NS 3600:2018-regime" not in blob
    assert "Hovedutfordringer med ," not in blob
    assert "mangler konkrete konsekvens" not in blob
    assert "mangler tydelig konkret konsekvens" in blob


def test_electrical_tg_forbidden_suppressed_when_no_tg_hms_point_has_empty_tg():
    analysis_output = {
        "gate": {
            "active": True,
            "blocked_96": True,
            "blocked_by": ["B_TG_el_anlegg_tg_forbudt_001", "L-BU-01"],
            "blocked_by_count": 2,
        },
        "top_issues": [
            {
                "finding_id": "B_TG_el_anlegg_tg_forbudt_001",
                "title": "Elektrisk anlegg tilstandsgradert - ikke tillatt",
                "gate_effect": {"blocks_96_gate": True},
            },
            {"finding_id": "L-BU-01", "title": "Lovlighet", "gate_effect": {"blocks_96_gate": True}},
        ],
        "all_findings": [
            {
                "finding_id": "B_TG_el_anlegg_tg_forbudt_001",
                "title": "Elektrisk anlegg tilstandsgradert - ikke tillatt",
                "evidence_snippets": ["Elektrisk anlegg med diverse kurser med automatsikringer"],
            },
            {"finding_id": "L-BU-01", "title": "Lovlighet"},
        ],
        "how_to_improve": [
            {"title": "Elektrisk anlegg tilstandsgradert - ikke tillatt"},
            {"title": "Lovlighet"},
        ],
        "category_breakdown": [
            {"category": "B - TG", "summary": "Elektrisk anlegg er feilaktig tilstandsgradert - dette er ikke tillatt fra 2026"},
        ],
    }
    detected_points = [
        {
            "point_id": "fremtind-electrical-installation",
            "title": "Elektrisk anlegg med diverse kurser med automatsikringer.",
            "tg": "",
            "no_tg_hms_point": True,
        }
    ]

    _drop_false_electrical_tg_forbidden_findings(analysis_output, detected_points)
    blob = json.dumps(analysis_output, ensure_ascii=False)

    assert "B_TG_el_anlegg_tg_forbudt_001" not in blob
    assert "Elektrisk anlegg tilstandsgradert" not in blob
    assert analysis_output["gate"]["blocked_by"] == ["L-BU-01"]
    assert analysis_output["gate"]["blocked_by_count"] == 1
    assert analysis_output["category_breakdown"][0]["summary"] == "Ingen scoretrekk i denne kategorien."


def test_electrical_tg_forbidden_kept_when_electrical_point_has_real_tg():
    analysis_output = {
        "all_findings": [
            {"finding_id": "B_TG_el_anlegg_tg_forbudt_001", "title": "Elektrisk anlegg tilstandsgradert - ikke tillatt"}
        ]
    }
    detected_points = [
        {
            "point_id": "fremtind-electrical-installation",
            "title": "Elektrisk anlegg",
            "tg": "TG2",
            "no_tg_hms_point": False,
        }
    ]

    _drop_false_electrical_tg_forbidden_findings(analysis_output, detected_points)

    assert analysis_output["all_findings"][0]["finding_id"] == "B_TG_el_anlegg_tg_forbudt_001"


def test_drainage_action_in_consequence_classified_as_tiltak_not_technical_development():
    result = _evaluate_arkat_point(
        point_id="fremtind-drainage",
        point_label="Tomteforhold > Fuktsikring og drenering",
        tg_grade="TG2",
        report_format="compressed_mixed",
        ns_version="NS3600:2018",
        raw_point_text="Konsekvens/tiltak Tiltak for redrenering rundt boligen kan ikke utelukkes.",
        extracted_fields={
            "aarsak": "Dagens drenering er fra boligens byggeår.",
            "risiko": "Dette kan påvirke boligens kjeller/underetasje negativt.",
            "konsekvens": "Tiltak for redrenering rundt boligen kan ikke utelukkes.",
            "anbefalt_tiltak": "MISSING",
        },
        report_context={},
        normalize_text=normalize_text,
        allow_llm=False,
    )

    consequence = result["field_results"]["konsekvens"]

    assert consequence["status"] == "WRONG"
    assert consequence["error_type"] == "TILTAK_AS_KONSEKVENS"
    assert consequence["error_type"] != "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS"


def test_kjolerom_action_sentence_recovered_from_konsekvens_tiltak_raw_text():
    raw = "KJELLER > KJØLEROM Teknisk anlegg Beskrivelse Kjøleraggregat. Vurdering av avvik: Det er påvist/opplyst at kjøleromsaggregat ikke fungerer. Konsekvens/tiltak Kjøleaggregat må skiftes ut/utbedres. Kostnadsestimat: 20 000 - 100 000"

    action = _best_action_sentence_from_text(raw, normalize_text)
    result = _normalize_arkat_eval_result(
        None,
        point_id="fremtind-special-room-surfaces",
        point_label="Spesialrom > Kjeller > Kjølerom > Teknisk anlegg",
        tg_grade="TG3",
        extracted_fields={"aarsak": "Det er påvist/opplyst at kjøleromsaggregat ikke fungerer", "risiko": "MISSING", "konsekvens": "MISSING", "anbefalt_tiltak": "MISSING"},
        raw_point_text=raw,
        ns_version="NS3600:2018",
        report_context={},
        normalize_text=normalize_text,
    )

    assert "Kjøleaggregat må skiftes ut/utbedres" in action
    assert result["field_results"]["anbefalt_tiltak"]["status"] == "WRONG"
    assert result["field_results"]["anbefalt_tiltak"]["error_type"] == "TILTAK_IMPERATIVE_FORM"


def test_missing_tg3_tiltak_finding_removed_when_final_raw_point_has_action():
    analysis_output = {
        "arkat_semantic_pipeline": {
            "points": [
                {
                    "point_id": "fremtind-special-room-surfaces",
                    "title": "Spesialrom > Kjeller > Kjølerom > Teknisk anlegg",
                    "tg_grade": "TG3",
                    "raw_point_text": "Konsekvens/tiltak Kjøleaggregat må skiftes ut/utbedres. Kostnadsestimat: 20 000 - 100 000",
                    "extracted_fields": {"anbefalt_tiltak": "MISSING"},
                }
            ]
        },
        "top_issues": [
            {"finding_id": "A_ARKAT.TG3.tiltak_missing_001", "title": "TG3 mangler anbefalt tiltak", "evidence_snippets": ["Spesialrom > Kjeller > Kjølerom > Teknisk anlegg - Det er påvist/opplyst at kjøleromsaggregat ikke fungerer"]},
            {"finding_id": "OTHER", "title": "Annet"},
        ],
        "all_findings": [
            {"finding_id": "A_ARKAT.TG3.tiltak_missing_001", "title": "TG3 mangler anbefalt tiltak", "evidence_snippets": ["Spesialrom > Kjeller > Kjølerom > Teknisk anlegg - Det er påvist/opplyst at kjøleromsaggregat ikke fungerer"]},
            {"finding_id": "OTHER", "title": "Annet"},
        ],
    }

    _drop_missing_tiltak_when_raw_action_present(analysis_output)
    blob = json.dumps(analysis_output, ensure_ascii=False)

    assert "A_ARKAT.TG3.tiltak_missing_001" not in blob
    assert "TG3 mangler anbefalt tiltak" not in blob
    assert "OTHER" in blob


def test_age_only_rule_suppressed_for_internal_water_and_drain_service_life():
    analysis_output = {
        "all_findings": [
            {
                "finding_id": "B_TG.age_only_violation_001",
                "title": "TG begrunnet hovedsakelig med alder uten tilstrekkelig faglig begrunnelse",
                "evidence_snippets": [
                    "Tekniske installasjoner > Vannledninger - Mer enn halvparten av forventet brukstid er passert på innvendige vannledninger",
                    "Tekniske installasjoner > Avløpsrør - Mer enn halvparten av forventet brukstid er passert på innvendige avløpsledninger",
                ],
                "gate_effect": {"blocks_96_gate": True},
            },
            {"finding_id": "OTHER", "title": "Annet"},
        ],
        "top_issues": [
            {
                "finding_id": "B_TG.age_only_violation_001",
                "title": "TG begrunnet hovedsakelig med alder uten tilstrekkelig faglig begrunnelse",
                "evidence_snippets": [
                    "Tekniske installasjoner > Vannledninger - Mer enn halvparten av forventet brukstid er passert på innvendige vannledninger",
                    "Tekniske installasjoner > Avløpsrør - Mer enn halvparten av forventet brukstid er passert på innvendige avløpsledninger",
                ],
            }
        ],
    }
    detected_points = [
        {"point_id": "fremtind-water-pipes", "title": "Tekniske installasjoner > Vannledninger", "effective_span_text": "Mer enn halvparten av forventet brukstid er passert på innvendige vannledninger."},
        {"point_id": "fremtind-drain-pipes", "title": "Tekniske installasjoner > Avløpsrør", "effective_span_text": "Mer enn halvparten av forventet brukstid er passert på innvendige avløpsledninger."},
    ]

    _drop_age_only_false_positives("", analysis_output, detected_points)
    blob = json.dumps(analysis_output, ensure_ascii=False)

    assert "B_TG.age_only_violation_001" not in blob
    assert "OTHER" in blob


def test_summary_sanitizer_removes_hva_avviket_betyr_for_kjoper_wording():
    analysis_output = {
        "category_breakdown": [
            {"category": "A - ARKAT", "summary": "Systematisk mangel på konkrete konsekvenser Alle TG2-punkter mangler tydelig forklaring av hva avviket betyr for kjøper i praksis"}
        ]
    }

    _normalize_category_summary_consequence_wording(analysis_output)
    summary = analysis_output["category_breakdown"][0]["summary"]

    assert "hva avviket betyr for kjøper" not in summary.lower()
    assert "mangler tydelig konkret konsekvens" in summary.lower()


def test_age_only_category_summary_removed_when_age_finding_suppressed():
    analysis_output = {
        "category_breakdown": [
            {"category": "B - TG", "summary": "TG-setting basert hovedsakelig på alder uten tilstrekkelig faglig begrunnelse for bygningsdeler som ikke er på tillegg C-listen"}
        ],
        "all_findings": [],
        "top_issues": [],
    }

    _scrub_age_only_category_summary_without_finding(analysis_output)
    summary = analysis_output["category_breakdown"][0]["summary"]

    assert "hovedsakelig på alder" not in summary.lower()
    assert summary == "Ingen scoretrekk i denne kategorien."


def test_final_category_summary_contract_sanitizes_non_a_buyer_wording():
    analysis_output = {
        "category_breakdown": [
            {"category": "F - Lovlighetsmangler", "summary": "Lovlighetsmangler er omtalt men konsekvens for kjøper kunne vært tydeligere"}
        ]
    }

    _finalize_category_summary_public_contracts(analysis_output)
    summary = analysis_output["category_breakdown"][0]["summary"]

    assert "konsekvens for kjøper" not in summary.lower()
    assert "konkret konsekvens" in summary.lower()


def test_final_category_summary_contract_removes_tg3_mangler_ogsaa_cost_variant():
    analysis_output = {
        "score_by_category": [{"category_id": "A", "deduction": 5}],
        "category_breakdown": [
            {"category": "A - ARKAT", "summary": "Flere TG2/TG3-punkter mangler deler av ARK/ARKAT-strukturen. TG3-punkter mangler også sjablongmessig kostnadsanslag"}
        ],
        "arkat_semantic_pipeline": {"points": [{"point_id": "x", "tg_grade": "TG3", "evaluation": {"field_results": {}}}]},
        "all_findings": [],
    }

    _finalize_category_summary_public_contracts(analysis_output)
    summary = analysis_output["category_breakdown"][0]["summary"].lower()

    assert "kostnadsanslag" not in summary
    assert "mangler også" not in summary

