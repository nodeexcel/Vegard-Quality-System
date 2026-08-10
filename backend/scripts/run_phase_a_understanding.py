"""Internal-only runner for the disabled/shadow A1/A2 document understanding path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///tmp.db")
os.environ.setdefault("OPENAI_API_KEY", "shadow-not-used")
os.environ.setdefault("SECRET_KEY", "phase-a-shadow-only")

from app.services.pdf_extractor import PDFExtractor
from app.services.phase_a_document_understanding import (
    BedrockDocumentCandidateExtractor,
    DocumentUnderstandingService,
)
from app.services.phase_a_pipeline import PhaseAFeaturePolicy, PhaseAPipeline


class ReplayCandidateExtractor:
    """Replay captured candidate batches to isolate deterministic reconciliation changes."""

    def __init__(self, artifact_path: Path):
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.batches = list(payload.get("candidate_batches") or [])
        self.metadata = list(payload.get("model_metadata") or [])

    def extract_candidates(self, *, batch_index: int, **_kwargs):
        if batch_index < 1 or batch_index > len(self.batches):
            raise ValueError(f"Replay artifact has no candidate batch {batch_index}")
        metadata = self.metadata[batch_index - 1] if batch_index <= len(self.metadata) else {}
        return self.batches[batch_index - 1], {"replayed": True, **metadata}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-replay", type=Path)
    parser.add_argument(
        "--verified-cover-tg-count",
        action="append",
        default=[],
        metavar="TG=N",
        help="Traceable externally verified cover count, e.g. TG1=11",
    )
    args = parser.parse_args()

    if not args.pdf.is_file():
        parser.error(f"PDF does not exist: {args.pdf}")
    if args.pdf.suffix.lower() != ".pdf":
        parser.error("Input must be a PDF")

    source_pdf_sha256 = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    report_text = PDFExtractor.extract_text(str(args.pdf))
    extractor = (
        ReplayCandidateExtractor(args.candidate_replay)
        if args.candidate_replay
        else BedrockDocumentCandidateExtractor()
    )
    cover_counts = {}
    for item in args.verified_cover_tg_count:
        try:
            tg, count = item.split("=", 1)
            cover_counts[tg.strip().upper()] = int(count)
        except (ValueError, TypeError):
            parser.error(f"Invalid --verified-cover-tg-count: {item}")
    service = DocumentUnderstandingService(extractor, verified_cover_tg_counts=cover_counts)
    pipeline = PhaseAPipeline(service, PhaseAFeaturePolicy(enabled=True, shadow_only=True))
    result = pipeline.understand_document(
        report_text,
        args.pdf.name,
        source_pdf_sha256=source_pdf_sha256,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
