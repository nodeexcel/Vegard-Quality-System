import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


_ALLOWED_LETTER_REPAIRS = (
    ("Ventilason", "Ventilasjon"),
    ("ventilasonsløsning", "ventilasjonsløsning"),
    ("ventilason", "ventilasjon"),
    ("funksonssvikt", "funksjonssvikt"),
    ("funksoner", "funksjoner"),
    ("funkson", "funksjon"),
    ("isolasonsevne", "isolasjonsevne"),
    ("genvrende", "gjenværende"),
    ("skøtene", "skjøtene"),
    ("slitase", "slitasje"),
)


def _repair_text(value: str) -> str:
    for before, after in _ALLOWED_LETTER_REPAIRS:
        value = value.replace(before, after)
    return value


def _leaf_differences(before: Any, after: Any, path: str) -> List[Dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        differences: List[Dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            differences.extend(_leaf_differences(before.get(key), after.get(key), f"{path}.{key}"))
        return differences
    if isinstance(before, list) and isinstance(after, list):
        differences = []
        for index in range(max(len(before), len(after))):
            left = before[index] if index < len(before) else None
            right = after[index] if index < len(after) else None
            differences.extend(_leaf_differences(left, right, f"{path}[{index}]"))
        return differences
    if before == after:
        return []
    return [{"json_path": path, "before": before, "after": after}]


def _classify_leaf_difference(difference: Dict[str, Any]) -> str:
    before = difference.get("before")
    after = difference.get("after")
    path = str(difference.get("json_path") or "")
    if isinstance(before, str) and isinstance(after, str) and _repair_text(before) == after:
        return "letter_preserving_repair"
    if (
        isinstance(before, int)
        and isinstance(after, int)
        and ".arkat_field_binding_evidence." in path
        and path.endswith(".offset")
    ):
        return "binding_offset_recomputed_against_emitted_raw"
    return ""

def _sha256_of_json_value(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _points_by_id(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    points = (
        report.get("dommer_b_full", {}).get("points")
        or report.get("analysis_output", {}).get("arkat_semantic_pipeline", {}).get("points")
        or []
    )
    out: Dict[str, Dict[str, Any]] = {}
    for point in points:
        if not isinstance(point, dict):
            continue
        point_id = str(point.get("point_id") or "").strip()
        if point_id:
            out[point_id] = point
    return out


def _compare_layer(
    point_id: str,
    layer: str,
    baseline_value: Any,
    delivered_value: Any,
    mismatches: List[Dict[str, Any]],
) -> Dict[str, Any]:
    baseline_hash = _sha256_of_json_value(baseline_value)
    delivered_hash = _sha256_of_json_value(delivered_value)
    equal = baseline_value == delivered_value
    if not equal:
        mismatches.append(
            {
                "point_id": point_id,
                "json_path": f"dommer_b_full.points[{point_id!r}].{layer}",
                "before": baseline_value,
                "after": delivered_value,
            }
        )
    return {
        "equal": equal,
        "baseline_hash": baseline_hash,
        "delivered_hash": delivered_hash,
    }


def _binding_recoverability(report: Dict[str, Any]) -> Dict[str, Any]:
    """Apply Vegard's locked Unicode-character offset verification method."""
    entries: List[Dict[str, Any]] = []
    for point_id, point in _points_by_id(report).items():
        raw = point.get("raw_point_text")
        evidence = point.get("arkat_field_binding_evidence")
        if not isinstance(raw, str) or not isinstance(evidence, dict):
            continue
        for field, bindings in evidence.items():
            if not isinstance(bindings, list):
                continue
            for index, entry in enumerate(bindings):
                if not isinstance(entry, dict):
                    continue
                bound_text = entry.get("text")
                offset = entry.get("offset")
                valid = False
                actual = None
                if isinstance(bound_text, str) and isinstance(offset, int):
                    actual = raw[offset : offset + len(bound_text)]
                    valid = actual == bound_text
                if not valid:
                    entries.append(
                        {
                            "point_id": point_id,
                            "json_path": (
                                f"dommer_b_full.points[{point_id!r}]"
                                f".arkat_field_binding_evidence.{field}[{index}]"
                            ),
                            "offset": offset,
                            "text": bound_text,
                            "actual": actual,
                        }
                    )
    total = sum(
        len(bindings)
        for point in _points_by_id(report).values()
        for bindings in (point.get("arkat_field_binding_evidence") or {}).values()
        if isinstance(bindings, list)
    )
    return {
        "method": "actual = raw_point_text[offset:offset + len(entry['text'])]; valid = (actual == entry['text'])",
        "position_unit": "Unicode characters",
        "total": total,
        "valid": total - len(entries),
        "invalid": len(entries),
        "invalid_entries": entries,
    }


def build_parity_diff(
    baseline_path: Path,
    delivered_path: Path,
) -> Dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    delivered = json.loads(delivered_path.read_text(encoding="utf-8"))

    base_points = _points_by_id(baseline)
    del_points = _points_by_id(delivered)
    point_ids = sorted(set(base_points.keys()) | set(del_points.keys()), key=lambda pid: [int(p) for p in pid.split(".")])

    mismatches: List[Dict[str, Any]] = []
    per_point: Dict[str, Any] = {}
    for point_id in point_ids:
        b = base_points.get(point_id, {})
        d = del_points.get(point_id, {})
        per_point[point_id] = {
            "raw_point_text": _compare_layer(point_id, "raw_point_text", b.get("raw_point_text"), d.get("raw_point_text"), mismatches),
            "extracted_fields": _compare_layer(point_id, "extracted_fields", b.get("extracted_fields"), d.get("extracted_fields"), mismatches),
            "arkat_field_binding_evidence": _compare_layer(
                point_id,
                "arkat_field_binding_evidence",
                b.get("arkat_field_binding_evidence"),
                d.get("arkat_field_binding_evidence"),
                mismatches,
            ),
            "evaluation": _compare_layer(
                point_id,
                "evaluation",
                b.get("evaluation"),
                d.get("evaluation"),
                mismatches,
            ),
        }

    whitelist: List[Dict[str, Any]] = []
    unauthorized_differences: List[Dict[str, Any]] = []
    for point_id in point_ids:
        baseline_point = base_points.get(point_id, {})
        delivered_point = del_points.get(point_id, {})
        for layer in ("raw_point_text", "extracted_fields", "arkat_field_binding_evidence", "evaluation"):
            path = f"dommer_b_full.points[{point_id!r}].{layer}"
            for difference in _leaf_differences(
                baseline_point.get(layer), delivered_point.get(layer), path
            ):
                difference["point_id"] = point_id
                reason = _classify_leaf_difference(difference)
                if reason:
                    difference["reason"] = reason
                    whitelist.append(difference)
                else:
                    unauthorized_differences.append(difference)
    return {
        "baseline": {
            "filename": baseline_path.name,
            "sha256": _file_sha(baseline_path),
        },
        "delivered": {
            "filename": delivered_path.name,
            "sha256": _file_sha(delivered_path),
        },
        "layers": ["raw_point_text", "extracted_fields", "arkat_field_binding_evidence", "evaluation"],
        "per_point": per_point,
        "mismatches": mismatches,
        "whitelist": whitelist,
        "unauthorized_differences": unauthorized_differences,
        "binding_recoverability": {
            "baseline": _binding_recoverability(baseline),
            "delivered": _binding_recoverability(delivered),
        },
        "overall_equal": len(mismatches) == 0,
        "overall_accepted": len(unauthorized_differences) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Bolavi parity diff JSON")
    parser.add_argument("--baseline", required=True, help="Baseline report path")
    parser.add_argument("--delivered", required=True, help="Delivered report path")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    delivered_path = Path(args.delivered)
    output_path = Path(args.output)
    result = build_parity_diff(baseline_path, delivered_path)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote parity diff: {output_path}")
    print(f"overall_equal={result['overall_equal']} mismatches={len(result['mismatches'])}")
    print(
        f"overall_accepted={result['overall_accepted']} "
        f"whitelist={len(result['whitelist'])} "
        f"unauthorized={len(result['unauthorized_differences'])}"
    )
    print(f"baseline={result['baseline']['filename']} sha={result['baseline']['sha256']}")
    print(f"delivered={result['delivered']['filename']} sha={result['delivered']['sha256']}")


if __name__ == "__main__":
    main()
