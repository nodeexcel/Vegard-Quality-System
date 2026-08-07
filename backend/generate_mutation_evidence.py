"""Generate inspectable mutation and regression evidence artifacts.

Runs the same logic as test_bolavi_mutation_checks_fire_for_each_arkat_field_class
and test_cross_format_regression_smoke_set_covers_required_runtime_axes but captures
the full before/after data into a JSON evidence file for client review.

Output: files/mutation_regression_evidence.json
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DATABASE_URL", "sqlite:///tmp.db")
os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("SECRET_KEY", "dummy")

from app.services.ai_analyzer import (  
    _ensure_semantic_tg3_cost_backstop,
    _drop_false_electrical_tg_forbidden_findings,
    _normalize_tg3_cost_text as normalize_text,
)
from app.services.arkat_semantic_pipeline import (  
    _detect_ns_version_for_dommer_b,
    _evaluate_arkat_point,
    _extract_fields_for_point,
)
from app.services.validert_files import get_runtime_manifest  


# ---------------------------------------------------------------------------
# Mutation evidence
# ---------------------------------------------------------------------------

CANONICAL_FIELDS = {
    "aarsak": "Manglende fall mot sluk er registrert.",
    "risiko": "Manglende fall kan føre til vannansamlinger og økt risiko for fuktskader.",
    "konsekvens": "Dette kan gi skjulte fuktskader og økte utbedringskostnader over tid.",
    "anbefalt_tiltak": "Det anbefales å etablere tilfredsstillende fall mot sluk.",
}

MUTATIONS = [
    ("aarsak",          "MISSING",                                        "MISSING",   "MISSING (aarsak)"),
    ("risiko",          "MISSING",                                        "MISSING",   "MISSING (risiko)"),
    ("konsekvens",      "Fukt kan trenge inn bak konstruksjonen.",         "WRONG",     "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS"),
    ("anbefalt_tiltak", "MISSING",                                        "MISSING",   "MISSING (anbefalt_tiltak)"),
]


def _run_arkat_eval(fields, tg_grade):
    return _evaluate_arkat_point(
        point_id="MUT-1",
        point_label="Mutasjonstest våtrom",
        tg_grade=tg_grade,
        report_format="semi_structured",
        ns_version="NS3600:2025",
        raw_point_text="\n".join(str(v) for v in fields.values()),
        extracted_fields=fields,
        report_context={},
        normalize_text=normalize_text,
        allow_llm=False,
    )


def build_mutation_evidence():
    records = []
    for field_name, mutated_value, expected_status, expected_error in MUTATIONS:
        tg_grade = "TG3" if field_name == "anbefalt_tiltak" else "TG2"

        # Error statuses trigger a finding; anything else (CORRECT, None, "") is clean
        _ERROR_STATUSES = {"MISSING", "WRONG", "DUPLICATE", "TOO_SHORT", "TOO_LONG"}

        def _is_error(field_result):
            return str(field_result.get("status") or "").upper() in _ERROR_STATUSES

        # Before (canonical) — no finding expected
        before_fields = dict(CANONICAL_FIELDS)
        before_result = _run_arkat_eval(before_fields, tg_grade)
        before_field_result = before_result.get("field_results", {}).get(field_name, {})
        before_has_error = _is_error(before_field_result)

        # Mutated — finding must fire
        mutated_fields = dict(CANONICAL_FIELDS)
        mutated_fields[field_name] = mutated_value
        mutated_result = _run_arkat_eval(mutated_fields, tg_grade)
        mutated_field_result = mutated_result.get("field_results", {}).get(field_name, {})

        # Revert (back to canonical) — finding must not fire
        revert_fields = dict(CANONICAL_FIELDS)
        revert_result = _run_arkat_eval(revert_fields, tg_grade)
        revert_field_result = revert_result.get("field_results", {}).get(field_name, {})
        revert_has_error = _is_error(revert_field_result)

        passed = (
            mutated_field_result.get("status") == expected_status
            and mutated_field_result.get("error_type") == expected_error
            and not before_has_error
            and not revert_has_error
        )

        records.append({
            "field": field_name,
            "tg_grade": tg_grade,
            "mutation": {
                "before_value": CANONICAL_FIELDS[field_name],
                "mutated_value": mutated_value,
            },
            "before_run": {
                "status": before_field_result.get("status"),
                "error_type": before_field_result.get("error_type"),
                "finding_fires": before_has_error,
            },
            "mutated_run": {
                "status": mutated_field_result.get("status"),
                "error_type": mutated_field_result.get("error_type"),
                "finding_fires": mutated_field_result.get("status") == expected_status,
                "expected_status": expected_status,
                "expected_error_type": expected_error,
            },
            "revert_run": {
                "status": revert_field_result.get("status"),
                "error_type": revert_field_result.get("error_type"),
                "finding_fires": revert_has_error,
            },
            "passed": passed,
        })
    return records


# ---------------------------------------------------------------------------
# Cross-format regression evidence
# ---------------------------------------------------------------------------

def build_cross_format_evidence():
    records = []

    # E3/BMTF — unlabeled_prose, all fields present, no errors expected
    e3_bmtf = _evaluate_arkat_point(
        point_id="7.1.3",
        point_label="Membran, tettesjiktet og sluk",
        tg_grade="TG2",
        report_format="unlabeled_prose",
        ns_version="NS3600:2025",
        raw_point_text=(
            "Membran har alder. Fukt kan gi skjulte skader. "
            "Dette kan medføre behov for omfattende utbedring og kostnader for kjøper. "
            "Oppgradering bør vurderes."
        ),
        extracted_fields={
            "aarsak": "Membran har alder.",
            "risiko": "Fukt kan gi skjulte skader.",
            "konsekvens": "Dette kan medføre behov for omfattende utbedring og kostnader for kjøper.",
            "anbefalt_tiltak": "Oppgradering bør vurderes.",
        },
        report_context={},
        normalize_text=normalize_text,
        allow_llm=False,
    )
    records.append({
        "case": "E3/BMTF (unlabeled_prose)",
        "report_id": "synthetic-E3-BMTF-7.1.3",
        "ns_version": "NS3600:2025",
        "report_format": "unlabeled_prose",
        "expected_findings": [],
        "actual_has_errors": e3_bmtf["has_errors"],
        "actual_findings": [
            {"field": fld, "status": res.get("status"), "error_type": res.get("error_type")}
            for fld, res in e3_bmtf.get("field_results", {}).items()
            if isinstance(res, dict) and str(res.get("status") or "").upper() in {"MISSING", "WRONG", "DUPLICATE", "TOO_SHORT", "TOO_LONG"}
        ],
        "passed": not e3_bmtf["has_errors"],
    })

    # Fremtind — compressed_mixed format field extraction
    fremtind = _extract_fields_for_point(
        "compressed_mixed",
        "Årsak: Avviket skyldes alder. Risiko: Det kan føre til fuktskader. "
        "Konsekvens: Skader kan gi utbedringsbehov. Tiltak: Utbedring anbefales.",
        lambda _text, _field: "",
        normalize_text,
    )
    records.append({
        "case": "Fremtind (compressed_mixed)",
        "report_id": "synthetic-Fremtind-extraction",
        "ns_version": "NS3600:2025",
        "report_format": "compressed_mixed",
        "expected_findings": ["aarsak extracted", "risiko extracted"],
        "actual_extracted_fields": list(fremtind.keys()),
        "passed": "aarsak" in fremtind and "risiko" in fremtind,
    })

    # NS3600:2018 regime detection
    ns_2018, meta_2018 = _detect_ns_version_for_dommer_b(
        "Rapporten er utarbeidet etter NS 3600:2018.",
        report_date="2025-12-20",
        context_ns_version="",
        normalize_text=normalize_text,
    )
    records.append({
        "case": "NS3600:2018 regime detection",
        "report_id": "synthetic-NS2018-detection",
        "input_text": "Rapporten er utarbeidet etter NS 3600:2018.",
        "report_date": "2025-12-20",
        "expected_ns_version": "NS3600:2018",
        "expected_source": "report_text",
        "actual_ns_version": ns_2018,
        "actual_source": meta_2018.get("source"),
        "passed": (ns_2018, meta_2018.get("source")) == ("NS3600:2018", "report_text"),
    })

    # NS3600:2025 regime detection
    ns_2025, meta_2025 = _detect_ns_version_for_dommer_b(
        "Rapporten er utarbeidet etter NS 3600:2025.",
        report_date="2026-05-26",
        context_ns_version="",
        normalize_text=normalize_text,
    )
    records.append({
        "case": "NS3600:2025 regime detection",
        "report_id": "synthetic-NS2025-detection",
        "input_text": "Rapporten er utarbeidet etter NS 3600:2025.",
        "report_date": "2026-05-26",
        "expected_ns_version": "NS3600:2025",
        "expected_detail": "ns3600_2025",
        "actual_ns_version": ns_2025,
        "actual_detail": meta_2025.get("detail"),
        "passed": (ns_2025, meta_2025.get("detail")) == ("NS3600:2025", "ns3600_2025"),
    })

    # TG3 cost backstop
    tg3_output = {
        "meta": {"ns_version": "NS 3600:2025"},
        "all_findings": [],
        "arkat_semantic_pipeline": {
            "ns_version": "NS3600:2025",
            "report_format": "semi_structured",
            "points": [{
                "point_id": "3",
                "title": "Terrengforhold",
                "tg_grade": "TG3",
                "raw_point_text": "TG 3 Terrengforhold. Det anbefales fall fra grunnmur.",
                "extracted_fields": {},
            }],
        },
    }
    _ensure_semantic_tg3_cost_backstop("", tg3_output)
    tg3_cost_findings = [
        item for item in tg3_output["all_findings"]
        if item.get("rule_id") == "E_METHOD.tg3_cost_missing"
    ]
    records.append({
        "case": "TG3-cost backstop",
        "report_id": "synthetic-TG3-cost-backstop",
        "ns_version": "NS3600:2025",
        "expected_findings": ["E_METHOD.tg3_cost_missing"],
        "actual_findings": [f.get("rule_id") for f in tg3_cost_findings],
        "passed": bool(tg3_cost_findings),
    })

    # TGIU case
    from app.services.arkat_semantic_pipeline import _evaluate_arkat_point as _eval
    tgiu = _eval(
        point_id="TGIU-1",
        point_label="Loft",
        tg_grade="TGIU",
        report_format="semi_structured",
        ns_version="NS3600:2025",
        raw_point_text="Loftkonstruksjonen er lukket og kan ikke vurderes.",
        extracted_fields={"aarsak": "", "risiko": "", "konsekvens": "", "anbefalt_tiltak": ""},
        report_context={},
        normalize_text=normalize_text,
        allow_llm=False,
    )
    tgiu_findings = tgiu.get("tgiu_findings", {}).get("findings", [])
    actual_tgiu_findings = [
        str(f.get("error_type") or f.get("rule_id") or f.get("finding_id") or "").strip()
        for f in tgiu_findings
        if isinstance(f, dict)
    ]
    expected_tgiu_findings = [
        "TGIU_MISSING_REASON",
        "TGIU_MISSING_FURTHER_INVESTIGATION",
        "TGIU_MISSING_MOISTURE_FLAG",
    ]
    records.append({
        "case": "TGIU case",
        "report_id": "synthetic-TGIU-loft",
        "ns_version": "NS3600:2025",
        "expected_tgiu_findings": expected_tgiu_findings,
        "actual_tgiu_findings": actual_tgiu_findings,
        "passed": sorted(actual_tgiu_findings) == sorted(expected_tgiu_findings),
    })

    # lovlighet/el/no-TG — el_tg_forbidden finding remains when point has TG2 (active finding)
    legal_no_tg = {
        "all_findings": [{
            "rule_id": "E_METHOD.el_tg_forbidden",
            "point_id": "L-EL",
            "message": "Elektrisk anlegg skal ikke ha TG.",
        }]
    }
    detected_points_with_tg2 = [{"point_id": "L-EL", "title": "Elektrisk anlegg", "tg": "TG2"}]
    _drop_false_electrical_tg_forbidden_findings(legal_no_tg, detected_points_with_tg2)
    el_findings_remaining = legal_no_tg["all_findings"]
    records.append({
        "case": "lovlighet/el/no-TG (el_tg_forbidden kept when TG2 present)",
        "report_id": "synthetic-el-tg-forbidden",
        "input": "Point L-EL has TG2, el_tg_forbidden finding present",
        "expected_findings": ["E_METHOD.el_tg_forbidden kept (TG2 present on el point)"],
        "actual_findings": [f.get("rule_id") for f in el_findings_remaining],
        "passed": bool(el_findings_remaining),
    })

    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Running mutation evidence collection...")
    mutation_evidence = build_mutation_evidence()
    print(f"  {len(mutation_evidence)} mutation cases collected")

    print("Running cross-format regression evidence collection...")
    cross_format_evidence = build_cross_format_evidence()
    print(f"  {len(cross_format_evidence)} cross-format cases collected")

    mutation_passed = all(r["passed"] for r in mutation_evidence)
    cross_format_passed = all(r["passed"] for r in cross_format_evidence)

    evidence = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "governance": get_runtime_manifest("mutation_cross_format_evidence"),
        "summary": {
            "mutation_checks": {
                "total": len(mutation_evidence),
                "passed": sum(1 for r in mutation_evidence if r["passed"]),
                "all_passed": mutation_passed,
            },
            "cross_format_regression": {
                "total": len(cross_format_evidence),
                "passed": sum(1 for r in cross_format_evidence if r["passed"]),
                "all_passed": cross_format_passed,
            },
            "overall_passed": mutation_passed and cross_format_passed,
        },
        "mutation_checks": mutation_evidence,
        "cross_format_regression": cross_format_evidence,
    }

    out_path = ROOT / "files" / "mutation_regression_evidence.json"
    out_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nEvidence written to: {out_path}")

    summary = evidence["summary"]
    print(f"\nSummary:")
    print(f"  Mutation checks:          {summary['mutation_checks']['passed']}/{summary['mutation_checks']['total']} passed")
    print(f"  Cross-format regression:  {summary['cross_format_regression']['passed']}/{summary['cross_format_regression']['total']} passed")
    print(f"  Overall: {'PASS' if summary['overall_passed'] else 'FAIL'}")
    if not summary["overall_passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
