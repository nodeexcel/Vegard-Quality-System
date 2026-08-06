"""Manifest-verified governed retrieval for Phase A3.

This module never decides which legal/standards regime applies. It only returns
candidate rule material from byte-verified assets in the active manifest.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from app.services.phase_a_contracts import (
    Abstention,
    GovernedAssetVerification,
    RegimeResolutionStatus,
    RuleApplicability,
    RuleCategory,
    RuleRetrievalRecord,
    RuleRetrievalResult,
    TraceRecord,
    ValidatedDocumentFact,
    ValidatedSegment,
)
from app.services.phase_a_regime import PendingGovernedRegimeResolver


class GovernedAssetError(RuntimeError):
    """The governed source cannot be proven against the active manifest."""


APPROVED_V46_MANIFEST_SHA256 = "310f2377501024ecc32646a6adad3175414f6dbdfa0b3ecd156bd4d47bc2d8a1"


class RegimeResolver(Protocol):
    def resolve(self, rule_category: RuleCategory, facts: Iterable[ValidatedDocumentFact]): ...


# This is technical source routing, not a decision about which edition/regime is
# applicable. Applicability remains the responsibility of RegimeResolver.
CATEGORY_ASSETS: Mapping[RuleCategory, tuple[str, ...]] = {
    RuleCategory.AARSAK: ("arkat_semantic_rules_v1_2_3.json",),
    RuleCategory.RISIKO: ("arkat_semantic_rules_v1_2_3.json",),
    RuleCategory.KONSEKVENS: ("arkat_semantic_rules_v1_2_3.json",),
    RuleCategory.ANBEFALT_TILTAK: ("arkat_semantic_rules_v1_2_3.json",),
    RuleCategory.METHODOLOGY: (
        "arkat_semantic_rules_v1_2_3.json",
        "validert_orchestrator_pipeline_v2.1.json",
    ),
    RuleCategory.LEGALITY: ("validert_legal_compliance_rules_v1_1.json",),
    RuleCategory.TG3_COST: (
        "rag_scoring_model_validert_v1.6.15.json",
    ),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{_sha256('|'.join(parts).encode())[:24]}"


def _pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return {token for token in re.findall(r"[a-z0-9_]{3,}", normalized) if token not in {"skal", "eller", "ikke", "med", "for", "som"}}


@dataclass(frozen=True)
class _Chunk:
    rule_id: str
    pointer: str
    content: dict[str, Any]
    searchable_text: str


class ManifestGovernedCatalog:
    def __init__(
        self,
        assets_root: Path,
        manifest_path: Path,
        approved_manifest_sha256: str = APPROVED_V46_MANIFEST_SHA256,
    ):
        self.assets_root = assets_root.resolve()
        self.manifest_path = manifest_path.resolve()
        manifest_raw = self.manifest_path.read_bytes()
        self.manifest_sha256 = _sha256(manifest_raw)
        if self.manifest_sha256 != approved_manifest_sha256:
            raise GovernedAssetError(
                "active manifest is not the independently pinned approved v46 manifest"
            )
        manifest = json.loads(manifest_raw)
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise GovernedAssetError("active manifest has no files list")
        self._manifest_hashes = {
            item["path"]: item["sha256"]
            for item in entries
            if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str)
        }

    def load(self, asset_path: str) -> tuple[Any, GovernedAssetVerification]:
        expected = self._manifest_hashes.get(asset_path)
        if not expected:
            raise GovernedAssetError(f"asset is not declared by active manifest: {asset_path}")
        target = (self.assets_root / asset_path).resolve()
        if target.parent != self.assets_root:
            raise GovernedAssetError(f"asset path escapes governed root: {asset_path}")
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise GovernedAssetError(f"governed asset is unavailable: {asset_path}") from exc
        actual = _sha256(raw)
        verification = GovernedAssetVerification(
            asset_path=asset_path,
            manifest_sha256=expected,
            actual_sha256=actual,
            verified=actual == expected,
        )
        if actual != expected:
            raise GovernedAssetError(f"governed asset hash mismatch: {asset_path}")
        try:
            return json.loads(raw), verification
        except json.JSONDecodeError as exc:
            raise GovernedAssetError(f"governed asset is not valid JSON: {asset_path}") from exc

    def chunks(self, asset_path: str, document: Any) -> list[_Chunk]:
        chunks: list[_Chunk] = []

        def walk(value: Any, pointer: str) -> None:
            if isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{pointer}/{index}")
                return
            if not isinstance(value, dict):
                return
            scalar_keys = {key for key, child in value.items() if not isinstance(child, (dict, list))}
            explicit_id = next((value.get(key) for key in ("rule_id", "id", "error_type") if isinstance(value.get(key), str)), None)
            # A rule-like object either has an explicit identity or contains enough
            # substantive scalar/list content to be independently cited.
            substantive = explicit_id is not None or len(scalar_keys) >= 2 or any(isinstance(child, list) for child in value.values())
            if substantive and pointer:
                rule_id = explicit_id or f"{asset_path}#{pointer}"
                chunks.append(_Chunk(rule_id, pointer, value, json.dumps(value, ensure_ascii=False, sort_keys=True)))
            for key, child in value.items():
                if isinstance(child, (dict, list)):
                    walk(child, f"{pointer}/{_pointer_escape(str(key))}")

        walk(document, "")
        return chunks


class ManifestVerifiedRuleRetriever:
    def __init__(
        self,
        catalog: ManifestGovernedCatalog,
        resolver: RegimeResolver | None = None,
        category_assets: Mapping[RuleCategory, tuple[str, ...]] = CATEGORY_ASSETS,
    ):
        self.catalog = catalog
        self.resolver = resolver or PendingGovernedRegimeResolver()
        self.category_assets = category_assets

    def retrieve(
        self,
        segment: ValidatedSegment,
        category: RuleCategory,
        facts: Iterable[ValidatedDocumentFact],
        *,
        document_hash: str,
        top_k: int = 5,
    ) -> RuleRetrievalResult:
        facts = list(facts)
        resolution = self.resolver.resolve(category, facts)
        query = " ".join(filter(None, (segment.title, segment.professional_subject, segment.point_label, segment.tg_grade, category.value)))
        query_tokens = _tokens(query)
        scored: list[tuple[float, str, _Chunk, GovernedAssetVerification]] = []
        verifications: list[GovernedAssetVerification] = []
        for asset_path in self.category_assets.get(category, ()):
            document, verification = self.catalog.load(asset_path)
            verifications.append(verification)
            for chunk in self.catalog.chunks(asset_path, document):
                chunk_tokens = _tokens(chunk.searchable_text)
                overlap = len(query_tokens & chunk_tokens)
                score = overlap / max(1, len(query_tokens))
                exact_tg3_rule = category == RuleCategory.TG3_COST and chunk.rule_id in {
                    "E_METHOD.tg3_cost_missing", "E_METHOD.tg3_cost_single_amount_only"
                }
                if overlap or category.value in chunk.searchable_text.casefold() or exact_tg3_rule:
                    if exact_tg3_rule:
                        score = 1.0
                    scored.append((score, asset_path, chunk, verification))
        scored.sort(key=lambda item: (-item[0], item[1], item[2].pointer))
        applicability = (
            RuleApplicability.REGIME_RESOLVED
            if resolution.status == RegimeResolutionStatus.RESOLVED
            else RuleApplicability.CANDIDATE_ONLY
        )
        records: list[RuleRetrievalRecord] = []
        traces: list[TraceRecord] = []
        for score, asset_path, chunk, verification in scored[:top_k]:
            resolve_rule = getattr(self.resolver, "resolve_rule", None)
            rule_resolution = (
                resolve_rule(category, chunk.rule_id, chunk.content, facts)
                if callable(resolve_rule)
                else resolution
            )
            retrieval_id = _stable_id("ret", segment.segment_id, category.value, asset_path, chunk.pointer)
            record = RuleRetrievalRecord(
                retrieval_id=retrieval_id,
                segment_id=segment.segment_id,
                rule_category=category,
                asset_path=asset_path,
                asset_sha256=verification.actual_sha256,
                rule_id=chunk.rule_id,
                json_pointer=chunk.pointer,
                content_sha256=_sha256(_canonical_bytes(chunk.content)),
                content=chunk.content,
                relevance_score=min(1.0, score),
                applicability=(
                    RuleApplicability.REGIME_RESOLVED
                    if rule_resolution.status == RegimeResolutionStatus.RESOLVED
                    else RuleApplicability.CANDIDATE_ONLY
                ),
                regime_status=rule_resolution.status,
                regime_id=rule_resolution.regime_id,
                controlling_fact_ids=rule_resolution.controlling_fact_ids,
                regime_explanation=rule_resolution.explanation,
                retrieval_reason=(
                    "Deterministic token-overlap candidate retrieval from an active-manifest asset; "
                    f"regime status is {resolution.status.value}."
                ),
            )
            records.append(record)
            traces.append(TraceRecord(
                trace_id=_stable_id("trace", retrieval_id),
                document_hash=document_hash,
                stage="governed_rule_retrieval",
                entity_type="rule_retrieval",
                entity_id=retrieval_id,
                parent_trace_ids=[],
                payload_sha256=_sha256(_canonical_bytes(record.model_dump(mode="json"))),
            ))
        abstentions: list[Abstention] = []
        if resolution.status != RegimeResolutionStatus.RESOLVED:
            abstentions.append(Abstention(
                abstention_id=_stable_id("abs", segment.segment_id, category.value, resolution.status.value),
                stage="regime_resolution",
                subject=f"{segment.segment_id}:{category.value}",
                reason_code=resolution.status.value,
                explanation=resolution.explanation,
            ))
        elif not records:
            abstentions.append(Abstention(
                abstention_id=_stable_id("abs", segment.segment_id, category.value, "no_rule_candidate"),
                stage="governed_rule_retrieval",
                subject=f"{segment.segment_id}:{category.value}",
                reason_code="no_governed_rule_candidate",
                explanation="No relevant rule candidate was found in the manifest-verified governed assets.",
            ))
        return RuleRetrievalResult(
            segment_id=segment.segment_id,
            rule_category=category,
            regime_resolution=resolution,
            records=records,
            asset_verifications=verifications,
            abstentions=abstentions,
            trace_records=traces,
        )
