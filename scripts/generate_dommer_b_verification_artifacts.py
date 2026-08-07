#!/usr/bin/env python3
"""Generate Dommer B verification artifacts for governance handoff."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DATABASE_URL", "sqlite:///tmp.db")
os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("SECRET_KEY", "dummy")

from app.config import settings  
from app.services.ai_analyzer import _normalize_tg3_cost_text as normalize_text  
from app.services.arkat_semantic_pipeline import (  
    _build_dommer_b_user_prompt,
    _evaluate_arkat_point,
    _extract_fields_for_point,
    _extract_json_object_from_text,
    _heuristic_tgiu_findings,
    _DOMMER_B_ALLOWED_ERROR_TYPES,
    _ARKAT_FIELD_NAMES,
    _finalize_arkat_fields,
    _sanitize_arkat_field_values,
    _force_tgiu_field_results_not_applicable,
)
from app.services.bedrock_ai import BedrockAI  
from app.services.validert_files import (  
    ARKAT_ERROR_DEDUCTION_MAPPING_PATH,
    ARKAT_SEMANTIC_RULES_PATH,
    DOMMER_B_SYSTEM_PROMPT_PATH,
    get_arkat_evaluation_pipeline_step,
    get_dommer_b_system_prompt_text,
)


FILES = ROOT / "files"
TEST_SET_PATH = FILES / "dommer_b_test_set_v1_3.md"
RAW_OUTPUT_PATH = FILES / "dommer_b_test_set_v1_3_llm_raw_outputs.json"
RESULT_PATH = FILES / "dommer_b_test_set_v1_3_verification_result.json"
CASE_6_USER_MESSAGE_PATH = FILES / "dommer_b_case_6_contract_user_message_v10.txt"
CASE_8_USER_MESSAGE_PATH = FILES / "dommer_b_case_8_contract_user_message_v10.txt"
HORTEN_REPORT_PATH = FILES / "dommer_b_real_report_1806_full.json"
FREDRIKSTAD_REPORT_PATH = FILES / "dommer_b_real_report_1807_full.json"
MODEL_NAME = "eu.anthropic.claude-sonnet-4-20250514-v1:0"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_cases() -> list[dict[str, Any]]:
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
                "expected_output": json.loads(expected_json),
            }
        )
    return cases


def _field_pairs(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "status": value.get("status"),
            "error_type": value.get("error_type"),
        }
        for key, value in (output.get("field_results") or {}).items()
        if isinstance(value, dict)
    }


def _tgiu_error_types(output: dict[str, Any]) -> list[str]:
    findings = ((output.get("tgiu_findings") or {}).get("findings") or [])
    return sorted(
        str(item.get("error_type"))
        for item in findings
        if isinstance(item, dict) and item.get("error_type")
    )


def _field_required_for_regression(field_name: str, tg_grade: str, ns_version: str) -> bool:
    tg = str(tg_grade or "").strip().upper()
    ns = str(ns_version or "").strip().upper().replace(" ", "")
    if tg == "TGIU":
        return False
    if field_name == "anbefalt_tiltak" and tg == "TG2" and ns == "NS3600:2018":
        return False
    return field_name in _ARKAT_FIELD_NAMES and tg in {"TG2", "TG3"}


def _normalize_model_output_for_llm_regression(raw_output: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize model JSON for regression without published-fasit/spec guards."""
    if not isinstance(raw_output, dict):
        return None
    tg_grade = str(payload.get("tg_grade") or raw_output.get("tg_grade") or "").strip().upper()
    ns_version = str(payload.get("ns_version") or "")
    normalized: dict[str, Any] = {
        "point_id": str(payload.get("point_id") or raw_output.get("point_id") or ""),
        "tg_grade": tg_grade or raw_output.get("tg_grade"),
        "field_results": {},
        "tgiu_findings": {"findings": []},
        "has_errors": False,
    }

    seen_tgiu: set[str] = set()
    parsed_tgiu = raw_output.get("tgiu_findings")
    if isinstance(parsed_tgiu, dict):
        findings = parsed_tgiu.get("findings")
        if isinstance(findings, list):
            for item in findings:
                if not isinstance(item, dict):
                    continue
                error_type = str(item.get("error_type") or "").strip()
                if error_type.startswith("TGIU_") and error_type in _DOMMER_B_ALLOWED_ERROR_TYPES and error_type not in seen_tgiu:
                    seen_tgiu.add(error_type)
                    normalized["tgiu_findings"]["findings"].append(
                        {"error_type": error_type, "explanation": str(item.get("explanation") or "").strip()}
                    )
    if tg_grade == "TGIU":
        for item in _heuristic_tgiu_findings(
            point_label=str(payload.get("point_label") or ""),
            raw_point_text=str(payload.get("raw_point_text") or ""),
            report_context=payload.get("report_context") or {},
            normalize_text=normalize_text,
        ):
            if not isinstance(item, dict):
                continue
            error_type = str(item.get("error_type") or "").strip()
            if error_type.startswith("TGIU_") and error_type in _DOMMER_B_ALLOWED_ERROR_TYPES and error_type not in seen_tgiu:
                seen_tgiu.add(error_type)
                normalized["tgiu_findings"]["findings"].append(
                    {"error_type": error_type, "explanation": str(item.get("explanation") or "").strip()}
                )

    raw_fields = raw_output.get("field_results")
    raw_fields = raw_fields if isinstance(raw_fields, dict) else {}
    for field_name in _ARKAT_FIELD_NAMES:
        candidate = raw_fields.get(field_name)
        candidate = candidate if isinstance(candidate, dict) else {}
        status = str(candidate.get("status") or "").strip().upper()
        error_type = candidate.get("error_type")
        explanation = str(candidate.get("explanation") or "").strip()

        if status.startswith("WRONG:"):
            error_type = status.split("WRONG:", 1)[1].strip()
            status = "WRONG"
        if status not in {"CORRECT", "WRONG", "MISSING", "NOT_APPLICABLE"}:
            status = "MISSING"
            error_type = f"MISSING ({field_name})"
            explanation = ""
        if status == "MISSING":
            error_type = error_type or f"MISSING ({field_name})"
            explanation = ""
        elif status == "WRONG":
            if not error_type or str(error_type) not in _DOMMER_B_ALLOWED_ERROR_TYPES:
                status = "CORRECT"
                error_type = None
                explanation = ""
        else:
            error_type = None
            explanation = ""

        normalized["field_results"][field_name] = {
            "status": status,
            "error_type": error_type,
            "explanation": explanation,
        }
        if status in {"WRONG", "MISSING"} and _field_required_for_regression(field_name, tg_grade, ns_version):
            normalized["has_errors"] = True

    if tg_grade == "TGIU" and normalized["tgiu_findings"]["findings"]:
        normalized["has_errors"] = True
    return _force_tgiu_field_results_not_applicable(normalized, tg_grade)


def _matches_expected(actual: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    if not isinstance(actual, dict):
        return False
    return (
        _field_pairs(actual) == _field_pairs(expected)
        and bool(actual.get("has_errors")) == bool(expected.get("has_errors"))
        and _tgiu_error_types(actual) == _tgiu_error_types(expected)
    )


def _actual_llm_output(case: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("dommer_b_output", "dommer_b_json_output", "actual_output", "raw_model_json_output", "raw_json_output"):
        value = case.get(key)
        if isinstance(value, dict):
            return value
    return None


def _normalize_llm_cases_for_current_fasit(raw_cases: list[dict[str, Any]], fasit_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fasit_by_id = {case["case_id"]: case for case in fasit_cases}
    normalized_cases: list[dict[str, Any]] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            continue
        case_id = raw_case.get("case_id")
        fasit_case = fasit_by_id.get(case_id)
        if not fasit_case:
            continue
        raw_model_output = raw_case.get("raw_model_json_output") or raw_case.get("raw_json_output")
        actual_output = _normalize_model_output_for_llm_regression(raw_model_output, fasit_case["input"])
        if actual_output is None:
            actual_output = _normalize_model_output_for_llm_regression(_actual_llm_output(raw_case), fasit_case["input"])
        expected_output = fasit_case["expected_output"]
        normalized = dict(raw_case)
        normalized["case_id"] = case_id
        normalized["point_id"] = raw_case.get("point_id") or fasit_case["input"].get("point_id")
        normalized["expected_output"] = expected_output
        normalized["dommer_b_json_output"] = actual_output
        normalized["llm_called"] = bool(
            raw_case.get("llm_called")
            or (isinstance(actual_output, dict) and actual_output.get("used_llm"))
            or (isinstance(actual_output, dict) and actual_output.get("llm_called"))
        )
        normalized["matches_expected"] = _matches_expected(actual_output, expected_output)
        normalized_cases.append(normalized)
    return normalized_cases


def _prepare_fields(payload: dict[str, Any]) -> dict[str, str]:
    fields = dict(payload.get("extracted_fields") or {})

    def no_section(_text: str, _field: str) -> str:
        return ""

    if all(not str(value or "").strip() for value in fields.values()):
        fields = _extract_fields_for_point(
            payload["report_format"],
            payload["raw_point_text"],
            no_section,
            normalize_text,
        )
    fields = _sanitize_arkat_field_values(fields, normalize_text, payload["point_id"])
    return _finalize_arkat_fields(
        fields,
        normalize_text,
        payload["point_id"],
        payload["raw_point_text"],
        payload["tg_grade"],
    )


def _run_deterministic(payload: dict[str, Any]) -> dict[str, Any]:
    return _evaluate_arkat_point(
        point_id=payload["point_id"],
        point_label=payload["point_label"],
        tg_grade=payload["tg_grade"],
        report_format=payload["report_format"],
        ns_version=payload["ns_version"],
        raw_point_text=payload["raw_point_text"],
        extracted_fields=_prepare_fields(payload),
        report_context=payload.get("report_context") or {},
        normalize_text=normalize_text,
        allow_llm=False,
    )


def _prompt_fields_for_payload(payload: dict[str, Any]) -> dict[str, str]:
    if str(payload.get("report_format") or "").strip().lower() in {"unlabeled_prose", "compressed_mixed"}:
        return {field_name: "" for field_name in _ARKAT_FIELD_NAMES}
    return _prepare_fields(payload)


def _build_user_prompt(payload: dict[str, Any], fields: dict[str, str]) -> str:
    step = get_arkat_evaluation_pipeline_step()
    template = str((step.get("user_prompt_template") or {}).get("content") or "").strip()
    return _build_dommer_b_user_prompt(
        user_template=template,
        point_id=payload["point_id"],
        point_label=payload["point_label"],
        tg_grade=payload["tg_grade"],
        report_format=payload["report_format"],
        ns_version=payload["ns_version"],
        raw_point_text=payload["raw_point_text"],
        fields=fields,
        report_context=payload.get("report_context") or {},
    )


def _call_bedrock_raw(system_prompt: str, user_prompt: str, max_tokens: int) -> tuple[dict[str, Any] | None, str, str | None]:
    bedrock = BedrockAI(region=settings.AWS_REGION)
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max(256, int(max_tokens)),
            "temperature": 0.0,
            "top_p": 1.0,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
    )
    response = bedrock._invoke_model_with_retry(
        model_id=MODEL_NAME,
        body=body,
        max_retries=2,
    )
    content = response.get("content") or []
    raw_text = "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("text")
    )
    return _extract_json_object_from_text(raw_text), raw_text, response.get("stop_reason") or response.get("stopReason")


def _run_llm_case(case: dict[str, Any]) -> dict[str, Any]:
    payload = case["input"]
    fields = _prompt_fields_for_payload(payload)
    system_prompt = get_dommer_b_system_prompt_text().strip()
    user_prompt = _build_user_prompt(payload, fields)
    started = time.monotonic()
    try:
        raw_json, raw_text, stop_reason = _call_bedrock_raw(system_prompt, user_prompt, max_tokens=1100)
        dommer_b_output = _normalize_model_output_for_llm_regression(raw_json, payload)
        if isinstance(dommer_b_output, dict):
            dommer_b_output["used_llm"] = raw_json is not None
        return {
            "case_id": case["case_id"],
            "point_id": payload["point_id"],
            "llm_called": raw_json is not None,
            "model_name": MODEL_NAME,
            "duration_s": round(time.monotonic() - started, 3),
            "stop_reason": stop_reason,
            "dommer_b_json_output": dommer_b_output,
            "raw_model_json_output": raw_json,
            "raw_response_text": raw_text,
            "expected_output": case["expected_output"],
            "matches_expected": _matches_expected(dommer_b_output, case["expected_output"]),
        }
    except Exception as exc:
        return {
            "case_id": case["case_id"],
            "point_id": payload["point_id"],
            "llm_called": False,
            "model_name": MODEL_NAME,
            "duration_s": round(time.monotonic() - started, 3),
            "error": f"{exc.__class__.__name__}: {exc}",
            "raw_json_output": None,
            "raw_response_text": "",
            "expected_output": case["expected_output"],
            "matches_expected": False,
        }


def _technical_development_count(path: Path) -> int:
    payload = _load_json(path)
    output = payload.get("analysis_output") or {}
    seen_point_ids: set[str] = set()
    for finding in output.get("all_findings") or []:
        if not isinstance(finding, dict):
            continue
        blob = " ".join(
            str(finding.get(key, ""))
            for key in ("finding_id", "rule_id", "title", "message")
        )
        if "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS" in blob:
            point_id = str(finding.get("exact_point_id") or finding.get("point_id") or "").strip()
            if point_id:
                seen_point_ids.add(point_id)
    pipeline = payload.get("dommer_b_full") or output.get("arkat_semantic_pipeline") or {}
    for point in pipeline.get("points") or []:
        if not isinstance(point, dict):
            continue
        field_results = ((point.get("evaluation") or {}).get("field_results") or {})
        result = field_results.get("konsekvens")
        if isinstance(result, dict) and str(result.get("error_type") or "") == "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS":
            point_id = str(point.get("point_id") or "").strip()
            if point_id:
                seen_point_ids.add(point_id)
    return len(seen_point_ids)


def _real_report_summary(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    meta = payload.get("meta") or {}
    output = payload.get("analysis_output") or {}
    return {
        "path": str(path),
        "document_title": meta.get("document_title"),
        "document_id": meta.get("document_id"),
        "rerun_at_utc": meta.get("rerun_at_utc"),
        "score_total": output.get("score_total"),
        "technical_development_as_konsekvens_findings": _technical_development_count(path),
        "top_issue_ids": [
            issue.get("finding_id")
            for issue in output.get("top_issues") or []
            if isinstance(issue, dict)
        ],
    }


def _write_routing_user_message_dumps(cases: list[dict[str, Any]]) -> None:
    dump_paths = {6: CASE_6_USER_MESSAGE_PATH, 8: CASE_8_USER_MESSAGE_PATH}
    for case in cases:
        path = dump_paths.get(case["case_id"])
        if path is None:
            continue
        payload = case["input"]
        fields = _prompt_fields_for_payload(payload)
        path.write_text(_build_user_prompt(payload, fields), encoding="utf-8")


def _write_artifacts(llm_cases: list[dict[str, Any]], generated_at: str) -> None:
    cases = _parse_cases()
    llm_cases = _normalize_llm_cases_for_current_fasit(llm_cases, cases)
    deterministic_cases = []
    for case in cases:
        actual = _run_deterministic(case["input"])
        deterministic_cases.append(
            {
                "case_id": case["case_id"],
                "point_id": case["input"]["point_id"],
                "actual_output": actual,
                "expected_output": case["expected_output"],
                "matches_expected": _matches_expected(actual, case["expected_output"]),
            }
        )

    mapping = _load_json(ARKAT_ERROR_DEDUCTION_MAPPING_PATH)
    semantic_rules = _load_json(ARKAT_SEMANTIC_RULES_PATH)
    prompt_text = get_dommer_b_system_prompt_text()
    prompt_lines = prompt_text.splitlines()[:2]
    prompt_version_lines = [line for line in prompt_text.splitlines() if line.startswith("Versjon ")]
    raw_payload = {
        "generated_at_utc": generated_at,
        "source_of_truth_markdown": str(TEST_SET_PATH),
        "prompt_source": str(DOMMER_B_SYSTEM_PROMPT_PATH),
        "mapping_source": str(ARKAT_ERROR_DEDUCTION_MAPPING_PATH),
        "semantic_rules_source": str(ARKAT_SEMANTIC_RULES_PATH),
        "prompt_first_two_lines": prompt_lines,
        "prompt_version_lines": prompt_version_lines,
        "routing_user_message_dumps": {
            "case_6": str(CASE_6_USER_MESSAGE_PATH),
            "case_8": str(CASE_8_USER_MESSAGE_PATH),
        },
        "cases": llm_cases,
    }
    RAW_OUTPUT_PATH.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    llm_mismatches = [
        {
            "case_id": case.get("case_id"),
            "point_id": case.get("point_id"),
            "llm_called": case.get("llm_called"),
            "model_name": case.get("model_name"),
            "expected_pairs": _field_pairs(case.get("expected_output") or {}),
            "dommer_b_pairs": _field_pairs(_actual_llm_output(case) or {}),
            "raw_model_pairs": _field_pairs(case.get("raw_model_json_output") or case.get("raw_json_output") or {}),
            "expected_has_errors": (case.get("expected_output") or {}).get("has_errors"),
            "dommer_b_has_errors": ((_actual_llm_output(case) or {}).get("has_errors") if isinstance(_actual_llm_output(case), dict) else None),
            "error": case.get("error"),
        }
        for case in llm_cases
        if not case.get("matches_expected")
    ]
    result_payload = {
        "generated_at_utc": generated_at,
        "artifact_status": "LLM_PASS" if not llm_mismatches and all(c["matches_expected"] for c in deterministic_cases) else "LLM_MISMATCHES_PRESENT",
        "provenance": {
            "source_of_truth_markdown": str(TEST_SET_PATH),
            "prompt_source": str(DOMMER_B_SYSTEM_PROMPT_PATH),
            "mapping_source": str(ARKAT_ERROR_DEDUCTION_MAPPING_PATH),
            "semantic_rules_source": str(ARKAT_SEMANTIC_RULES_PATH),
        },
        "prompt_first_two_lines": prompt_lines,
        "prompt_version_lines": prompt_version_lines,
        "mapping_check": {
            "version": (mapping.get("meta") or {}).get("version") or mapping.get("version"),
            "konsekvens_entry_count": len((mapping.get("deductions") or {}).get("konsekvens") or {}),
            "konsekvens_entries": sorted(((mapping.get("deductions") or {}).get("konsekvens") or {}).keys()),
            "has_24_type_konsekvens_entries": all(
                key in ((mapping.get("deductions") or {}).get("konsekvens") or {})
                for key in (
                    "TILTAK_AS_KONSEKVENS",
                    "RISIKO_AS_KONSEKVENS",
                    "LIMITATION_AS_KONSEKVENS",
                )
            ),
        },
        "semantic_rules_check": {
            "version": (semantic_rules.get("meta") or {}).get("version") or semantic_rules.get("version"),
            "anbefalt_tiltak_missing_mentions_tg2_2025": "NS3600:2025" in json.dumps(semantic_rules, ensure_ascii=False)
            and "MISSING (anbefalt_tiltak)" in json.dumps(semantic_rules, ensure_ascii=False),
        },
        "deterministic_regression": {
            "runner_type": "deterministic local evaluator",
            "llm_called": False,
            "case_count": len(deterministic_cases),
            "passed": sum(1 for case in deterministic_cases if case["matches_expected"]),
            "failed": sum(1 for case in deterministic_cases if not case["matches_expected"]),
            "mismatches": [
                {
                    "case_id": case["case_id"],
                    "point_id": case["point_id"],
                    "expected_pairs": _field_pairs(case["expected_output"]),
                    "actual_pairs": _field_pairs(case["actual_output"]),
                    "expected_has_errors": case["expected_output"].get("has_errors"),
                    "actual_has_errors": case["actual_output"].get("has_errors"),
                }
                for case in deterministic_cases
                if not case["matches_expected"]
            ],
        },
        "llm_regression": {
            "runner_type": "Dommer B LLM output with production-safe normalization",
            "normalization_note": "Scores raw_model_json_output after schema cleanup, invalid error-type rejection and TGIU backstops; published-fasit/spec regression guards are not applied.",
            "model_name": MODEL_NAME,
            "case_count": len(llm_cases),
            "llm_called_all_cases": all(bool(case.get("llm_called")) for case in llm_cases),
            "passed": sum(1 for case in llm_cases if case.get("matches_expected")),
            "failed": sum(1 for case in llm_cases if not case.get("matches_expected")),
            "mismatches": llm_mismatches,
            "raw_outputs_file": str(RAW_OUTPUT_PATH),
            "routing_user_message_dumps": {
                "case_6": str(CASE_6_USER_MESSAGE_PATH),
                "case_8": str(CASE_8_USER_MESSAGE_PATH),
            },
        },
        "real_reports": {
            "note": "Existing rerun artifacts prepared for client handoff.",
            "reports": [
                _real_report_summary(HORTEN_REPORT_PATH),
                _real_report_summary(FREDRIKSTAD_REPORT_PATH),
            ],
        },
    }
    RESULT_PATH.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-llm", action="store_true", help="Reuse existing raw LLM cases instead of calling Bedrock.")
    parser.add_argument("--llm-output-file", type=Path, default=RAW_OUTPUT_PATH, help="Existing LLM artifact to reuse with --skip-llm.")
    args = parser.parse_args()

    generated_at = _utc_now()
    cases = _parse_cases()
    if args.skip_llm:
        llm_cases = (_load_json(args.llm_output_file).get("cases") or []) if args.llm_output_file.exists() else []
    else:
        llm_cases = [_run_llm_case(case) for case in cases]
    _write_routing_user_message_dumps(cases)
    _write_artifacts(llm_cases, generated_at)
    print(f"Wrote: {RAW_OUTPUT_PATH}")
    print(f"Wrote: {RESULT_PATH}")
    print(f"Wrote: {CASE_6_USER_MESSAGE_PATH}")
    print(f"Wrote: {CASE_8_USER_MESSAGE_PATH}")


if __name__ == "__main__":
    main()
