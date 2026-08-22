#!/usr/bin/env python3
"""Finding-aware comparison against the authoritative controlled A4 targets."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


EXPECTED = {
    "ivit": {
        "score": 59, "gate": True, "state": "complete_with_findings", "items": 10,
        "findings": [
            ("Balkonger_terrasser_og_rom_under_balkonger", "MISSING (risiko)"),
            ("Balkonger_terrasser_og_rom_under_balkonger", "TILTAK_AS_KONSEKVENS"),
            ("Overflater_vegger_og_himling", "RISIKO_AS_KONSEKVENS"),
            ("1.8", "TGIU_MISSING_FURTHER_INVESTIGATION"),
            ("Grunnmur_og_fundamenter", "MISSING (risiko)"),
            ("Grunnmur_og_fundamenter", "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS"),
            ("Septiktank", "TGIU_MISSING_REASON"),
            ("Septiktank", "TGIU_MISSING_FURTHER_INVESTIGATION"),
            ("Oljetank", "TGIU_MISSING_REASON"),
            ("Oljetank", "TGIU_MISSING_FURTHER_INVESTIGATION"),
            ("Sjøbod", "E_METHOD.garasje_avvik_uten_arkat"),
            ("Båtbu", "E_METHOD.garasje_avvik_uten_arkat"),
            ("Garasje", "E_METHOD.garasje_avvik_uten_arkat"),
            ("Lovlighet", "L-AV-01"),
        ],
        "mandatory_non_findings": [
            ("Taktekking", "MISSING (risiko)"),
            ("Nedløp_og_beslag", "MISSING (risiko)"),
            ("Vinduer", "MISSING (risiko)"),
        ],
    },
    "bolavi": {
        "score": 70, "gate": True, "state": "complete_with_findings", "items": 2,
        "findings": [
            ("3", "E_METHOD.tg3_cost_missing"),
            ("10.1", "MISSING (aarsak)"),
            ("10.1", "MISSING (risiko)"),
            ("10.1", "MISSING (konsekvens)"),
            ("10.1", "E_METHOD.tg2_missing_anbefalt_tiltak_ns2025"),
        ],
        "mandatory_non_findings": [],
    },
    "bmtf": {
        "score": 97, "gate": False, "state": "complete_with_findings", "items": 1,
        "findings": [
            ("7.4", "A_ARKAT_SEMANTIC.RISIKO.LIMITATION_USED_AS_RISK_SUBSTITUTE"),
        ],
        "mandatory_non_findings": [
            ("7.2", "MISSING (aarsak)"),
            ("23.3", "E_METHOD.tg3_cost_missing"),
        ],
    },
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(name: str, a2_path: Path, a4_path: Path) -> dict:
    a2 = json.loads(a2_path.read_text(encoding="utf-8"))
    a4 = json.loads(a4_path.read_text(encoding="utf-8"))
    segments = {item["segment_id"]: item for item in a2["segments"]}
    assessments = {item["assessment_id"]: item for item in a4["assessments"]}
    accepted = []
    accepted_ids = []
    for decision in a4["validation_decisions"]:
        if decision["admission"] != "accepted":
            continue
        assessment = assessments[decision["assessment_id"]]
        segment = segments[assessment["segment_id"]]
        accepted_ids.append(decision["accepted_finding_id"])
        accepted.append({
            "accepted_finding_id": decision["accepted_finding_id"],
            "point": decision.get("canonical_point_id"),
            "title": segment["title"],
            "section_context": segment.get("section_context"),
            "identity": decision.get("canonical_finding_identity"),
            "category": decision.get("category"),
            "deduction": decision.get("deduction"),
        })

    expected = EXPECTED[name]
    expected_counter = Counter(tuple(item) for item in expected["findings"])
    actual_counter = Counter((item["point"], item["identity"]) for item in accepted)
    missing = list((expected_counter - actual_counter).elements())
    unexpected = list((actual_counter - expected_counter).elements())
    duplicates = [
        {"point": point, "identity": identity, "count": count}
        for (point, identity), count in actual_counter.items() if count > 1
    ]
    prohibited = [
        {"point": point, "identity": identity}
        for point, identity in expected["mandatory_non_findings"]
        if actual_counter[(point, identity)]
    ]

    items = a4.get("normalized_customer_items") or []
    projected_ids = [finding_id for item in items for finding_id in item.get("accepted_finding_ids", [])]
    lineage = a4.get("finding_lineage") or []
    lineage_ids = [item["accepted_finding_id"] for item in lineage if item.get("public_projection_status") == "projected"]
    lineage_errors = {
        "missing_from_items": sorted(set(accepted_ids) - set(projected_ids)),
        "extra_in_items": sorted(set(projected_ids) - set(accepted_ids)),
        "non_unique_item_dispositions": sorted(key for key, count in Counter(projected_ids).items() if count != 1),
        "missing_from_lineage": sorted(set(accepted_ids) - set(lineage_ids)),
        "non_unique_lineage_dispositions": sorted(key for key, count in Counter(lineage_ids).items() if count != 1),
    }
    lineage_pass = not any(lineage_errors.values())

    score = a4.get("score_result") or {}
    actual_summary = {
        "score": score.get("score"), "score_valid": score.get("score_valid"),
        "gate": score.get("gate_blocked"), "state": a4.get("analysis_state"),
        "items": len(items), "accepted_raw_findings": len(accepted),
    }
    summary_variances = [
        {"field": key, "expected": expected[key], "actual": actual_summary.get(key)}
        for key in ("score", "gate", "state", "items") if actual_summary.get(key) != expected[key]
    ]
    finding_set_pass = not (missing or unexpected or duplicates or prohibited)
    passes = not summary_variances and finding_set_pass and lineage_pass
    return {
        "report": name,
        "a2_path": str(a2_path), "a2_sha256": digest(a2_path),
        "a4_path": str(a4_path), "a4_sha256": digest(a4_path),
        "expected_summary": {key: expected[key] for key in ("score", "gate", "state", "items")},
        "actual_summary": actual_summary,
        "accepted_findings": accepted,
        "missing_expected_findings": missing,
        "unexpected_findings": unexpected,
        "duplicate_findings": duplicates,
        "prohibited_findings_present": prohibited,
        "lineage": {"passes": lineage_pass, **lineage_errors},
        "summary_variances": summary_variances,
        "passes_authoritative_acceptance": passes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in EXPECTED:
        parser.add_argument(f"--{name}-a2", type=Path, required=True)
        parser.add_argument(f"--{name}-a4", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    results = [summarize(name, getattr(args, f"{name}_a2"), getattr(args, f"{name}_a4")) for name in EXPECTED]
    payload = {
        "specification_id": "VALIDERT-A4-2026-02-with-approved-decisions-2026-08-12",
        "finding_aware": True,
        "all_reports_pass": all(item["passes_authoritative_acceptance"] for item in results),
        "reports": results,
    }
    args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Finding-aware A4 acceptance report", ""]
    for item in results:
        lines.extend([
            f"## {item['report'].upper()}", "",
            f"Result: **{'PASS' if item['passes_authoritative_acceptance'] else 'FAIL'}**", "",
            f"Expected summary: `{json.dumps(item['expected_summary'], sort_keys=True)}`", "",
            f"Actual summary: `{json.dumps(item['actual_summary'], sort_keys=True)}`", "",
            f"Missing expected findings: `{json.dumps(item['missing_expected_findings'], ensure_ascii=False)}`", "",
            f"Unexpected findings: `{json.dumps(item['unexpected_findings'], ensure_ascii=False)}`", "",
            f"Duplicates: `{json.dumps(item['duplicate_findings'], ensure_ascii=False)}`", "",
            f"Prohibited findings present: `{json.dumps(item['prohibited_findings_present'], ensure_ascii=False)}`", "",
            f"Lineage pass: `{item['lineage']['passes']}`", "",
        ])
    args.markdown_output.write_text("\n".join(lines), encoding="utf-8")
    return 0 if payload["all_reports_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
