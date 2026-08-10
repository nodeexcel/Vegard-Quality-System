#!/usr/bin/env python3
"""Compare three shadow results with VALIDERT-A4-2026-02 without altering them."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


EXPECTED = {
    "ivit": {"score": 54, "gate": True, "state": "complete_with_findings", "items": 9},
    "bolavi": {"score": 92, "gate": True, "state": "complete_with_findings", "items": 1},
    "bmtf": {"score": 97, "gate": False, "state": "complete_with_findings", "items": 1},
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(name: str, a2_path: Path, a4_path: Path) -> dict:
    a2 = json.loads(a2_path.read_text(encoding="utf-8"))
    a4 = json.loads(a4_path.read_text(encoding="utf-8"))
    segments = {item["segment_id"]: item for item in a2["segments"]}
    assessments = {item["assessment_id"]: item for item in a4["assessments"]}
    accepted = []
    for decision in a4["validation_decisions"]:
        if decision["admission"] != "accepted":
            continue
        assessment = assessments[decision["assessment_id"]]
        segment = segments[assessment["segment_id"]]
        accepted.append({
            "point": decision.get("canonical_point_id"),
            "title": segment["title"],
            "section_context": segment.get("section_context"),
            "rule_category": assessment["rule_category"],
            "proposed_finding_type": assessment.get("proposed_finding_type"),
            "canonical_finding_identity": decision.get("canonical_finding_identity"),
            "deduction": decision.get("deduction"),
            "blocks_96_gate": decision.get("blocks_96_gate"),
        })
    score = a4.get("score_result") or {}
    expected = EXPECTED[name]
    actual = {
        "score": score.get("score"),
        "score_valid": score.get("score_valid"),
        "gate": score.get("gate_blocked"),
        "state": a4.get("analysis_state"),
        "items": len(a4.get("normalized_customer_items") or []),
    }
    variances = [
        {"field": key, "expected": value, "actual": actual.get(key)}
        for key, value in expected.items() if actual.get(key) != value
    ]
    return {
        "report": name,
        "a2_path": str(a2_path), "a2_sha256": digest(a2_path),
        "a4_path": str(a4_path), "a4_sha256": digest(a4_path),
        "expected": expected, "actual": actual,
        "accepted_findings": accepted,
        "variances": variances,
        "passes_frozen_summary": not variances,
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
        "specification_id": "VALIDERT-A4-2026-02",
        "all_frozen_summaries_pass": all(item["passes_frozen_summary"] for item in results),
        "reports": results,
    }
    args.json_output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Frozen A4 variance report", "", "Specification: `VALIDERT-A4-2026-02`", ""]
    for item in results:
        lines.extend([
            f"## {item['report'].upper()}", "",
            f"Result: **{'PASS' if item['passes_frozen_summary'] else 'VARIANCE'}**", "",
            f"Expected: `{json.dumps(item['expected'], sort_keys=True)}`", "",
            f"Actual: `{json.dumps(item['actual'], sort_keys=True)}`", "",
        ])
        for variance in item["variances"]:
            lines.append(f"- `{variance['field']}`: expected `{variance['expected']}`, actual `{variance['actual']}`")
        lines.extend(["", "Accepted findings:", "", "```json", json.dumps(item["accepted_findings"], ensure_ascii=False, indent=2), "```", ""])
    args.markdown_output.write_text("\n".join(lines), encoding="utf-8")
    return 0 if payload["all_frozen_summaries_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
