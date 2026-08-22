#!/usr/bin/env python3
"""Build the isolated, hash-pinned A3/A4 candidate manifest reproducibly."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ACTIVE = ROOT / "files/MANIFEST.json"
OUTPUT = ROOT / "files/candidates/a3_a4_v2/MANIFEST.a3_a4_candidate.json"

CANDIDATE_ASSETS = (
    "candidates/a3_a4_v2/validert_phase_a_methodology_rules_v1_0.json",
    "candidates/a3_a4_v2/validert_phase_a_semantic_admission_rules_v1_0.json",
    "candidates/a3_a4_v2/validert_punkt_for_punkt_scoring_hooks_phase_a_v1_0.json",
    "candidates/a3_a4_v2/arkat_error_to_deduction_mapping_phase_a_v1_0.json",
    "candidates/a3_a4_v2/rag_scoring_model_validert_phase_a_v1_0.json",
    "candidates/a3_a4_v2/validert_governed_regime_decision_v2.0_approved.md",
    "candidates/a3_a4_v2/validert_a4_acceptance_specification_v2.0_frozen.md",
    "candidates/a3_a4_v2/validert_a4_approved_variances_v2_1.json",
    "candidates/a3_a4_v2/validert_a4_approved_decisions_v2_2.json",
    "candidates/a3_a4_v2/APPROVAL_RECORD.txt",
)

RUNTIME_FILES = (
    "backend/app/services/phase_a_contracts.py",
    "backend/app/services/phase_a_source_inventory.py",
    "backend/app/services/phase_a_document_understanding.py",
    "backend/app/services/phase_a_applicability.py",
    "backend/app/services/phase_a_regime.py",
    "backend/app/services/phase_a_governed_retrieval.py",
    "backend/app/services/phase_a_assessment.py",
    "backend/app/services/phase_a_scoring.py",
    "backend/app/services/phase_a_projection.py",
    "backend/app/services/arkat_semantic_pipeline.py",
    "backend/scripts/run_phase_a_understanding.py",
    "backend/scripts/run_phase_a_shadow_analysis.py",
    "backend/scripts/verify_phase_a_frozen_acceptance.py",
    "backend/scripts/build_phase_a_candidate_manifest.py",
    "frontend/app/results/[id]/page.tsx",
)

TEST_FILES = (
    "tests/test_phase_a_candidate_scoring_projection.py",
    "tests/test_phase_a_source_inventory_safety.py",
    "tests/test_phase_a3_a4_governed_pipeline.py",
    "tests/test_dommer_b_regression.py",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entries(paths: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"path": path, "sha256": digest(ROOT / path)} for path in paths]


def governed_entries(paths: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"path": path, "sha256": digest(ROOT / "files" / path)} for path in paths]


def main() -> int:
    active = json.loads(ACTIVE.read_text(encoding="utf-8"))
    payload = {
        "version": "a3-a4-v2-candidate-implementation-1",
        "generate_on_packaging": False,
        "notes": "Hash-pinned shadow candidate; active signed v46 manifest is referenced and never mutated.",
        "files": [*active["files"], *governed_entries(CANDIDATE_ASSETS)],
        "runtime_code_files": entries(RUNTIME_FILES),
        "test_files": entries(TEST_FILES),
        "shadow_only": True,
        "customer_publication_authorized": False,
        "baseline": {
            "commit": "cac28d8badddc6bbaf125fe7f152f4870da85eea",
            "manifest_path": "files/MANIFEST.json",
            "manifest_sha256": digest(ACTIVE),
            "immutable": True,
        },
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{digest(OUTPUT)}  {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
