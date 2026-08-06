#!/usr/bin/env python3
"""Create an internal A3/A4 trace from an A2 artifact.

The active resolver intentionally remains pending, so this command retrieves
manifest-verified candidate material and records abstentions without invoking AI
or producing customer findings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.phase_a_assessment import PhaseA4ShadowService
from app.services.phase_a_contracts import DocumentUnderstandingResult, RuleCategory
from app.services.phase_a_governed_retrieval import ManifestGovernedCatalog, ManifestVerifiedRuleRetriever


class _BlockedAssessmentModel:
    def assess(self, *_args, **_kwargs):
        raise RuntimeError("assessment invocation is blocked until governed regime resolution is authorized")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("a2_artifact", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--assets-root", type=Path, default=ROOT / "files")
    parser.add_argument("--manifest", type=Path, default=ROOT / "files" / "MANIFEST.json")
    args = parser.parse_args()

    understanding = DocumentUnderstandingResult.model_validate_json(
        args.a2_artifact.read_text(encoding="utf-8")
    )
    catalog = ManifestGovernedCatalog(args.assets_root, args.manifest)
    retriever = ManifestVerifiedRuleRetriever(catalog)
    service = PhaseA4ShadowService(retriever, _BlockedAssessmentModel())
    result = service.analyze(understanding, list(RuleCategory))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
