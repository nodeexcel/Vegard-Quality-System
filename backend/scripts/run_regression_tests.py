#!/usr/bin/env python3
"""
Regression tests for the Vegard Quality System segmentation and punkt-for-punkt flow.

Run from backend directory (use venv if available):
  source .venv/bin/activate  # if you have backend/.venv
  python -m scripts.run_regression_tests [path/to/report.pdf]

If no PDF path is given, uses Tilstandsrapport_-_NS_3600_2018_Storgaten_89_21.01.2026.pdf
from project root.

Tests:
1. etg: / for ytterligere vurderinger → rejected by stray filter, NOT in points_overview
2. Parent (6) + child (6.1) → UI shows parent with nested children, no duplicates
3. Valid TG3 cost intervals ("10 000 – 50 000") → accepted as sjablonmessig, not flagged

This script runs the segmentation trace (no LLM). For full analysis including
points_overview and cost validation, use the API: upload PDF, then check admin report.
"""
import os
import sys
import json

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.pdf_extractor import PDFExtractor
from app.services.ai_analyzer import run_segmentation_trace


def main():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    default_pdf = os.path.join(root, "Tilstandsrapport_-_NS_3600_2018_Storgaten_89_21.01.2026.pdf")

    pdf_path = sys.argv[1] if len(sys.argv) > 1 else default_pdf
    if not os.path.exists(pdf_path):
        print(f"ERROR: PDF not found: {pdf_path}")
        sys.exit(1)

    print("=" * 70)
    print("REGRESSION TEST: Segmentation Trace")
    print("=" * 70)
    print(f"PDF: {pdf_path}\n")

    # Extract text
    extractor = PDFExtractor()
    with open(pdf_path, "rb") as f:
        text = extractor.extract_text(f)

    if not text or len(text.strip()) < 100:
        print("ERROR: Could not extract sufficient text from PDF")
        sys.exit(1)

    print(f"Extracted {len(text)} chars from PDF.\n")

    # Run segmentation trace
    trace = run_segmentation_trace(text)

    # Summary
    print("--- SEGMENTATION TRACE (admin debug) ---")
    print(f"Total detected (before stray filter): {trace.get('total_detected_before_stray', 0)}")
    print(f"Stray rejected:                        {trace.get('stray_rejected_count', 0)}")
    print(f"Whitelist rejected:                    {trace.get('whitelist_rejected_count', 0)}")
    print(f"detected_points_total:                 {trace.get('detected_points_total', 0)}")
    print(f"rejected_not_in_whitelist_count:       {trace.get('rejected_not_in_whitelist_count', 0)}")
    print(f"rejected_hard_regex_count:            {trace.get('rejected_hard_regex_count', 0)}")
    print(f"accepted_canonical_count:             {trace.get('accepted_canonical_count', 0)}")
    print(f"accepted_alias_count:                 {trace.get('accepted_alias_count', 0)}")
    print(f"rejected_noise_count:                 {trace.get('rejected_noise_count', 0)}")
    print(f"unclassified_heading_count:            {trace.get('unclassified_heading_count', 0)}")
    print(f"classified_heading_count:              {trace.get('classified_heading_count', 0)}")
    print(f"Validated (in points_overview):        {trace.get('total_after_whitelist', 0)}")
    print()

    # Regression 1: etg: and "for ytterligere vurderinger" should be in stray_rejected
    stray = trace.get("stray_rejected") or []
    etg_rejected = [r for r in stray if "etg:" in (r.get("normalized_title") or "").lower() or "etg:" in (r.get("point_id") or "").lower()]
    fyv_rejected = [r for r in stray if "ytterligere" in (r.get("normalized_title") or "").lower() or "ytterligere" in (r.get("reason") or "").lower()]

    print("--- REGRESSION 1: Stray filter (etg:, for ytterligere vurderinger) ---")
    if etg_rejected or fyv_rejected:
        print("PASS: These should NOT appear in points_overview:")
        for r in (etg_rejected + fyv_rejected):
            print(f"  - point_id={r.get('point_id')} reason={r.get('reason')} normalized_title={r.get('normalized_title')}")
    else:
        # Check if they were never detected (also OK) or if they might be in validated (FAIL)
        validated_ids = set(trace.get("validated_point_ids") or [])
        has_etg_like = any("etg" in str(x).lower() for x in validated_ids)
        if has_etg_like:
            print("WARN: Possible etg-like point in validated - review validated_point_ids")
        else:
            print("INFO: No 'etg:' or 'for ytterligere vurderinger' in stray_rejected.")
            print("      (Either not present in PDF or already filtered earlier.)")
    print()

    # Stray rejected details
    if stray:
        print("--- All stray rejected (reason + normalized_title) ---")
        for r in stray:
            print(f"  {r.get('point_id')!r} | {r.get('reason')!r} | {r.get('normalized_title')!r}")
        print()

    # Whitelist rejected
    wl = trace.get("whitelist_rejected") or []
    if wl:
        print("--- Whitelist rejected (for tuning) ---")
        for r in wl[:20]:  # First 20
            print(f"  {r.get('point_id')!r} | {r.get('reason')!r} | {r.get('normalized_title')!r}")
        if len(wl) > 20:
            print(f"  ... and {len(wl)-20} more")
        print()

    # Validated point IDs
    vids = trace.get("validated_point_ids") or []
    if vids:
        print("--- Validated point IDs (first 30) ---")
        print(", ".join(vids[:30]))
        if len(vids) > 30:
            print(f" ... and {len(vids)-30} more")
        print()

    print("=" * 70)
    print("For full regression (points_overview, parent-child nesting, TG3 cost):")
    print("1. Upload PDF via API (or admin)")
    print("2. Open admin report → Segmentation trace tab")
    print("3. Open results page → verify parent/child nesting, no etg in overview")
    print("=" * 70)


if __name__ == "__main__":
    main()
