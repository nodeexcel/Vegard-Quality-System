"""Run full local postprocess pipeline on bolavi-egen-rapport.pdf.

Output shape matches the confirmed previous run (dommer_b_bolavi-egen-rapport_fresh (1).json):
  meta
  dommer_b_full            -> analysis_output["arkat_semantic_pipeline"]
  analysis_output          -> full ARKAT analysis (policy_invariants here, NOT in feedback_v11)
  detected_points_payload  -> clean 12-point validated payload
  scoring_result_payload   -> run_meta + analysis_output + feedback_v11
"""
import hashlib
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

from app.services.ai_analyzer import (  # noqa: E402
    postprocess_analysis_output,
    build_feedback_v11,
    _mark_incomplete_fallback_output,
    get_validated_detected_points_payload,
)
from app.services.pdf_extractor import PDFExtractor  # noqa: E402
from app.services.validert_files import get_runtime_manifest  # noqa: E402


def main() -> None:
    pdf_path = ROOT / "files" / "bolavi-egen-rapport.pdf"
    output_path = ROOT / "files" / "dommer_b_bolavi-egen-rapport_fresh.json"

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    print(f"Extracting text from: {pdf_path}")
    extracted_text = PDFExtractor.extract_text(str(pdf_path))
    pdf_metadata = PDFExtractor.get_pdf_metadata(str(pdf_path))
    document_hash = "sha256:" + hashlib.sha256(extracted_text.encode("utf-8")).hexdigest()
    print(f"  {len(extracted_text)} chars | hash={document_hash[:30]}...")

    # Skeleton analysis_output in fallback mode
    analysis_output: dict = {
        "analysis_mode": "local_postprocess_dommer_b_fallback",
        "analysis_complete": False,
        "score_valid": False,
        "ui_status": "incomplete_analysis",
        "incomplete_reason": "full_analyzer_not_run_local_fallback",
        "incomplete_full_analyzer_reasons": ["full_analyzer_not_run_local_fallback"],
        "meta": {
            "document_title": pdf_path.name,
            "document_id": "1815",
            "analysis_mode": "local_postprocess_dommer_b_fallback",
            "analysis_complete": False,
            "score_valid": False,
            "incomplete_reason": "full_analyzer_not_run_local_fallback",
            "incomplete_full_analyzer_reasons": ["full_analyzer_not_run_local_fallback"],
        },
        "all_findings": [],
        "top_issues": [],
        "top_score_drivers": [],
        "score_drivers": [],
        "feedback_findings": [],
    }

    # Run full local ARKAT postprocessing on the raw PDF text (no LLM needed)
    print("Running postprocess_analysis_output (ARKAT analysis on PDF text)...")
    analysis_output = postprocess_analysis_output(analysis_output, extracted_text)
    print(f"  all_findings: {len(analysis_output.get('all_findings', []))}")

    # Get clean validated detected_points_payload from PDF text
    print("Getting validated detected_points_payload...")
    detected_points_payload = get_validated_detected_points_payload(
        extracted_text,
        document_hash=document_hash,
        document_title=pdf_path.name,
        document_id="1815",
        pdf_metadata=pdf_metadata,
    )
    print(f"  Detected points: {len(detected_points_payload.get('points', []))}")

    # Apply fallback mode markers and inject fresh v37 manifest
    _mark_incomplete_fallback_output(analysis_output)
    runtime_manifest = get_runtime_manifest("local_postprocess_dommer_b_fallback")
    analysis_output["runtime_manifest"] = runtime_manifest

    # Generate fresh feedback_v11 (policy_invariants moved OUT per client fix)
    print("Building feedback_v11...")
    feedback = build_feedback_v11(
        analysis_output=analysis_output,
        detected_points_payload=detected_points_payload,
        report_id="1815",
        document_hash=document_hash,
        report_text=extracted_text,
    )

    if "policy_invariants" in feedback:
        print("WARNING: policy_invariants still in feedback_v11 — check _apply_incomplete_feedback_policy")
    else:
        print("OK: policy_invariants NOT in feedback_v11 (fix confirmed)")

    invariants = analysis_output.get("policy_invariants", [])
    if invariants:
        passed = [i["id"] for i in invariants if i.get("passed")]
        failed = [i["id"] for i in invariants if not i.get("passed")]
        print(f"\nPolicy invariants ({len(invariants)} total):")
        print(f"  Passed ({len(passed)}): {', '.join(passed)}")
        if failed:
            print(f"  FAILED ({len(failed)}): {', '.join(failed)}")
        else:
            print("  All invariants PASSED")

    run_meta = {
        "run_id": f"local-bolavi-fresh-fallback-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "document_hash": document_hash,
        "source_filename": pdf_path.name,
        "analysis_mode": "local_postprocess_dommer_b_fallback",
        "runtime_manifest": runtime_manifest,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Same 5-key shape as confirmed (1).json
    output = {
        "meta": {
            "document_title": pdf_path.name,
            "document_id": "1815",
            "analysis_mode": "local_postprocess_dommer_b_fallback",
            "analysis_complete": False,
            "score_valid": False,
            "ui_status": "incomplete_analysis",
            "incomplete_reason": "full_analyzer_not_run_local_fallback",
            "overall_score": None,
            "runtime_manifest": runtime_manifest,
        },
        "dommer_b_full": analysis_output.get("arkat_semantic_pipeline", {}),
        "analysis_output": analysis_output,
        "detected_points_payload": detected_points_payload,
        "scoring_result_payload": {
            "run_meta": run_meta,
            "analysis_output": analysis_output,
            "feedback_v11": feedback,
        },
    }

    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWritten to: {output_path}")

    findings = feedback.get("findings", [])
    overview = feedback.get("points_overview", [])
    print(f"Findings: {len(findings)}, Points overview: {len(overview)}")
    print(f"runtime_manifest.pipeline_version: {runtime_manifest.get('pipeline_version')}")


if __name__ == "__main__":
    main()
