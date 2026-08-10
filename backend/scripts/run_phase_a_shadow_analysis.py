#!/usr/bin/env python3
"""Create an internal A3/A4 trace from an A2 artifact.

The active resolver intentionally remains pending, so this command retrieves
manifest-verified candidate material and records abstentions without invoking AI
or producing customer findings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DATABASE_URL", "sqlite:///tmp.db")
os.environ.setdefault("OPENAI_API_KEY", "shadow-not-used")
os.environ.setdefault("SECRET_KEY", "phase-a-shadow-only")

from app.services.phase_a_assessment import BedrockSemanticAssessmentModel, PhaseA4ShadowService
from app.services.phase_a_contracts import DocumentUnderstandingResult, RuleCategory
from app.services.phase_a_governed_retrieval import ManifestGovernedCatalog, ManifestVerifiedRuleRetriever
from app.services.phase_a_regime import ApprovedGovernedRegimeResolver
from app.services.phase_a_projection import serialize_for_customer_result_component


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("a2_artifact", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--assets-root", type=Path, default=ROOT / "files")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--customer-envelope", type=Path)
    args = parser.parse_args()

    understanding = DocumentUnderstandingResult.model_validate_json(
        args.a2_artifact.read_text(encoding="utf-8")
    )
    actual_manifest_sha256 = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    if actual_manifest_sha256 != args.manifest_sha256:
        parser.error("candidate manifest SHA-256 does not match --manifest-sha256")
    catalog = ManifestGovernedCatalog(
        args.assets_root, args.manifest, approved_manifest_sha256=args.manifest_sha256
    )
    category_assets = {
        RuleCategory.AARSAK: ("arkat_semantic_rules_v1_2_3.json", "arkat_error_to_deduction_mapping_v1_1_2.json", "rag_scoring_model_validert_v1.6.15.json"),
        RuleCategory.RISIKO: (
            "arkat_semantic_rules_v1_2_3.json",
            "arkat_error_to_deduction_mapping_v1_1_2.json",
            "rag_scoring_model_validert_v1.6.15.json",
            "candidates/a3_a4_v2/validert_phase_a_semantic_admission_rules_v1_0.json",
        ),
        RuleCategory.KONSEKVENS: ("arkat_semantic_rules_v1_2_3.json", "arkat_error_to_deduction_mapping_v1_1_2.json", "rag_scoring_model_validert_v1.6.15.json"),
        RuleCategory.ANBEFALT_TILTAK: (
            "arkat_semantic_rules_v1_2_3.json",
            "arkat_error_to_deduction_mapping_v1_1_2.json",
            "candidates/a3_a4_v2/validert_phase_a_methodology_rules_v1_0.json",
        ),
        RuleCategory.METHODOLOGY: (
            "arkat_semantic_rules_v1_2_3.json",
            "rag_scoring_model_validert_v1.6.15.json",
            "candidates/a3_a4_v2/validert_phase_a_methodology_rules_v1_0.json",
        ),
        RuleCategory.LEGALITY: ("validert_legal_compliance_rules_v1_1.json",),
        RuleCategory.TG3_COST: (
            "candidates/a3_a4_v2/validert_phase_a_methodology_rules_v1_0.json",
            "rag_scoring_model_validert_v1.6.15.json",
        ),
    }
    retriever = ManifestVerifiedRuleRetriever(
        catalog, resolver=ApprovedGovernedRegimeResolver(), category_assets=category_assets
    )
    service = PhaseA4ShadowService(retriever, BedrockSemanticAssessmentModel())
    result = service.analyze(understanding, list(RuleCategory))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.customer_envelope:
        envelope = serialize_for_customer_result_component(
            result.production_compatible_public_payload,
            result.score_result,
            report_id=0,
            filename=understanding.source_filename,
        )
        args.customer_envelope.parent.mkdir(parents=True, exist_ok=True)
        args.customer_envelope.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
