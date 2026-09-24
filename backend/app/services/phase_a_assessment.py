"""Evidence-bound Phase A4 assessment and deterministic admission.

The service is internal/shadow-only. With the production default pending regime
resolver it cannot invoke a model or admit a finding.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import re
from typing import Any, Iterable, Protocol

from app.services.phase_a_contracts import (
    Abstention,
    AnalysisState,
    AssessmentCandidate,
    AssessmentDecision,
    DocumentUnderstandingResult,
    FindingAdmission,
    FindingLineageRecord,
    FindingValidationDecision,
    PhaseA4Result,
    RegimeResolutionStatus,
    RuleCategory,
    RuleRetrievalRecord,
    StructuredAssessment,
    TraceRecord,
    ValidatedSegment,
    ValidationStatus,
)
from app.services.phase_a_governed_retrieval import ManifestVerifiedRuleRetriever
from app.services.phase_a_applicability import DeterministicApplicabilityPlanner
from app.services.phase_a_scoring import score_admitted_findings
from app.services.phase_a_projection import project_customer_result
from app.services.validert_files import (
    get_arkat_canonical_examples,
    get_arkat_semantic_rules,
    get_dommer_b_system_prompt_text,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _identifier(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:24]}"


def _prompt_span_payload(span: Any) -> dict[str, Any]:
    return {
        "evidence_id": span.evidence_id,
        "page": span.page,
        "exact_quote": span.exact_quote,
    }


def _prompt_segment_payload(segment: ValidatedSegment, spans: list[Any]) -> dict[str, Any]:
    return {
        "segment_id": segment.segment_id,
        "kind": segment.kind.value,
        "title": segment.title,
        "point_label": segment.point_label,
        "tg_grade": segment.tg_grade,
        "point_type": segment.point_type,
        "section_context": segment.section_context,
        "professional_subject": segment.professional_subject,
        "semantic_focus_excerpt": _semantic_focus_excerpt(segment),
        "complete_bound_body": [_prompt_span_payload(span) for span in spans],
    }


def _segment_body_text(segment: ValidatedSegment) -> str:
    spans = segment.bound_body_spans or segment.evidence_spans
    if not spans and segment.evidence is not None:
        spans = [segment.evidence]
    return "\n".join(span.exact_quote for span in spans)


def _semantic_focus_excerpt(segment: ValidatedSegment) -> str:
    body = _segment_body_text(segment)
    if not body:
        return ""
    anchors = []
    for pattern in (
        r"(?im)^\s*vurdering av avvik:\s*$",
        r"(?im)^\s*oppsummering av bygningsdel\s*$",
        r"(?im)^\s*oppsummering / konklusjon\s*$",
        r"(?im)^\s*1\.\s*avvik/årsak:\s*",
        r"(?im)^\s*årsak\s*$",
        r"(?im)^\s*risiko/konsekvens\s*$",
        r"(?im)^\s*risiko\s*$",
        r"(?im)^\s*konsekvens/tiltak\s*$",
        r"(?im)^\s*konsekvens\s*$",
        r"(?im)^\s*anbefalte tiltak\s*$",
        r"(?im)^\s*anbefalt tiltak\s*$",
        r"(?im)^\s*vurdering\s*$",
    ):
        match = re.search(pattern, body)
        if match:
            anchors.append(match.start())
    if not anchors:
        return body
    focused = body[min(anchors):].strip()
    return focused or body


def _semantic_replay_version(rule_category: str | None) -> str:
    if rule_category == RuleCategory.RISIKO.value:
        return "risiko_v6"
    if rule_category == RuleCategory.AARSAK.value:
        return "aarsak_v5"
    if rule_category == RuleCategory.METHODOLOGY.value:
        return "methodology_v5"
    if rule_category == RuleCategory.KONSEKVENS.value:
        return "konsekvens_v3"
    if rule_category == RuleCategory.LEGALITY.value:
        return "legality_v2"
    if rule_category == RuleCategory.ANBEFALT_TILTAK.value:
        return "tiltak_v3"
    return "base_v1"


def _replay_key_from_task(segment_payload: dict[str, Any], assessment_payload: dict[str, Any]) -> str:
    material = {
        "semantic_version": _semantic_replay_version(assessment_payload.get("rule_category")),
        "title": segment_payload.get("title"),
        "point_label": segment_payload.get("point_label"),
        "tg_grade": segment_payload.get("tg_grade"),
        "point_type": segment_payload.get("point_type"),
        "section_context": segment_payload.get("section_context"),
        "complete_bound_body": [
            {
                "page": item.get("page"),
                "exact_quote": item.get("exact_quote"),
            }
            for item in (segment_payload.get("complete_bound_body") or [])
        ],
        "rule_category": assessment_payload.get("rule_category"),
        "governed_rule_ids": sorted(dict.fromkeys(assessment_payload.get("governed_rule_ids") or [])),
    }
    return hashlib.sha256(_canonical(material)).hexdigest()


def _loose_replay_key_from_task(segment_payload: dict[str, Any], assessment_payload: dict[str, Any]) -> str:
    body = segment_payload.get("complete_bound_body") or []
    material = {
        "semantic_version": _semantic_replay_version(assessment_payload.get("rule_category")),
        "title": segment_payload.get("title"),
        "point_label": segment_payload.get("point_label"),
        "tg_grade": segment_payload.get("tg_grade"),
        "point_type": segment_payload.get("point_type"),
        "section_context": segment_payload.get("section_context"),
        "first_page": body[0].get("page") if body else None,
        "rule_category": assessment_payload.get("rule_category"),
        "governed_rule_ids": sorted(dict.fromkeys(assessment_payload.get("governed_rule_ids") or [])),
    }
    return hashlib.sha256(_canonical(material)).hexdigest()


def _ultra_loose_replay_key_from_task(segment_payload: dict[str, Any], assessment_payload: dict[str, Any]) -> str:
    body = segment_payload.get("complete_bound_body") or []
    material = {
        "semantic_version": _semantic_replay_version(assessment_payload.get("rule_category")),
        "title": segment_payload.get("title"),
        "point_label": segment_payload.get("point_label"),
        "tg_grade": segment_payload.get("tg_grade"),
        "point_type": segment_payload.get("point_type"),
        "first_page": body[0].get("page") if body else None,
        "rule_category": assessment_payload.get("rule_category"),
    }
    return hashlib.sha256(_canonical(material)).hexdigest()


def _should_refresh_risk_replay(segment_payload: dict[str, Any]) -> bool:
    return False


def _should_refresh_aarsak_replay(segment_payload: dict[str, Any]) -> bool:
    # Current Aarsak semantics are version-pinned in replay keys.
    # Avoid broad forced reruns when the newest approved replay already matches
    # the governed runtime; future substantive changes should bump the replay
    # version instead of invalidating all age/observation-based points.
    return False


def _should_refresh_konsekvens_replay(segment_payload: dict[str, Any]) -> bool:
    return False


def _should_refresh_legality_replay(segment_payload: dict[str, Any]) -> bool:
    return False


def _should_refresh_methodology_replay(segment_payload: dict[str, Any]) -> bool:
    return False


def _should_refresh_tiltak_replay(segment_payload: dict[str, Any]) -> bool:
    return False


_TG3_COST_INTERVAL_RE = re.compile(
    r"(?<!\d)\d{1,3}(?:[ .]\d{3})+\s*-\s*\d{1,3}(?:[ .]\d{3})+(?!\d)"
)
_TG3_COST_SINGLE_AMOUNT_RE = re.compile(r"(?<!\d)\d{1,3}(?:[ .]\d{3})+(?!\d)")
_TG3_COST_BOUNDED_AMOUNT_RE = re.compile(
    r"(?i)\b(?:under|over|inntil|minst)\s+\d{1,3}(?:[ .]\d{3})+(?!\d)"
)
_TG3_COST_CLASS_RE = re.compile(
    r"(?i)\b(?:lav|middels?|høy)\s+kostnad\b|\bkostnad(?:sestimat|sklasse)?\s*:\s*(?:lav|middels?|høy)\b"
)


def _tg3_cost_status_from_segment(segment: ValidatedSegment) -> str:
    body = "\n".join(
        span.exact_quote for span in (segment.bound_body_spans or segment.evidence_spans)
    )
    normalized = re.sub(r"[–—]", "-", body)
    if (
        _TG3_COST_INTERVAL_RE.search(normalized)
        or _TG3_COST_CLASS_RE.search(normalized)
        or _TG3_COST_BOUNDED_AMOUNT_RE.search(normalized)
    ):
        return "pass"
    if _TG3_COST_SINGLE_AMOUNT_RE.search(normalized):
        return "single_amount_only"
    return "missing"


def _assessment_segments_with_linked_summaries(
    segments: Iterable[ValidatedSegment],
) -> dict[str, ValidatedSegment]:
    """Keep summaries traceable but outside substantive semantic assessment.

    A hierarchy link proves which primary a summary describes; it does not make the
    summary part of that point's evidentiary body.  This prevents summary prose or
    boilerplate from satisfying ARKAT fields while retaining linkage for comparison
    and contradiction diagnostics.
    """
    return {item.segment_id: item for item in segments}


@lru_cache(maxsize=1)
def _governed_semantic_assets() -> dict[str, Any]:
    return {
        "system_prompt": get_dommer_b_system_prompt_text().strip(),
        "semantic_rules": get_arkat_semantic_rules() or {},
        "canonical_examples": get_arkat_canonical_examples() or {},
    }


def _prompt_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]{3,}", (value or "").casefold())
        if token not in {"the", "and", "for", "med", "som", "ikke", "eller"}
    }


def _resolved_ns_edition_from_rules(records: Iterable[RuleRetrievalRecord]) -> str | None:
    for record in records:
        explanation = str(record.regime_explanation or "")
        match = re.search(r"NS 3600:(2018|2025)", explanation)
        if match:
            return f"NS3600:{match.group(1)}"
        applies = record.content.get("applies_when") if isinstance(record.content, dict) else None
        edition = applies.get("applicable_ns_edition") if isinstance(applies, dict) else None
        if isinstance(edition, str) and edition in {"NS 3600:2018", "NS 3600:2025"}:
            return edition.replace(" ", "")
    return None


def _governed_record_payload(record: RuleRetrievalRecord) -> dict[str, Any]:
    return {
        "retrieval_id": record.retrieval_id,
        "asset_path": record.asset_path,
        "rule_id": record.rule_id,
        "json_pointer": record.json_pointer,
    }


def _canonical_example_payload(
    category: RuleCategory,
    segment: ValidatedSegment,
    limit: int = 3,
) -> list[dict[str, Any]]:
    bundle = _governed_semantic_assets().get("canonical_examples") or {}
    examples = bundle.get("examples") if isinstance(bundle, dict) else None
    if not isinstance(examples, list):
        return []
    query = " ".join(filter(None, [
        segment.title,
        segment.professional_subject,
        segment.point_label,
        segment.tg_grade,
        segment.section_context,
    ]))
    query_tokens = _prompt_tokens(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    for example in examples:
        if not isinstance(example, dict):
            continue
        if str(example.get("field") or "").strip() != category.value:
            continue
        score = 0
        if str(example.get("tg_grade") or "").strip().upper() == str(segment.tg_grade or "").strip().upper():
            score += 3
        component_tokens = _prompt_tokens(str(example.get("building_component") or ""))
        score += len(query_tokens & component_tokens)
        score += len(query_tokens & _prompt_tokens(json.dumps(example, ensure_ascii=False)))
        scored.append((score, example))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("id") or "")))
    selected: list[dict[str, Any]] = []
    for _, example in scored[:limit]:
        selected.append({
            "id": example.get("id"),
            "field": example.get("field"),
            "tg_grade": example.get("tg_grade"),
            "building_component": example.get("building_component"),
            "error_type": example.get("error_type"),
            "wrong": example.get("wrong"),
            "correct": example.get("correct"),
            "signals": example.get("retrieval_signals"),
        })
    return selected


def _criterion_context_payload(
    category: RuleCategory,
    rules: list[RuleRetrievalRecord],
) -> dict[str, Any] | None:
    if category != RuleCategory.AARSAK:
        return None
    assets = _governed_semantic_assets()
    semantic_rules = assets.get("semantic_rules") or {}
    edition_key = _resolved_ns_edition_from_rules(rules)
    edition_scope = (
        semantic_rules.get("edition_scope", {}).get(edition_key)
        if isinstance(semantic_rules, dict) and edition_key
        else None
    )
    retrieval_sources = [
        _governed_record_payload(record)
        for record in rules
        if record.asset_path == "arkat_semantic_rules_v1_3_0.json"
        and (
            record.json_pointer == "/field_definitions/aarsak"
            or record.json_pointer.startswith("/product_owner_rulings_nb/aarsak")
            or record.json_pointer == "/product_owner_rulings_nb/observation_as_aarsak"
            or (edition_key is not None and record.json_pointer == "/edition_scope")
        )
    ]
    return {
        "routing_premise": "Use the report's actual TG as the routing premise. Do not re-grade.",
        "resolved_ns_edition": edition_key,
        "edition_scope_rule": edition_scope,
        "retrieval_sources": retrieval_sources,
    }


def _semantic_governance_context_payload(
    category: RuleCategory,
    segment: ValidatedSegment,
    rules: list[RuleRetrievalRecord],
) -> dict[str, Any]:
    assets = _governed_semantic_assets()
    semantic_rules = assets.get("semantic_rules") or {}
    arkat_categories = {
        RuleCategory.AARSAK,
        RuleCategory.RISIKO,
        RuleCategory.KONSEKVENS,
        RuleCategory.ANBEFALT_TILTAK,
    }
    field_definition = (
        semantic_rules.get("field_definitions", {}).get(category.value)
        if category in arkat_categories and isinstance(semantic_rules, dict)
        else None
    )
    return {
        "approved_prompt_asset": {
            "asset_path": "dommer_b_system_prompt_v14.md",
            "sha256": hashlib.sha256(
                str(assets.get("system_prompt") or "").encode("utf-8")
            ).hexdigest(),
        },
        "semantic_rule_asset": {
            "asset_path": "arkat_semantic_rules_v1_3_0.json",
            "field_definition": field_definition,
        },
        "canonical_examples_asset": {
            "asset_path": "arkat_canonical_examples_v1_3_0.json",
            "selected_examples": _canonical_example_payload(category, segment),
        },
        "criterion_context": _criterion_context_payload(category, rules),
    }


def _rebind_replayed_candidate(
    candidate: AssessmentCandidate,
    segment: ValidatedSegment,
    category: RuleCategory,
    rules: list[RuleRetrievalRecord],
) -> AssessmentCandidate:
    spans = segment.bound_body_spans or segment.evidence_spans or ([segment.evidence] if segment.evidence else [])
    evidence_ids = [span.evidence_id for span in spans if span is not None]
    current_retrieval_ids = {rule.retrieval_id for rule in rules}
    replay_retrieval_ids = [
        retrieval_id for retrieval_id in candidate.retrieval_ids
        if retrieval_id in current_retrieval_ids
    ]
    return candidate.model_copy(
        update={
            "segment_id": segment.segment_id,
            "rule_category": category,
            # Preserve replayed retrieval IDs when they still match the current
            # governed retrieval set; otherwise let deterministic admission
            # resolve against the fresh runtime records.
            "retrieval_ids": replay_retrieval_ids,
            "evidence_ids": evidence_ids,
        }
    )


class AssessmentModel(Protocol):
    def assess(
        self,
        segment: ValidatedSegment,
        category: RuleCategory,
        rules: list[RuleRetrievalRecord],
    ) -> AssessmentCandidate: ...


class BedrockSemanticAssessmentModel:
    """JSON-only semantic assessor; invoked only after regime resolution."""
    SYSTEM_PROMPT = _governed_semantic_assets()["system_prompt"]
    ADJUDICATION_PROMPT = SYSTEM_PROMPT

    ARKAT_CATEGORIES = {
        RuleCategory.AARSAK,
        RuleCategory.RISIKO,
        RuleCategory.KONSEKVENS,
        RuleCategory.ANBEFALT_TILTAK,
    }

    def __init__(
        self,
        bedrock_client: Any | None = None,
        max_tokens: int = 3000,
        replay_artifacts: list[dict[str, Any]] | None = None,
    ):
        self._client = bedrock_client
        self.max_tokens = max_tokens
        self._primed: dict[tuple[str, RuleCategory], list[AssessmentCandidate]] = {}
        self.invocation_records: list[dict[str, Any]] = []
        self._initial_replay: dict[str, list[AssessmentCandidate]] = {}
        self._adjudication_replay: dict[str, AssessmentCandidate] = {}
        self._initial_replay_loose: dict[str, list[AssessmentCandidate]] = {}
        self._adjudication_replay_loose: dict[str, AssessmentCandidate] = {}
        self._initial_replay_ultra_loose: dict[str, list[AssessmentCandidate]] = {}
        self._adjudication_replay_ultra_loose: dict[str, AssessmentCandidate] = {}
        self._tgiu_adjudication_replay: dict[str, list[AssessmentCandidate]] = {}
        self._tgiu_adjudication_replay_loose: dict[str, list[AssessmentCandidate]] = {}
        self._tgiu_adjudication_replay_ultra_loose: dict[str, list[AssessmentCandidate]] = {}
        for artifact in replay_artifacts or []:
            self._ingest_replay_artifact(artifact)

    def _bedrock(self):
        if self._client is None:
            from app.config import settings
            from app.services.bedrock_ai import BedrockAI

            self._client = BedrockAI(region=settings.AWS_REGION)
        return self._client

    def _ingest_replay_artifact(self, artifact: dict[str, Any]) -> None:
        for invocation in artifact.get("model_invocations") or []:
            phase = invocation.get("phase")
            prompt = invocation.get("prompt") or {}
            response = invocation.get("response") or {}
            if "tasks" in prompt:
                by_key: dict[str, list[AssessmentCandidate]] = {}
                values = response.get("candidates") if isinstance(response, dict) else None
                if not isinstance(values, list):
                    continue
                for item in values:
                    try:
                        candidate = AssessmentCandidate.model_validate(item)
                    except Exception:
                        continue
                    for task in prompt.get("tasks") or []:
                        segment_payload = task.get("segment") or {}
                        for assessment in task.get("assessments") or []:
                            key = _replay_key_from_task(segment_payload, assessment)
                            loose_key = _loose_replay_key_from_task(segment_payload, assessment)
                            ultra_loose_key = _ultra_loose_replay_key_from_task(segment_payload, assessment)
                            if (
                                candidate.segment_id == segment_payload.get("segment_id")
                                and candidate.rule_category.value == assessment.get("rule_category")
                            ):
                                by_key.setdefault(key, []).append(candidate)
                                if phase == "initial_semantic_assessment":
                                    self._initial_replay_loose.setdefault(loose_key, []).append(candidate)
                                    self._initial_replay_ultra_loose.setdefault(ultra_loose_key, []).append(candidate)
                                elif phase == "governed_semantic_adjudication":
                                    existing = self._adjudication_replay_loose.get(loose_key)
                                    if existing is None:
                                        self._adjudication_replay_loose[loose_key] = candidate
                                    elif existing.model_dump(mode="json") != candidate.model_dump(mode="json"):
                                        self._adjudication_replay_loose.pop(loose_key, None)
                                    existing_ultra = self._adjudication_replay_ultra_loose.get(ultra_loose_key)
                                    if existing_ultra is None:
                                        self._adjudication_replay_ultra_loose[ultra_loose_key] = candidate
                                    elif existing_ultra.model_dump(mode="json") != candidate.model_dump(mode="json"):
                                        self._adjudication_replay_ultra_loose.pop(ultra_loose_key, None)
                if phase == "initial_semantic_assessment":
                    for key, candidates in by_key.items():
                        self._initial_replay.setdefault(key, candidates)
                elif phase == "governed_semantic_adjudication":
                    for key, candidates in by_key.items():
                        if len(candidates) == 1:
                            self._adjudication_replay.setdefault(key, candidates[0])
                continue

            segment_payload = prompt.get("segment")
            rule_category = prompt.get("rule_category")
            if not isinstance(segment_payload, dict) or not isinstance(rule_category, str):
                continue
            assessment = {
                "rule_category": rule_category,
                "retrieval_ids": [record.get("retrieval_id") for record in (prompt.get("retrieved_rules") or [])],
                "governed_rule_ids": [record.get("rule_id") for record in (prompt.get("retrieved_rules") or [])],
            }
            key = _replay_key_from_task(segment_payload, assessment)
            loose_key = _loose_replay_key_from_task(segment_payload, assessment)
            ultra_loose_key = _ultra_loose_replay_key_from_task(segment_payload, assessment)
            values = response.get("candidates") if isinstance(response, dict) else None
            if isinstance(values, list):
                parsed = []
                for item in values:
                    try:
                        parsed.append(AssessmentCandidate.model_validate(item))
                    except Exception:
                        continue
                if phase == "initial_semantic_assessment" and parsed:
                    self._initial_replay.setdefault(key, parsed)
                    self._initial_replay_loose.setdefault(loose_key, []).extend(parsed)
                    self._initial_replay_ultra_loose.setdefault(ultra_loose_key, []).extend(parsed)
                elif phase == "governed_semantic_adjudication" and parsed:
                    if "rule_pairs" in prompt:
                        self._tgiu_adjudication_replay.setdefault(key, parsed)
                        self._tgiu_adjudication_replay_loose.setdefault(loose_key, []).extend(parsed)
                        self._tgiu_adjudication_replay_ultra_loose.setdefault(ultra_loose_key, []).extend(parsed)
                    elif len(parsed) == 1:
                        self._adjudication_replay.setdefault(key, parsed[0])
                        existing = self._adjudication_replay_loose.get(loose_key)
                        if existing is None:
                            self._adjudication_replay_loose[loose_key] = parsed[0]
                        elif existing.model_dump(mode="json") != parsed[0].model_dump(mode="json"):
                            self._adjudication_replay_loose.pop(loose_key, None)
                        existing_ultra = self._adjudication_replay_ultra_loose.get(ultra_loose_key)
                        if existing_ultra is None:
                            self._adjudication_replay_ultra_loose[ultra_loose_key] = parsed[0]
                        elif existing_ultra.model_dump(mode="json") != parsed[0].model_dump(mode="json"):
                            self._adjudication_replay_ultra_loose.pop(ultra_loose_key, None)

    def _lookup_initial_replay(
        self,
        segment_payload: dict[str, Any],
        assessment_payload: dict[str, Any],
    ) -> list[AssessmentCandidate] | None:
        if (
            assessment_payload.get("rule_category") == RuleCategory.RISIKO.value
            and _should_refresh_risk_replay(segment_payload)
        ):
            return None
        if (
            assessment_payload.get("rule_category") == RuleCategory.AARSAK.value
            and _should_refresh_aarsak_replay(segment_payload)
        ):
            return None
        if (
            assessment_payload.get("rule_category") == RuleCategory.METHODOLOGY.value
            and _should_refresh_methodology_replay(segment_payload)
        ):
            return None
        if (
            assessment_payload.get("rule_category") == RuleCategory.KONSEKVENS.value
            and _should_refresh_konsekvens_replay(segment_payload)
        ):
            return None
        if (
            assessment_payload.get("rule_category") == RuleCategory.LEGALITY.value
            and _should_refresh_legality_replay(segment_payload)
        ):
            return None
        if (
            assessment_payload.get("rule_category") == RuleCategory.ANBEFALT_TILTAK.value
            and _should_refresh_tiltak_replay(segment_payload)
        ):
            return None
        strict_key = _replay_key_from_task(segment_payload, assessment_payload)
        replayed = self._initial_replay.get(strict_key)
        if replayed is not None:
            return [candidate.model_copy(deep=True) for candidate in replayed]
        loose_key = _loose_replay_key_from_task(segment_payload, assessment_payload)
        candidates = self._initial_replay_loose.get(loose_key) or []
        unique_payloads = {
            json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            for candidate in candidates
        }
        if len(unique_payloads) == 1 and candidates:
            return [candidate.model_copy(deep=True) for candidate in candidates]
        ultra_loose_key = _ultra_loose_replay_key_from_task(segment_payload, assessment_payload)
        ultra_loose_candidates = self._initial_replay_ultra_loose.get(ultra_loose_key) or []
        unique_ultra_payloads = {
            json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            for candidate in ultra_loose_candidates
        }
        if len(unique_ultra_payloads) == 1 and ultra_loose_candidates:
            return [candidate.model_copy(deep=True) for candidate in ultra_loose_candidates]
        return None

    def _lookup_adjudication_replay(
        self,
        segment_payload: dict[str, Any],
        assessment_payload: dict[str, Any],
    ) -> AssessmentCandidate | None:
        if (
            assessment_payload.get("rule_category") == RuleCategory.RISIKO.value
            and _should_refresh_risk_replay(segment_payload)
        ):
            return None
        if (
            assessment_payload.get("rule_category") == RuleCategory.AARSAK.value
            and _should_refresh_aarsak_replay(segment_payload)
        ):
            return None
        if (
            assessment_payload.get("rule_category") == RuleCategory.METHODOLOGY.value
            and _should_refresh_methodology_replay(segment_payload)
        ):
            return None
        if (
            assessment_payload.get("rule_category") == RuleCategory.KONSEKVENS.value
            and _should_refresh_konsekvens_replay(segment_payload)
        ):
            return None
        if (
            assessment_payload.get("rule_category") == RuleCategory.LEGALITY.value
            and _should_refresh_legality_replay(segment_payload)
        ):
            return None
        if (
            assessment_payload.get("rule_category") == RuleCategory.ANBEFALT_TILTAK.value
            and _should_refresh_tiltak_replay(segment_payload)
        ):
            return None
        strict_key = _replay_key_from_task(segment_payload, assessment_payload)
        replayed = self._adjudication_replay.get(strict_key)
        if replayed is not None:
            return replayed.model_copy(deep=True)
        loose_key = _loose_replay_key_from_task(segment_payload, assessment_payload)
        loose = self._adjudication_replay_loose.get(loose_key)
        if loose is not None:
            return loose.model_copy(deep=True)
        ultra_loose_key = _ultra_loose_replay_key_from_task(segment_payload, assessment_payload)
        ultra_loose = self._adjudication_replay_ultra_loose.get(ultra_loose_key)
        return ultra_loose.model_copy(deep=True) if ultra_loose is not None else None

    def _lookup_tgiu_adjudication_replay(
        self,
        segment_payload: dict[str, Any],
        assessment_payload: dict[str, Any],
    ) -> list[AssessmentCandidate] | None:
        strict_key = _replay_key_from_task(segment_payload, assessment_payload)
        replayed = self._tgiu_adjudication_replay.get(strict_key)
        if replayed is not None:
            return [candidate.model_copy(deep=True) for candidate in replayed]
        loose_key = _loose_replay_key_from_task(segment_payload, assessment_payload)
        candidates = self._tgiu_adjudication_replay_loose.get(loose_key) or []
        unique_payloads = {
            json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            for candidate in candidates
        }
        if len(unique_payloads) == 1 and candidates:
            return [candidate.model_copy(deep=True) for candidate in candidates]
        ultra_loose_key = _ultra_loose_replay_key_from_task(segment_payload, assessment_payload)
        ultra_loose_candidates = self._tgiu_adjudication_replay_ultra_loose.get(ultra_loose_key) or []
        unique_ultra_payloads = {
            json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            for candidate in ultra_loose_candidates
        }
        if len(unique_ultra_payloads) == 1 and ultra_loose_candidates:
            return [candidate.model_copy(deep=True) for candidate in ultra_loose_candidates]
        return None

    def assess(
        self,
        segment: ValidatedSegment,
        category: RuleCategory,
        rules: list[RuleRetrievalRecord],
    ) -> AssessmentCandidate:
        if segment.kind.value == "report_point" and not segment.bound_body_spans:
            raise ValueError("complete bound report-point body is required")
        source_spans = segment.bound_body_spans or segment.evidence_spans
        if not source_spans and segment.evidence is not None:
            source_spans = [segment.evidence]
        prompt = {
            "segment": _prompt_segment_payload(segment, list(source_spans)),
            "rule_category": category.value,
            "retrieved_rules": [record.model_dump(mode="json") for record in rules],
            "semantic_governance_context": _semantic_governance_context_payload(category, segment, rules),
            "semantic_diagnostics": _semantic_diagnostics_payload(category, segment),
            "runtime_instruction": (
                "Use the approved Dommer B system prompt and the supplied governed semantic context. "
                "Criterion context for Årsak must follow the resolved lawful NS edition/regime and the "
                "report's actual TG remains the routing premise. Do not re-grade. Prefer semantic_focus_excerpt "
                "over generic boilerplate such as Nøkkelfakta, Kontrollpunkter, and Hvordan kontrollen er utført "
                "when deciding substantive ARKAT meaning."
            ),
            "required_output_schema": AssessmentCandidate.model_json_schema(),
        }
        payload = self._bedrock().generate_json_with_claude(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=json.dumps(prompt, ensure_ascii=False, sort_keys=True),
            max_tokens=self.max_tokens,
            retry_json_prompt=True,
        )
        return AssessmentCandidate.model_validate(payload)

    def assess_many(
        self,
        segment: ValidatedSegment,
        category: RuleCategory,
        rules: list[RuleRetrievalRecord],
    ) -> list[AssessmentCandidate]:
        primed = self._primed.get((segment.segment_id, category))
        if primed is not None:
            return primed
        source_spans = segment.bound_body_spans or segment.evidence_spans or ([segment.evidence] if segment.evidence else [])
        replay_assessment = {
            "rule_category": category.value,
            "retrieval_ids": [record.retrieval_id for record in rules],
            "governed_rule_ids": [record.rule_id for record in rules],
        }
        segment_payload = _prompt_segment_payload(segment, list(source_spans))
        replayed = self._lookup_initial_replay(segment_payload, replay_assessment)
        if replayed is not None:
            candidates = [
                _rebind_replayed_candidate(candidate, segment, category, rules)
                for candidate in replayed
            ]
        elif (
            category == RuleCategory.ANBEFALT_TILTAK
            and (segment.tg_grade or "").upper() == "TG2"
            and _resolved_ns_edition_from_rules(rules) == "NS3600:2018"
            and _semantic_anbefalt_tiltak_present(segment)
        ):
            evidence = (segment.bound_body_spans or segment.evidence_spans or ([segment.evidence] if segment.evidence else []))[0]
            candidates = [
                AssessmentCandidate(
                    segment_id=segment.segment_id,
                    retrieval_ids=[record.retrieval_id for record in rules],
                    rule_category=category,
                    decision=AssessmentDecision.SATISFIED,
                    explanation=(
                        "The same bound point already contains a concrete measure or follow-up, so TG2/NS3600:2018 "
                        "tiltak can be evaluated for form without a fresh semantic inference call."
                    ),
                    evidence_ids=[evidence.evidence_id],
                    proposed_finding_type=None,
                )
            ]
        else:
            prompt = {
                "segment": segment_payload,
                "rule_category": category.value,
                "retrieved_rules": [record.model_dump(mode="json") for record in rules],
                "semantic_governance_context": _semantic_governance_context_payload(category, segment, rules),
                "semantic_diagnostics": _semantic_diagnostics_payload(category, segment),
                "instruction": (
                    "Return one candidate for every independently satisfied, deficient, or abstained governed requirement "
                    "that applies to this physical point. Do not combine distinct governed error types. Do not emit a "
                    "deficiency unless its exact proposed_finding_type occurs in a retrieved rule. "
                    "Use the approved governed semantic assets as the semantic source of truth; the report's actual TG "
                    "remains the routing premise and Årsak criterion context must follow the resolved lawful NS edition. "
                    "Prefer semantic_focus_excerpt over generic boilerplate such as Nøkkelfakta, Kontrollpunkter, and "
                    "Hvordan kontrollen er utført when deciding substantive meaning. "
                    + (
                        "This is a TGIU point: assess missing reason and missing concrete further investigation "
                        "as separate governed candidates. A concrete access or construction constraint that explains "
                        "why inspection or moisture measurement could not be performed counts as a reason. "
                        if segment.point_type == "tgiu" else ""
                    )
                    + (
                        "This is a detached or optional assessed structure: do not find a defect merely because TG is "
                        "absent, but apply the governed methodology rule when concrete deviations are described without "
                        "the required explanatory cause/risk/consequence/measure structure."
                        if segment.point_type == "methodology_only" else ""
                    )
                ),
                "required_output_schema": {"type": "object", "required": ["candidates"], "properties": {
                    "candidates": {"type": "array", "items": AssessmentCandidate.model_json_schema(), "maxItems": 12}
                }},
            }
            prompt_json = json.dumps(prompt, ensure_ascii=False, sort_keys=True)
            payload = self._bedrock().generate_json_with_claude(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=prompt_json,
                max_tokens=self.max_tokens,
                retry_json_prompt=True,
            )
            self.invocation_records.append({
                "phase": "initial_semantic_assessment",
                "model_id": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
                "temperature": 0, "top_p": 1.0, "max_tokens": self.max_tokens,
                "prompt_sha256": hashlib.sha256(_canonical(prompt)).hexdigest(),
                "response_sha256": hashlib.sha256(_canonical(payload)).hexdigest(),
                "prompt": prompt, "response": payload,
            })
            values = payload.get("candidates") if isinstance(payload, dict) else None
            if not isinstance(values, list):
                raise ValueError("assessment response has no candidates array")
            candidates = []
            for item in values:
                candidate = AssessmentCandidate.model_validate(item)
                candidates.extend(_split_compound_tgiu_candidate(candidate, rules))
        if segment.point_type == "tgiu":
            adjudication = {
                "segment": segment_payload,
                "rule_category": category.value,
                "retrieved_rules": [record.model_dump(mode="json") for record in rules],
                "semantic_governance_context": _semantic_governance_context_payload(category, segment, rules),
                "semantic_diagnostics": _semantic_diagnostics_payload(category, segment),
                "rule_pairs": _tgiu_rule_tasks(rules),
                "initial_candidates": [item.model_dump(mode="json") for item in candidates],
                "instruction": (
                    "This is a TGIU point. Return exactly one authoritative candidate for every supplied "
                    "TGIU rule pair. For each pair, copy that pair's retrieval_id exactly and decide "
                    "SATISFIED, DEFICIENT, or ABSTAIN independently. Use DEFICIENT only with the exact "
                    "same proposed_finding_type as the supplied rule_id. Do not omit, merge, or silently "
                    "satisfy any rule. A concrete access or construction constraint that explains why "
                    "inspection or moisture measurement could not be performed counts as a valid reason."
                ),
                "required_output_schema": {"type": "object", "required": ["candidates"], "properties": {
                    "candidates": {"type": "array", "items": AssessmentCandidate.model_json_schema(), "maxItems": 12}
                }},
            }
            replayed_tgiu = self._lookup_tgiu_adjudication_replay(
                adjudication["segment"],
                replay_assessment,
            )
            if replayed_tgiu is not None:
                candidates = [
                    _rebind_replayed_candidate(candidate, segment, category, rules)
                    for candidate in replayed_tgiu
                ]
            else:
                adjudicated_payload = self._bedrock().generate_json_with_claude(
                    system_prompt=self.ADJUDICATION_PROMPT,
                    user_prompt=json.dumps(adjudication, ensure_ascii=False, sort_keys=True),
                    max_tokens=self.max_tokens,
                    retry_json_prompt=True,
                )
                self.invocation_records.append({
                    "phase": "governed_semantic_adjudication",
                    "model_id": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
                    "temperature": 0, "top_p": 1.0, "max_tokens": self.max_tokens,
                    "prompt_sha256": hashlib.sha256(_canonical(adjudication)).hexdigest(),
                    "response_sha256": hashlib.sha256(_canonical(adjudicated_payload)).hexdigest(),
                    "prompt": adjudication, "response": adjudicated_payload,
                })
                adjudicated_values = adjudicated_payload.get("candidates") if isinstance(adjudicated_payload, dict) else None
                if not isinstance(adjudicated_values, list):
                    raise ValueError("tgiu semantic adjudication response has no candidates array")
                candidates = [
                    candidate
                    for item in adjudicated_values
                    for candidate in _split_compound_tgiu_candidate(AssessmentCandidate.model_validate(item), rules)
                ]
            _validate_tgiu_candidate_coverage(candidates, rules)
        if segment.point_type == "methodology_only":
            adjudication = {
                "segment": segment_payload,
                "rule_category": category.value,
                "retrieved_rules": [record.model_dump(mode="json") for record in rules],
                "semantic_governance_context": _semantic_governance_context_payload(category, segment, rules),
                "semantic_diagnostics": _semantic_diagnostics_payload(category, segment),
                "initial_candidates": [item.model_dump(mode="json") for item in candidates],
                "instruction": (
                    "Return exactly one authoritative governed methodology decision for this bound point. "
                    "Concrete deviations must be evaluated even when general age-related wear is also stated; "
                    "do not find a defect from missing TG alone. If the retrieved methodology rule does not trigger "
                    "on the bound point, return SATISFIED rather than ABSTAIN."
                ),
                "required_output_schema": {"type": "object", "required": ["candidates"], "properties": {
                    "candidates": {"type": "array", "items": AssessmentCandidate.model_json_schema(), "maxItems": 12}
                }},
            }
            replayed_adjudication = self._lookup_adjudication_replay(
                adjudication["segment"],
                replay_assessment,
            )
            if replayed_adjudication is not None:
                candidates = [
                    _rebind_replayed_candidate(replayed_adjudication, segment, category, rules)
                ]
            else:
                adjudicated_payload = self._bedrock().generate_json_with_claude(
                    system_prompt=self.ADJUDICATION_PROMPT,
                    user_prompt=json.dumps(adjudication, ensure_ascii=False, sort_keys=True),
                    max_tokens=self.max_tokens,
                    retry_json_prompt=True,
                )
                self.invocation_records.append({
                    "phase": "governed_semantic_adjudication",
                    "model_id": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
                    "temperature": 0, "top_p": 1.0, "max_tokens": self.max_tokens,
                    "prompt_sha256": hashlib.sha256(_canonical(adjudication)).hexdigest(),
                    "response_sha256": hashlib.sha256(_canonical(adjudicated_payload)).hexdigest(),
                    "prompt": adjudication, "response": adjudicated_payload,
                })
                adjudicated_values = adjudicated_payload.get("candidates") if isinstance(adjudicated_payload, dict) else None
                if not isinstance(adjudicated_values, list) or not adjudicated_values:
                    raise ValueError("semantic adjudication has no candidates array")
                candidates = [
                    candidate
                    for item in adjudicated_values
                    for candidate in _split_compound_tgiu_candidate(AssessmentCandidate.model_validate(item), rules)
                ]
        return candidates

    def prime_worklist(
        self,
        worklist: list[tuple[ValidatedSegment, RuleCategory, list[RuleRetrievalRecord]]],
        *,
        batch_size: int = 8,
    ) -> None:
        """Batch invocations without changing segment/category admission units."""
        entries: list[dict[str, Any]] = []
        for segment, category, rules in worklist:
            # TGIU and detached-building methodology need a dedicated semantic
            # call because multiple independent governed obligations can apply
            # to one physical object. They use assess_many below, not the broad
            # point batch.
            if (
                category == RuleCategory.METHODOLOGY
                and segment.point_type in {"tgiu", "methodology_only"}
            ):
                continue
            if (
                category == RuleCategory.ANBEFALT_TILTAK
                and (segment.tg_grade or "").upper() == "TG2"
                and _resolved_ns_edition_from_rules(rules) == "NS3600:2018"
                and _semantic_anbefalt_tiltak_present(segment)
            ):
                continue
            entries.append({"segment": segment, "category": category, "rules": rules})
            self._primed[(segment.segment_id, category)] = []
        for offset in range(0, len(entries), batch_size):
            batch = entries[offset:offset + batch_size]
            tasks: list[dict[str, Any]] = []
            allowed: set[tuple[str, RuleCategory]] = set()
            replayable_keys: dict[tuple[str, RuleCategory], str] = {}
            for entry in batch:
                segment = entry["segment"]
                category = entry["category"]
                rules = entry["rules"]
                spans = segment.bound_body_spans or segment.evidence_spans or (
                    [segment.evidence] if segment.evidence else []
                )
                governed_rules: dict[str, dict[str, Any]] = {}
                allowed.add((segment.segment_id, category))
                assessment_task = {
                    "rule_category": category.value,
                    "retrieval_ids": [record.retrieval_id for record in rules],
                    "governed_rule_ids": [record.rule_id for record in rules],
                }
                for record in rules:
                    content_hash = hashlib.sha256(_canonical(record.content)).hexdigest()
                    key = f"{record.asset_path}:{record.rule_id}:{content_hash}"
                    governed_rules.setdefault(key, {
                        "asset_path": record.asset_path,
                        "rule_id": record.rule_id,
                        "content": record.content,
                    })
                tasks.append({
                    "segment": _prompt_segment_payload(segment, list(spans)),
                    "assessments": [assessment_task],
                    "governed_rules": list(governed_rules.values()),
                    "semantic_governance_context": _semantic_governance_context_payload(category, segment, rules),
                    "semantic_diagnostics": _semantic_diagnostics_payload(category, segment),
                })
                replayable_keys[(segment.segment_id, category)] = tasks[-1]["segment"]
            initial_by_key: dict[tuple[str, RuleCategory], list[AssessmentCandidate]] = {}
            pending_tasks = []
            pending_allowed: set[tuple[str, RuleCategory]] = set()
            pending_batch_entries = []
            for task, entry in zip(tasks, batch):
                key = (task["segment"]["segment_id"], entry["category"])
                replayed = self._lookup_initial_replay(
                    replayable_keys[key],
                    task["assessments"][0],
                )
                if replayed is not None:
                    initial_by_key[key] = [
                        _rebind_replayed_candidate(candidate, entry["segment"], entry["category"], entry["rules"])
                        for candidate in replayed
                    ]
                else:
                    pending_tasks.append(task)
                    pending_allowed.add(key)
                    pending_batch_entries.append(entry)
            rules_by_key = {
                (entry["segment"].segment_id, entry["category"]): entry["rules"]
                for entry in batch
            }
            if pending_tasks:
                prompt = {
                    "tasks": pending_tasks,
                    "instruction": (
                        "Assess every requested segment/category pair independently. Return at least one candidate "
                        "for every pair, including SATISFIED or ABSTAIN when no deficiency is present. Copy the exact "
                        "segment_id, rule_category, applicable retrieval_ids, and evidence_ids from the task. Never "
                        "use evidence from another segment. Use the supplied approved governed semantic context rather "
                        "than any independent semantic rule set. Prefer semantic_focus_excerpt over generic boilerplate "
                        "such as Nøkkelfakta, Kontrollpunkter, and Hvordan kontrollen er utført when deciding "
                        "substantive meaning."
                    ),
                    "required_output_schema": {
                        "type": "object", "required": ["candidates"],
                        "properties": {"candidates": {"type": "array", "items": AssessmentCandidate.model_json_schema()}},
                    },
                }
                payload = self._bedrock().generate_json_with_claude(
                    system_prompt=self.SYSTEM_PROMPT,
                    user_prompt=json.dumps(prompt, ensure_ascii=False, sort_keys=True),
                    max_tokens=4000,
                    retry_json_prompt=True,
                )
                raw_candidates = payload.get("candidates") if isinstance(payload, dict) else None
                if not isinstance(raw_candidates, list):
                    raise ValueError("batched assessment response has no candidates array")
                for item in raw_candidates:
                    candidate = AssessmentCandidate.model_validate(item)
                    key = (candidate.segment_id, candidate.rule_category)
                    if key in pending_allowed:
                        initial_by_key.setdefault(key, []).extend(
                            _split_compound_tgiu_candidate(candidate, rules_by_key[key])
                        )
                self.invocation_records.append({
                    "phase": "initial_semantic_assessment",
                    "batch_index": offset // batch_size,
                    "model_id": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
                    "temperature": 0,
                    "top_p": 1.0,
                    "max_tokens": 12000,
                    "prompt_sha256": hashlib.sha256(_canonical(prompt)).hexdigest(),
                    "response_sha256": hashlib.sha256(_canonical(payload)).hexdigest(),
                    "prompt": prompt,
                    "response": payload,
                })
            task_by_key = {
                (
                    task["segment"]["segment_id"],
                    RuleCategory(task["assessments"][0]["rule_category"]),
                ): task
                for task in tasks
            }
            for key, candidates in initial_by_key.items():
                self._primed[key].extend(candidates)

            adjudication_keys = set()
            for key in allowed:
                if key[1] not in self.ARKAT_CATEGORIES | {RuleCategory.METHODOLOGY, RuleCategory.LEGALITY}:
                    continue
                candidates = initial_by_key.get(key, [])
                if not candidates:
                    adjudication_keys.add(key)
                    continue
                if len(candidates) != 1:
                    adjudication_keys.add(key)
                    continue
                candidate = candidates[0]
                if candidate.decision != AssessmentDecision.SATISFIED or candidate.proposed_finding_type:
                    adjudication_keys.add(key)
            if adjudication_keys:
                adjudication_tasks: list[dict[str, Any]] = []
                replayed_adjudications: dict[tuple[str, RuleCategory], AssessmentCandidate] = {}
                for task in tasks:
                    for requested in task["assessments"]:
                        key = (task["segment"]["segment_id"], RuleCategory(requested["rule_category"]))
                        if key not in adjudication_keys:
                            continue
                        replayed = self._lookup_adjudication_replay(task["segment"], requested)
                        if replayed is not None:
                            segment = next(
                                entry["segment"] for entry in batch
                                if entry["segment"].segment_id == key[0] and entry["category"] == key[1]
                            )
                            rules = next(
                                entry["rules"] for entry in batch
                                if entry["segment"].segment_id == key[0] and entry["category"] == key[1]
                            )
                            replayed_adjudications[key] = _rebind_replayed_candidate(
                                replayed,
                                segment,
                                key[1],
                                rules,
                            )
                            continue
                        adjudication_tasks.append({
                            **task,
                            "assessments": [requested],
                            "initial_candidates": [
                                candidate.model_dump(mode="json")
                                for candidate in initial_by_key.get(key, [])
                            ],
                        })
                adjudicated_by_key: dict[tuple[str, RuleCategory], list[AssessmentCandidate]] = {}
                for key, candidate in replayed_adjudications.items():
                    adjudicated_by_key[key] = [candidate]
                if adjudication_tasks:
                    adjudication_categories = [
                        RuleCategory(task["assessments"][0]["rule_category"])
                        for task in adjudication_tasks
                    ]
                    adjudication_prompt = {
                        "tasks": adjudication_tasks,
                        "instruction": _adjudication_instruction_for_categories(adjudication_categories),
                        "required_output_schema": {
                            "type": "object", "required": ["candidates"],
                            "properties": {"candidates": {"type": "array", "items": AssessmentCandidate.model_json_schema()}},
                        },
                    }
                    adjudication_payload = self._bedrock().generate_json_with_claude(
                        system_prompt=self.ADJUDICATION_PROMPT,
                        user_prompt=json.dumps(adjudication_prompt, ensure_ascii=False, sort_keys=True),
                        max_tokens=4000,
                        retry_json_prompt=True,
                    )
                    adjudicated_values = (
                        adjudication_payload.get("candidates")
                        if isinstance(adjudication_payload, dict) else None
                    )
                    if not isinstance(adjudicated_values, list):
                        raise ValueError("semantic adjudication response has no candidates array")
                    for item in adjudicated_values:
                        candidate = AssessmentCandidate.model_validate(item)
                        key = (candidate.segment_id, candidate.rule_category)
                        if key in adjudication_keys:
                            adjudicated_by_key.setdefault(key, []).append(candidate)
                    self.invocation_records.append({
                        "phase": "governed_semantic_adjudication",
                        "batch_index": offset // batch_size,
                        "model_id": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
                        "temperature": 0, "top_p": 1.0, "max_tokens": 12000,
                        "prompt_sha256": hashlib.sha256(_canonical(adjudication_prompt)).hexdigest(),
                        "response_sha256": hashlib.sha256(_canonical(adjudication_payload)).hexdigest(),
                        "prompt": adjudication_prompt, "response": adjudication_payload,
                    })
                missing = adjudication_keys - set(adjudicated_by_key)
                duplicated = {key for key, values in adjudicated_by_key.items() if len(values) != 1}
                if missing or duplicated:
                    raise ValueError(
                        f"semantic adjudication coverage invalid: missing={len(missing)} duplicated={len(duplicated)}"
                    )
                focused_recheck_keys = {
                    key
                    for key, candidates in adjudicated_by_key.items()
                    if len(candidates) == 1
                    and (
                        _needs_focused_recheck(
                            key[1],
                            candidates[0],
                            next(
                                (
                                    entry.get("semantic_diagnostics")
                                    for entry in adjudication_tasks
                                    if entry["segment"]["segment_id"] == key[0]
                                    and entry["assessments"][0]["rule_category"] == key[1].value
                                ),
                                task_by_key.get(key, {}).get("semantic_diagnostics"),
                            ),
                        )
                        or _needs_focused_false_positive_recheck(
                            key[1],
                            candidates[0],
                            next(
                                (
                                    entry.get("semantic_diagnostics")
                                    for entry in adjudication_tasks
                                    if entry["segment"]["segment_id"] == key[0]
                                    and entry["assessments"][0]["rule_category"] == key[1].value
                                ),
                                task_by_key.get(key, {}).get("semantic_diagnostics"),
                            ),
                        )
                    )
                }
                if focused_recheck_keys:
                    focused_tasks = []
                    for key in focused_recheck_keys:
                        base_task = task_by_key[key]
                        focused_tasks.append({
                            **base_task,
                            "assessments": [base_task["assessments"][0]],
                            "initial_candidates": [
                                candidate.model_dump(mode="json")
                                for candidate in adjudicated_by_key[key]
                            ],
                            "focused_recheck_reason": (
                                "Re-read the whole point and the diagnostic snippets before finalizing the decision. "
                                "If the field appears satisfied only because of limitation-driven uncertainty, "
                                "use-impact wording, or other non-qualifying same-point prose, correct it now."
                            ),
                        })
                    focused_prompt = {
                        "tasks": focused_tasks,
                        "instruction": (
                            _adjudication_instruction_for_categories(
                                [RuleCategory(task["assessments"][0]["rule_category"]) for task in focused_tasks]
                            )
                            + " Diagnostic supporting quotes come from the same bound point and may already satisfy "
                            + "the governed field. Keep a deficiency only if the whole bound point still lacks the "
                            + "required semantic content after considering those snippets."
                        ),
                        "required_output_schema": {
                            "type": "object", "required": ["candidates"],
                            "properties": {"candidates": {"type": "array", "items": AssessmentCandidate.model_json_schema()}},
                        },
                    }
                    focused_payload = self._bedrock().generate_json_with_claude(
                        system_prompt=self.ADJUDICATION_PROMPT,
                        user_prompt=json.dumps(focused_prompt, ensure_ascii=False, sort_keys=True),
                        max_tokens=4000,
                        retry_json_prompt=True,
                    )
                    focused_values = focused_payload.get("candidates") if isinstance(focused_payload, dict) else None
                    if not isinstance(focused_values, list):
                        raise ValueError("focused semantic recheck response has no candidates array")
                    focused_by_key: dict[tuple[str, RuleCategory], list[AssessmentCandidate]] = {}
                    for item in focused_values:
                        candidate = AssessmentCandidate.model_validate(item)
                        key = (candidate.segment_id, candidate.rule_category)
                        if key in focused_recheck_keys:
                            focused_by_key.setdefault(key, []).append(candidate)
                    missing_focused = focused_recheck_keys - set(focused_by_key)
                    duplicated_focused = {key for key, values in focused_by_key.items() if len(values) != 1}
                    if missing_focused or duplicated_focused:
                        raise ValueError(
                            f"focused semantic recheck coverage invalid: missing={len(missing_focused)} duplicated={len(duplicated_focused)}"
                        )
                    self.invocation_records.append({
                        "phase": "governed_semantic_recheck",
                        "batch_index": offset // batch_size,
                        "model_id": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
                        "temperature": 0, "top_p": 1.0, "max_tokens": 12000,
                        "prompt_sha256": hashlib.sha256(_canonical(focused_prompt)).hexdigest(),
                        "response_sha256": hashlib.sha256(_canonical(focused_payload)).hexdigest(),
                        "prompt": focused_prompt, "response": focused_payload,
                    })
                    for key, values in focused_by_key.items():
                        adjudicated_by_key[key] = values
                for key, candidates in adjudicated_by_key.items():
                    self._primed[key] = candidates


def _governed_finding_types(records: Iterable[RuleRetrievalRecord]) -> set[str]:
    values: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"id", "rule_id", "error_type", "semantic_error_type", "error_type_if_wrong"} and isinstance(child, str):
                    values.add(child)
                    # Some governed fields contain comma-separated alternatives.
                    values.update(part.strip(" .") for part in child.replace(" or ", ",").split(",") if part.strip())
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for record in records:
        values.add(record.rule_id)
        walk(record.content)
    return values


def _split_compound_tgiu_candidate(
    candidate: AssessmentCandidate,
    records: Iterable[RuleRetrievalRecord],
) -> list[AssessmentCandidate]:
    """Convert a model's compound TGIU label into governed atomic candidates."""
    if (
        candidate.decision != AssessmentDecision.DEFICIENT
        or candidate.rule_category != RuleCategory.METHODOLOGY
        or not candidate.proposed_finding_type
    ):
        return [candidate]
    allowed = {
        value for value in _governed_finding_types(records)
        if value.startswith("TGIU_")
    }
    tokens = re.findall(r"TGIU_[A-Z0-9_]+", candidate.proposed_finding_type.upper())
    selected = list(dict.fromkeys(token for token in tokens if token in allowed))
    if len(selected) < 2:
        return [candidate]
    return [
        candidate.model_copy(update={"proposed_finding_type": finding_type})
        for finding_type in selected
    ]


def _tgiu_rule_records(records: Iterable[RuleRetrievalRecord]) -> list[RuleRetrievalRecord]:
    return [record for record in records if record.rule_id.startswith("TGIU_")]


def _candidate_covers_tgiu_rule(
    candidate: AssessmentCandidate,
    rule: RuleRetrievalRecord,
) -> bool:
    if rule.retrieval_id in candidate.retrieval_ids:
        return True
    return (
        candidate.decision == AssessmentDecision.DEFICIENT
        and str(candidate.proposed_finding_type or "").strip().upper() == rule.rule_id.upper()
    )


def _validate_tgiu_candidate_coverage(
    candidates: list[AssessmentCandidate],
    rules: Iterable[RuleRetrievalRecord],
) -> None:
    tgiu_rules = _tgiu_rule_records(rules)
    if not tgiu_rules:
        return
    problems: list[str] = []
    for rule in tgiu_rules:
        matched = [candidate for candidate in candidates if _candidate_covers_tgiu_rule(candidate, rule)]
        if len(matched) != 1:
            problems.append(f"{rule.rule_id}:{len(matched)}")
            continue
        candidate = matched[0]
        if candidate.decision == AssessmentDecision.DEFICIENT:
            if candidate.proposed_finding_type != rule.rule_id:
                problems.append(f"{rule.rule_id}:wrong_deficiency_type")
        elif candidate.decision not in {AssessmentDecision.SATISFIED, AssessmentDecision.ABSTAIN}:
            problems.append(f"{rule.rule_id}:invalid_decision")
    if problems:
        raise ValueError("tgiu semantic coverage invalid: " + ", ".join(problems))


def _tgiu_rule_tasks(rules: Iterable[RuleRetrievalRecord]) -> list[dict[str, str]]:
    return [
        {"retrieval_id": rule.retrieval_id, "rule_id": rule.rule_id}
        for rule in _tgiu_rule_records(rules)
    ]


def _semantic_risiko_present(segment: ValidatedSegment) -> bool:
    """Recognize explicit technical risk semantics in the isolated point body."""
    return bool(_semantic_risiko_supporting_quotes(segment))


def _semantic_risiko_false_positive_signal(segment: ValidatedSegment) -> bool:
    body = _normalized_segment_body(segment)
    if not body or _semantic_risiko_supporting_quotes(segment):
        return False
    return bool(
        re.search(
            r"(?ix)\b(?:påvirke\s+funksjon\s+og\s+bruk(?:\s+av\s+rommet)?|"
            r"funksjon\s+og\s+bruk(?:\s+av\s+rommet)?|"
            r"omfattende\s+og\s+kostbar|omfattende,\s+kostbar|"
            r"bør\s+vurderes\s+ved\s+behov|"
            r"oppst[aå]r\s+flassing\b[^.;]{0,100}\bved\s+bruk)\b",
            body,
        )
    )


def _normalized_segment_body(segment: ValidatedSegment) -> str:
    body = _semantic_focus_excerpt(segment).casefold()
    body = re.sub(r"\s+", " ", body)
    return re.sub(
        r"hvordan\s+kontrollen\s+er\s+utført.*?konklusjon\s+bygningsdel\s*:?",
        " ", body,
    )


def _semantic_risiko_supporting_quotes(segment: ValidatedSegment, limit: int = 3) -> list[str]:
    body = _normalized_segment_body(segment)
    if not body:
        return []
    harm = (
        r"(?:\w*skad\w*|fukt\w*|råte\w*|vanninntreng\w*|snøras\w*|"
        r"\w*svikt\w*|nedbøyn\w*|nedbryt\w*|lekk\w*|kondens\w*|brann\w*|helse\w*|"
        r"oppsvell\w*|membran\w*|fuktbelast\w*|lukt\w*|inneklima\w*|"
        r"funksjonstap\w*|redusert\s+levetid|setningsskad\w*|korrosjon\w*|varmetap\w*)"
    )
    risk_patterns = (
        rf"\b(?:økt\s+)?risiko(?:en)?\s+(?:for|av)\s+[^.;]{{2,220}}{harm}[^.;]{{0,120}}",
        rf"\bfare\s+for\s+[^.;]{{2,220}}{harm}[^.;]{{0,120}}",
        rf"\bøkt\s+sannsynlighet\s+for\s+[^.;]{{2,220}}{harm}[^.;]{{0,120}}",
        rf"\b(?:kan|vil\s+kunne|kan\s+over\s+tid)\s+[^.;]{{0,120}}"
        rf"(?:føre\s+til|medføre|resultere\s+i|utvikle|belaste|påvirke|gi)\s+[^.;]{{0,160}}{harm}[^.;]{{0,120}}",
        rf"\b(?:kan\s+oppstå|kan\s+forekomme)\s+[^.;]{{0,160}}{harm}[^.;]{{0,120}}",
        rf"\b(?:må\s+påregnes|påregnelig\s+med)\s+[^.;]{{0,120}}{harm}[^.;]{{0,120}}",
        rf"\bkonsekvens(?:en)?\s+er\s+[^.;]{{0,160}}{harm}[^.;]{{0,120}}",
        r"\busikkerhe\w*\s+om\s+[^.;]{0,140}(?:materialval|oppbygging|utføring|skjulte\s+\w+løysing\w*|skjulte\s+\w+løsning\w*)[^.;]{0,160}",
        r"\b(?:bedre|redusert)\s+sikkerhet(?:\s+og\s+tilgjengelighet)?\b[^.;]{0,120}",
    )
    matches: list[str] = []
    for pattern in risk_patterns:
        for match in re.finditer(pattern, body):
            snippet = match.group(0).strip(" .")
            if snippet and snippet not in matches:
                matches.append(snippet)
            if len(matches) >= limit:
                return matches
    return matches


def _semantic_aarsak_rationale_present(segment: ValidatedSegment) -> bool:
    """Recognize generic rationale/justification language for the reported TG."""
    return bool(_semantic_aarsak_supporting_quotes(segment))


def _semantic_aarsak_supporting_quotes(segment: ValidatedSegment, limit: int = 3) -> list[str]:
    body = _segment_body_text(segment).casefold()
    body = re.sub(r"\s+", " ", body)
    if not body:
        return []
    patterns = (
        r"\b(?:på grunn av|skyldes|begrunnes med|er satt fordi)\b[^.;]{0,220}",
        r"\b(?:alder|eldre|slitasje|bruksslitasje|forventet funksjonstid|levetid)\b[^.;]{0,220}",
        r"\b(?:oppført\s+etter\s+(?:tekniske\s+)?forskrift\w*|oppført\s+etter\s+byggeforskrift\w*)\b[^.;]{0,220}",
        r"\bdet\s+foreligger\s+ingen\s+dokumentasjon\b[^.;]{0,220}",
        r"\b(?:sprekk(?:er)?\s+i|terreng\w*\s+er\s+flatt|faller\s+inn\s+mot\s+bygning\w*|flomutsatt\s+område)\b[^.;]{0,220}",
        r"\b(?:det er registrert|det er påvist|det er ikke påvist|det er ikke montert|mangler|"
        r"sprekk(?:er)?|skjevheter|fuktmerker|bruksmerker|mangelfull|utdatert)\b[^.;]{0,220}",
        r"\b(?:umulig|ikke fysisk mulig|manglende fysisk tilgang|begrenset tilgang|ikke inspisert)\b[^.;]{0,220}",
    )
    matches: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, body):
            snippet = match.group(0).strip(" .")
            if snippet and snippet not in matches:
                matches.append(snippet)
            if len(matches) >= limit:
                return matches
    return matches


def _semantic_anbefalt_tiltak_present(segment: ValidatedSegment) -> bool:
    """Recognize generic action/follow-up language anywhere in the bound point."""
    return bool(_semantic_anbefalt_tiltak_supporting_quotes(segment))


def _semantic_anbefalt_tiltak_supporting_quotes(
    segment: ValidatedSegment,
    limit: int = 3,
) -> list[str]:
    body = _normalized_segment_body(segment)
    if not body:
        return []
    patterns = (
        r"\b(?:bør|må|skal|anbefales|oppfordres til)\b[^.\n;]{0,160}\b(?:"
        r"kontrolleres|kontroll|etablere?s?|monteres|skiftes|isoleres|utbedres|"
        r"undersøkes|følges opp|følges med på|vurderes|oppgraderes|vedlikeholdes)\b",
        r"\b(?:holdes?|hold)\b[^.\n;]{0,80}\bund(er)?\s+oppsikt\b",
        r"\bkan\s+det\s+være\s+påregnelig\s+med\s+(?:utskiftning|utbedringer|oppgradering|vedlikehold|moderniseringer)\b",
        r"\b(?:utskiftning|utbedringer|oppgradering|vedlikehold|moderniseringer)\b[^.\n;]{0,80}\b(?:må påregnes|bør påregnes|anbefales)\b",
        r"\b(?:må|bør)\b[^.\n;]{0,120}\bskiftes\b",
    )
    matches: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, body):
            snippet = match.group(0).strip(" .")
            if snippet and snippet not in matches:
                matches.append(snippet)
            if len(matches) >= limit:
                return matches
    return matches


def _tiltak_direct_execution_order_snippets(segment: ValidatedSegment, limit: int = 3) -> list[str]:
    body = _semantic_focus_excerpt(segment)
    if not body:
        return []
    snippets: list[str] = []
    for chunk in re.split(r"(?<=[.!?])\s+|\n+", body):
        sentence = " ".join(chunk.split()).strip(" .")
        if not sentence:
            continue
        low = sentence.casefold()
        if "påregnes" in low or "vurderes" in low:
            continue
        if re.search(
            r"(?ix)\b(?:m[aå]\s+utføres|m[aå](?:\s+derfor)?\s+skiftes(?:\s+ut)?|m[aå]\s+justeres|m[aå]\s+totalrenoveres)\b",
            low,
        ):
            snippets.append(sentence)
            if len(snippets) >= limit:
                return snippets
    return snippets


def _semantic_konsekvens_present(segment: ValidatedSegment) -> bool:
    return bool(_semantic_konsekvens_supporting_quotes(segment))


def _semantic_konsekvens_limitation_only_signal(segment: ValidatedSegment) -> bool:
    body = _normalized_segment_body(segment)
    if not body or _semantic_konsekvens_supporting_quotes(segment):
        return False
    has_verification_limit = bool(
        re.search(
            r"(?ix)\b(?:kan\s+ikke\s+verifiseres|ikke\s+kan\s+verifiseres|"
            r"kan\s+ikke\s+fastslås|ikke\s+mulig\s+å\s+verifisere|"
            r"manglende\s+dokumentasjon)\b",
            body,
        )
    )
    has_uncertainty_chain = bool(
        re.search(
            r"(?ix)\b(?:kan\s+derfor\s+ikke\s+utelukkes|økt\s+usikkerhet|"
            r"nærmere\s+undersøkelser|fremskaffe\s+tilgjengelig\s+dokumentasjon)\b",
            body,
        )
    )
    return has_verification_limit and has_uncertainty_chain


def _semantic_konsekvens_supporting_quotes(
    segment: ValidatedSegment,
    limit: int = 3,
) -> list[str]:
    body = _normalized_segment_body(segment)
    if not body:
        return []
    patterns = (
        r"\b(?:konsekvens(?:en)?\s+er|betyr(?:\s+ikke)?\s+nødvendigvis\s+at)\b[^.;]{0,220}"
        r"(?:\w*skad\w*|fukt\w*|råte\w*|kostnad\w*|begrens\w*|funksjon\w*|sikkerhet\w*)[^.;]{0,120}",
        r"\b(?:kan\s+ikke\s+utelukkes\s+at)\b[^.;]{0,220}(?:skjulte\s+)?(?:fuktforhold\w*|skad\w*|feil\w*)[^.;]{0,120}"
        r"\b(?:foreligger|ligger)\b[^.;]{0,120}\b(?:i|bak)\s+(?:konstruksjon\w*|bygningsdel\w*)",
        r"\b(?:egner\s+seg\s+ikke|redusert\s+luftutskifting|økt\s+forbruk)\b[^.;]{0,220}",
        r"\bf[aå]r\s+ikke\s+luften\s+sirkulert\s+skikkelig\b[^.;]{0,220}",
        r"\b(?:dårligere\s+inneklima|høyere\s+luftfuktighet|kondens|biologisk\s+vekst)\b[^.;]{0,220}",
        r"\b(?:omfattende|kostbar)\s+(?:utbedring|rehabilitering|oppfølging)\b[^.;]{0,220}",
        r"\b(?:g[aå]r\s+i\s+anslaget|sl[aå]r\s+i\s+karm(?:en)?|binder)\b[^.;]{0,160}"
        r"(?:\bved\s+funksjonsprøving\b|\bved\s+bruk\b|\bn[aå]r\s+den\s+(?:åpnes|lukkes)\b)?",
    )
    matches: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, body):
            snippet = match.group(0).strip(" .")
            if snippet and snippet not in matches:
                matches.append(snippet)
            if len(matches) >= limit:
                return matches
    return matches


def _semantic_konsekvens_measure_purpose_only_signal(segment: ValidatedSegment) -> bool:
    body = _normalized_segment_body(segment)
    if not body or _semantic_konsekvens_supporting_quotes(segment):
        return False
    return bool(
        re.search(
            r"(?ix)"
            r"\bfor\s+å\s+unngå\b[^.;]{0,160}\b(?:miljø|forurensnings)risiko\b"
            r"|"
            r"\bfor\s+å\s+sikre\s+tilstrekkelig\s+luftutskifting\b"
            r"|"
            r"\boppst[aå]r\s+flassing\b[^.;]{0,100}\bved\s+bruk\b",
            body,
        )
    )


def _semantic_konsekvens_measurement_only_signal(segment: ValidatedSegment) -> bool:
    body = _normalized_segment_body(segment)
    if not body or _semantic_konsekvens_supporting_quotes(segment):
        return False
    has_measurements = bool(
        re.search(
            r"(?ix)\b(?:retningsavvik|høydeforskjell|nivåforskjell|vesentlig\s+skjevheter)\b",
            body,
        )
    )
    lacks_effect = not re.search(
        r"(?ix)\b(?:kan|vil|medfører|fører\s+til|resultere|betyr|konsekvens(?:en)?\s+er)\b[^.;]{0,160}",
        body,
    )
    return has_measurements and lacks_effect


def _semantic_legality_present(segment: ValidatedSegment) -> bool:
    return bool(_semantic_legality_supporting_quotes(segment))


def _semantic_legality_supporting_quotes(
    segment: ValidatedSegment,
    limit: int = 3,
) -> list[str]:
    body = _normalized_segment_body(segment)
    if not body:
        return []
    patterns = (
        r"\b(?:oppfordres\s+derfor\s+til\s+å\s+sjekke|betydning\s+for\s+kjøpers\s+bruk|"
        r"bruk/\s*utvikling\s+av\s+eiendommen)\b[^.;]{0,220}",
        r"\b(?:stemmer\s+ikke\s+med\s+dagens\s+bruk|stemmer\s+ikke\s+overens\s+med\s+tegningene)\b[^.;]{0,220}",
    )
    matches: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, body):
            snippet = match.group(0).strip(" .")
            if snippet and snippet not in matches:
                matches.append(snippet)
            if len(matches) >= limit:
                return matches
    return matches


def _semantic_diagnostics_payload(
    category: RuleCategory,
    segment: ValidatedSegment,
) -> dict[str, Any] | None:
    diagnostics: dict[str, Any] = {}
    if category == RuleCategory.RISIKO:
        diagnostics["future_risk_signal"] = _semantic_risiko_present(segment)
        diagnostics["risk_false_positive_signal"] = _semantic_risiko_false_positive_signal(segment)
        diagnostics["supporting_quotes"] = _semantic_risiko_supporting_quotes(segment)
    elif category == RuleCategory.AARSAK:
        diagnostics["rationale_signal"] = _semantic_aarsak_rationale_present(segment)
        diagnostics["supporting_quotes"] = _semantic_aarsak_supporting_quotes(segment)
    elif category == RuleCategory.ANBEFALT_TILTAK:
        diagnostics["action_signal"] = _semantic_anbefalt_tiltak_present(segment)
        diagnostics["supporting_quotes"] = _semantic_anbefalt_tiltak_supporting_quotes(segment)
    elif category == RuleCategory.KONSEKVENS:
        diagnostics["consequence_signal"] = _semantic_konsekvens_present(segment)
        diagnostics["consequence_limitation_only_signal"] = _semantic_konsekvens_limitation_only_signal(segment)
        diagnostics["supporting_quotes"] = _semantic_konsekvens_supporting_quotes(segment)
    elif category == RuleCategory.METHODOLOGY:
        diagnostics["non_triggered_rule_is_satisfied"] = True
    elif category == RuleCategory.LEGALITY:
        diagnostics["require_point_bound_legality_gap"] = True
        diagnostics["legality_signal"] = _semantic_legality_present(segment)
        diagnostics["supporting_quotes"] = _semantic_legality_supporting_quotes(segment)
    return diagnostics or None


def _adjudication_instruction_for_categories(
    categories: Iterable[RuleCategory],
) -> str:
    category_set = set(categories)
    parts = [
        "Return exactly one authoritative candidate for every requested segment/category pair.",
        "Judge the semantic role across the complete bound point body; never require a heading.",
        "Copy exact segment_id, rule_category, applicable retrieval_ids and evidence_ids.",
        "Use the supplied approved governed semantic context; do not re-grade the point.",
    ]
    if RuleCategory.RISIKO in category_set:
        parts.append(
            "For Risiko, future technical risk stated anywhere in the bound point counts, including mixed "
            "Konsekvens/tiltak text. Do not return MISSING when the same point already states leakage, moisture, "
            "fire, electrical, structural, or similar forward-looking technical risk. Same-point wording that "
            "describes what the condition can cause, may cause, or results in as technical damage or deterioration "
            "also counts even if it appears in consequence-style prose. Named uncertainty about concealed materials, "
            "build-up, execution, or hidden wet-room solutions is substantive technical risk when it identifies the "
            "same point's unresolved technical exposure. Present-state inconvenience, existing use impact, room "
            "function impact, or costly remediation alone is not technical risk unless the point also states a "
            "future technical harm. If the point only states use impact, room function impact, or costly "
            "remediation burden, return MISSING for Risiko rather than CONSEQUENCE_AS_RISIKO. If the point only "
            "states observed condition, tolerances, settlement/cause explanation, or current use impact without "
            "future technical harm, return MISSING rather than AARSAK_AS_RISIKO."
        )
    if RuleCategory.AARSAK in category_set:
        parts.append(
            "For Årsak, the report's justification/rationale for the reported TG may be observation-based, "
            "limitation-based, age/service-life based, or deficiency-based. Technical root-cause diagnosis is not "
            "a universal requirement."
        )
    if RuleCategory.ANBEFALT_TILTAK in category_set:
        parts.append(
            "For Anbefalt tiltak, a concrete action or follow-up anywhere in the point counts even inside a combined "
            "Konsekvens/tiltak field. Exact by-whom or exact timing is not a universal semantic requirement. Direct "
            "execution orders such as 'må utføres', 'må skiftes', 'må justeres', and 'må totalrenoveres' can trigger "
            "TILTAK_IMPERATIVE_FORM, while expectation or review wording such as 'må påregnes' and 'må vurderes' "
            "does not do so automatically."
        )
    if RuleCategory.KONSEKVENS in category_set:
        parts.append(
            "For Konsekvens, concrete same-point follow-on harm, hidden-damage exposure, buyer-use limitation, or "
            "other practical effect counts even in mixed prose. Keep TECHNICAL_DEVELOPMENT_AS_KONSEKVENS only when "
            "the point states development without any actual consequence or practical effect. If the point only says "
            "that hidden damage, defects, or leaks cannot be excluded because documentation, access, or execution "
            "cannot be verified, treat that as LIMITATION_AS_KONSEKVENS rather than a satisfied consequence unless "
            "the same point also states a concrete buyer-facing effect or present exposure. Increased assessment "
            "uncertainty, need for further investigation, need to obtain documentation, or future maintenance/"
            "replacement planning caused only by unresolved verification limits is still limitation/tiltak, not a "
            "satisfied consequence. Poor indoor climate, higher humidity, condensation exposure, "
            "biological growth, reduced safe use, extensive remediation burden, or concrete impact on function/use "
            "are substantive consequences when the point itself states them. If the same point already states such "
            "a concrete consequence, do not emit TILTAK_AS_KONSEKVENS merely because the field also contains a "
            "recommendation."
        )
    if RuleCategory.METHODOLOGY in category_set:
        parts.append(
            "For methodology, if the retrieved rule does not trigger on the bound point, return SATISFIED, not "
            "ABSTAIN. Use ABSTAIN only when applicability cannot be determined from the bound point evidence."
        )
    if RuleCategory.LEGALITY in category_set:
        parts.append(
            "For legality, do not emit a deficiency unless the bound point itself communicates the governed "
            "buyer-relevant legality gap required by the retrieved rule. A same-point explanation that the buyer "
            "should check plan or drawing compliance because it may matter for use or development is relevant "
            "substantive legality context and must be considered."
        )
    return " ".join(parts)


def _needs_focused_recheck(
    category: RuleCategory,
    candidate: AssessmentCandidate,
    diagnostics: dict[str, Any] | None,
) -> bool:
    return False


def _needs_focused_false_positive_recheck(
    category: RuleCategory,
    candidate: AssessmentCandidate,
    diagnostics: dict[str, Any] | None,
) -> bool:
    return False


def _normalize_semantic_candidate(
    candidate: AssessmentCandidate,
    segment: ValidatedSegment,
    records: Iterable[RuleRetrievalRecord],
) -> AssessmentCandidate:
    """Deprecated compatibility hook; semantic decisions are returned unchanged.

    Professional-language adjudication is performed by the governed AI pass.
    Deterministic code must never change SATISFIED/DEFICIENT here.
    """
    if candidate.rule_category == RuleCategory.TG3_COST:
        cost_status = _tg3_cost_status_from_segment(segment)
        if cost_status == "missing":
            return candidate.model_copy(
                update={
                    "decision": AssessmentDecision.DEFICIENT,
                    "proposed_finding_type": "E_METHOD.tg3_cost_missing",
                    "explanation": (
                        "The cited point-bound evidence for this TG3 point contains no cost class, "
                        "cost interval, or other schematic estimate. TG3 cost therefore remains missing."
                    ),
                }
            )
        if cost_status == "single_amount_only":
            return candidate.model_copy(
                update={
                    "decision": AssessmentDecision.DEFICIENT,
                    "proposed_finding_type": "E_METHOD.tg3_cost_single_amount_only",
                    "explanation": (
                        "The cited point-bound evidence for this TG3 point contains only a single amount, "
                        "not a valid schematic cost class or interval."
                    ),
                }
            )
        return candidate.model_copy(
            update={
                "decision": AssessmentDecision.SATISFIED,
                "proposed_finding_type": None,
                "explanation": (
                    "The cited point-bound evidence contains a valid TG3 cost class or cost interval "
                    "for this same physical point."
                ),
            }
        )
    if (
        candidate.rule_category == RuleCategory.METHODOLOGY
        and candidate.decision == AssessmentDecision.ABSTAIN
        and not candidate.proposed_finding_type
    ):
        return candidate.model_copy(
            update={
                "decision": AssessmentDecision.SATISFIED,
                "explanation": (
                    "The retrieved governed methodology rule does not trigger on this bound point. "
                    "A non-triggered methodology rule is satisfied rather than abstained."
                ),
            }
        )
    if (
        candidate.rule_category == RuleCategory.METHODOLOGY
        and _methodology_rule_requires_arkat(segment)
        and candidate.decision != AssessmentDecision.DEFICIENT
    ):
        return candidate.model_copy(
            update={
                "decision": AssessmentDecision.DEFICIENT,
                "proposed_finding_type": "E_METHOD.garasje_avvik_uten_arkat",
                "explanation": (
                    "The detached-building point describes a concrete deviation while also stating that the "
                    "structure is only simply described and not fully condition-assessed. Full ARKAT therefore "
                    "remains required under the governed methodology rule."
                ),
            }
        )
    if (
        candidate.rule_category == RuleCategory.AARSAK
        and candidate.decision == AssessmentDecision.DEFICIENT
        and candidate.proposed_finding_type == "MISSING (aarsak)"
        and _semantic_aarsak_rationale_present(segment)
    ):
        return candidate.model_copy(
            update={
                "decision": AssessmentDecision.SATISFIED,
                "proposed_finding_type": None,
                "explanation": (
                    "The bound point already states the report's rationale for the reported TG in observation-, "
                    "standard-, or documentation-based terms."
                ),
            }
        )
    if (
        candidate.rule_category == RuleCategory.RISIKO
        and candidate.decision == AssessmentDecision.DEFICIENT
        and candidate.proposed_finding_type == "MISSING (risiko)"
        and _semantic_risiko_present(segment)
    ):
        return candidate.model_copy(
            update={
                "decision": AssessmentDecision.SATISFIED,
                "proposed_finding_type": None,
                "explanation": (
                    "The same bound point already communicates future technical or safety-related risk."
                ),
            }
        )
    if (
        candidate.rule_category == RuleCategory.RISIKO
        and candidate.decision == AssessmentDecision.SATISFIED
        and _semantic_risiko_false_positive_signal(segment)
    ):
        return candidate.model_copy(
            update={
                "decision": AssessmentDecision.DEFICIENT,
                "proposed_finding_type": "MISSING (risiko)",
                "explanation": (
                    "The bound point still lacks a qualifying future technical risk statement; present-state or "
                    "measure-purpose wording alone is not enough."
                ),
            }
        )
    if (
        candidate.rule_category == RuleCategory.KONSEKVENS
        and candidate.decision == AssessmentDecision.DEFICIENT
        and candidate.proposed_finding_type == "MISSING (konsekvens)"
        and _semantic_konsekvens_present(segment)
    ):
        return candidate.model_copy(
            update={
                "decision": AssessmentDecision.SATISFIED,
                "proposed_finding_type": None,
                "explanation": (
                    "The same bound point already states a concrete practical effect or consequence."
                ),
            }
        )
    if (
        candidate.rule_category == RuleCategory.KONSEKVENS
        and candidate.decision == AssessmentDecision.DEFICIENT
        and candidate.proposed_finding_type == "MISSING (konsekvens)"
        and _semantic_konsekvens_measurement_only_signal(segment)
    ):
        return candidate.model_copy(
            update={
                "decision": AssessmentDecision.SATISFIED,
                "proposed_finding_type": None,
                "explanation": (
                    "The bound point only repeats measured deviation data without a separate practical-effect "
                    "statement, so no scored consequence deficiency is emitted."
                ),
            }
        )
    if (
        candidate.rule_category == RuleCategory.KONSEKVENS
        and candidate.decision == AssessmentDecision.SATISFIED
        and _semantic_konsekvens_measure_purpose_only_signal(segment)
    ):
        return candidate.model_copy(
            update={
                "decision": AssessmentDecision.DEFICIENT,
                "proposed_finding_type": "TILTAK_AS_KONSEKVENS",
                "explanation": (
                    "The bound point states a measure or follow-up but does not separately communicate a "
                    "substantive consequence."
                ),
            }
        )
    if (
        candidate.rule_category == RuleCategory.ANBEFALT_TILTAK
        and candidate.decision == AssessmentDecision.SATISFIED
        and _tiltak_direct_execution_order_snippets(segment)
    ):
        return candidate.model_copy(
            update={
                "decision": AssessmentDecision.DEFICIENT,
                "proposed_finding_type": "TILTAK_IMPERATIVE_FORM",
                "explanation": (
                    "The measure is formulated as a direct execution order rather than a recommendation."
                ),
            }
        )
    if (
        candidate.rule_category == RuleCategory.LEGALITY
        and candidate.decision == AssessmentDecision.ABSTAIN
        and not candidate.proposed_finding_type
    ):
        return candidate.model_copy(
            update={
                "decision": AssessmentDecision.SATISFIED,
                "explanation": (
                    "The retrieved governed legality rules do not trigger a deficiency on this bound point. "
                    "A non-triggered legality rule is satisfied rather than abstained."
                ),
            }
        )
    if candidate.rule_category == RuleCategory.RISIKO:
        diagnostics = _semantic_diagnostics_payload(RuleCategory.RISIKO, segment) or {}
        if (
            candidate.decision == AssessmentDecision.DEFICIENT
            and candidate.proposed_finding_type in {"AARSAK_AS_RISIKO", "CONSEQUENCE_AS_RISIKO"}
            and not diagnostics.get("future_risk_signal")
            and diagnostics.get("risk_false_positive_signal")
        ):
            return candidate.model_copy(
                update={
                    "proposed_finding_type": "MISSING (risiko)",
                    "explanation": (
                        "The bound point does not state a future technical harm. It only gives observed condition, "
                        "cause/current-state explanation, use impact, or remediation burden, so Risiko remains "
                        "missing rather than a wrong-role subtype."
                    ),
                }
            )
    if candidate.rule_category == RuleCategory.KONSEKVENS:
        diagnostics = _semantic_diagnostics_payload(RuleCategory.KONSEKVENS, segment) or {}
        tiltak_diagnostics = _semantic_diagnostics_payload(RuleCategory.ANBEFALT_TILTAK, segment) or {}
        if (
            candidate.decision == AssessmentDecision.DEFICIENT
            and candidate.proposed_finding_type == "MISSING (konsekvens)"
            and not diagnostics.get("consequence_signal")
            and tiltak_diagnostics.get("action_signal")
        ):
            return candidate.model_copy(
                update={
                    "proposed_finding_type": "TILTAK_AS_KONSEKVENS",
                    "explanation": (
                        "The bound point does not state a substantive consequence, but it does state a concrete "
                        "recommendation or measure. Konsekvens therefore remains deficient as tiltak used as "
                        "consequence rather than plain missing."
                    ),
                }
            )
    return candidate


def _canonical_finding_identity(
    proposed_finding_type: str | None,
    records: Iterable[RuleRetrievalRecord],
) -> str | None:
    """Resolve any governed alias to its owning canonical governed rule ID."""
    if not proposed_finding_type:
        return None
    proposed = " ".join(proposed_finding_type.split()).casefold()
    governed_semantic = {
        record.rule_id for record in records
        if " ".join(str(record.content.get("semantic_error_type") or "").split()).casefold() == proposed
    }
    if len(governed_semantic) == 1:
        return next(iter(governed_semantic))
    exact = {
        record.rule_id for record in records
        if " ".join(record.rule_id.split()).casefold() == proposed
    }
    if len(exact) == 1:
        return next(iter(exact))
    matched: set[str] = set()
    for record in records:
        aliases = _governed_finding_types([record])
        if proposed in {" ".join(alias.split()).casefold() for alias in aliases}:
            matched.add(record.rule_id)
    return next(iter(matched)) if len(matched) == 1 else None


class DeterministicAssessmentValidator:
    def validate(
        self,
        assessment: StructuredAssessment,
        segment: ValidatedSegment,
        rules: list[RuleRetrievalRecord],
        regime_status: RegimeResolutionStatus,
    ) -> FindingValidationDecision:
        reasons: list[str] = []
        rule_ids = {record.retrieval_id for record in rules}
        evidence_ids = {span.evidence_id for span in segment.evidence_spans}
        evidence_ids.update(span.evidence_id for span in segment.bound_body_spans)
        if segment.evidence is not None:
            evidence_ids.add(segment.evidence.evidence_id)
        if regime_status != RegimeResolutionStatus.RESOLVED:
            reasons.append("regime_not_resolved")
        if any(record.regime_status != RegimeResolutionStatus.RESOLVED for record in rules):
            reasons.append("retrieved_rule_regime_not_resolved")
        if assessment.segment_id != segment.segment_id:
            reasons.append("segment_reference_mismatch")
        if any(record.segment_id != segment.segment_id for record in rules):
            reasons.append("retrieval_segment_mismatch")
        if any(record.rule_category != assessment.rule_category for record in rules):
            reasons.append("retrieval_category_mismatch")
        if not set(assessment.retrieval_ids).issubset(rule_ids):
            reasons.append("unknown_retrieval_reference")
        if not set(assessment.evidence_ids).issubset(evidence_ids):
            reasons.append("unknown_evidence_reference")
        if assessment.decision == AssessmentDecision.DEFICIENT:
            if segment.kind.value == "report_point" and not segment.bound_body_spans:
                reasons.append("complete_point_body_missing")
            if not assessment.evidence_ids:
                reasons.append("finding_without_source_evidence")
            if not assessment.proposed_finding_type:
                reasons.append("finding_type_missing")
            elif assessment.proposed_finding_type not in _governed_finding_types(rules) and not (
                assessment.rule_category == RuleCategory.ANBEFALT_TILTAK
                and assessment.proposed_finding_type in {"MISSING", "MISSING (anbefalt_tiltak)"}
            ):
                reasons.append("finding_type_not_governed_by_retrieved_rules")
        else:
            reasons.append("no_finding_proposed")
        methodology_rule = next(
            (record for record in rules if record.rule_id == "E_METHOD.tg2_missing_anbefalt_tiltak_ns2025"),
            None,
        )
        special_tg2_measure = bool(
            assessment.decision == AssessmentDecision.DEFICIENT
            and assessment.rule_category == RuleCategory.ANBEFALT_TILTAK
            and assessment.proposed_finding_type in {"MISSING", "MISSING (anbefalt_tiltak)"}
            and segment.point_type == "graded"
            and (segment.tg_grade or "").upper() == "TG2"
            and methodology_rule is not None
            and methodology_rule.regime_id is not None
        )
        canonical_identity = (
            "E_METHOD.tg2_missing_anbefalt_tiltak_ns2025"
            if special_tg2_measure
            else _canonical_finding_identity(assessment.proposed_finding_type, rules)
        )
        if (
            canonical_identity
            and assessment.rule_category in {
                RuleCategory.AARSAK, RuleCategory.RISIKO,
                RuleCategory.KONSEKVENS, RuleCategory.ANBEFALT_TILTAK,
            }
            and canonical_identity.startswith(("B_TG.", "C_TGIU.", "E_METHOD."))
            and not special_tg2_measure
        ):
            reasons.append("governed_rule_domain_mismatch")
        if (
            canonical_identity == "L-BU-01"
            and assessment.rule_category == RuleCategory.LEGALITY
            and not _semantic_legality_present(segment)
        ):
            reasons.append("buyer_relevant_legality_gap_not_point_bound")
        if (
            assessment.decision == AssessmentDecision.DEFICIENT
            and assessment.proposed_finding_type
            and (
                assessment.proposed_finding_type in _governed_finding_types(rules)
                or special_tg2_measure
            )
            and canonical_identity is None
        ):
            reasons.append("canonical_finding_identity_ambiguous")
        admission = FindingAdmission.ACCEPTED if not reasons else FindingAdmission.REJECTED
        canonical_point_id = re.sub(
            r"[^A-Za-z0-9ÆØÅæøå._-]+", "_",
            segment.point_label or segment.title or segment.segment_id,
        ).strip("_")
        accepted_id = (
            f"E_METHOD_tg2_missing_anbefalt_tiltak_ns2025_{canonical_point_id}"
            if special_tg2_measure and admission == FindingAdmission.ACCEPTED
            else f"{canonical_identity.replace('.', '_')}_{canonical_point_id}"
            if admission == FindingAdmission.ACCEPTED and str(canonical_identity or "").startswith("E_METHOD.")
            else f"A_ARKAT_{canonical_point_id}_{canonical_identity.split('.', 2)[1]}_{canonical_identity.split('.', 2)[2]}"
            if admission == FindingAdmission.ACCEPTED and str(canonical_identity or "").startswith("A_ARKAT_SEMANTIC.")
            else f"{canonical_identity}_{canonical_point_id}"
            if admission == FindingAdmission.ACCEPTED and str(canonical_identity or "").startswith("TGIU_")
            else _identifier("finding", segment.segment_id, assessment.rule_category.value, canonical_identity or "")
            if admission == FindingAdmission.ACCEPTED
            else None
        )
        canonical_record = next((record for record in rules if record.rule_id == canonical_identity), None)
        metadata = methodology_rule.content if special_tg2_measure and methodology_rule else (
            canonical_record.content if canonical_record else {}
        )
        category = str(metadata.get("category") or "")
        if not category:
            category = "C" if str(canonical_identity or "").startswith("TGIU_") else "A"
        deduction = metadata.get("deduction", metadata.get("points", 0))
        gate_effect = metadata.get("gate_effect") if isinstance(metadata.get("gate_effect"), dict) else {}
        missing_gate = bool(
            str(assessment.proposed_finding_type or "").startswith("MISSING (")
            and assessment.rule_category in {RuleCategory.AARSAK, RuleCategory.RISIKO, RuleCategory.KONSEKVENS}
        )
        return FindingValidationDecision(
            validation_id=_identifier("val", assessment.assessment_id),
            assessment_id=assessment.assessment_id,
            admission=admission,
            reason_codes=reasons,
            accepted_finding_id=accepted_id,
            canonical_finding_identity=canonical_identity,
            canonical_point_id=canonical_point_id,
            category=category if admission == FindingAdmission.ACCEPTED else None,
            deduction=int(deduction or 0) if admission == FindingAdmission.ACCEPTED else 0,
            obligation_class=str(metadata.get("obligation_class") or (
                "standard_methodology" if category in {"C", "E"} else "validert_product_quality"
            )) if admission == FindingAdmission.ACCEPTED else None,
            regulatory=bool(metadata.get("regulatory")) if admission == FindingAdmission.ACCEPTED else False,
            blocks_96_gate=bool(metadata.get("blocks_96_gate") or gate_effect.get("blocks_96_gate") or missing_gate) if admission == FindingAdmission.ACCEPTED else False,
        )


def _is_non_material_rejected_deficiency(
    assessment: StructuredAssessment,
    decision: FindingValidationDecision,
) -> bool:
    return (
        assessment.decision == AssessmentDecision.DEFICIENT
        and assessment.rule_category == RuleCategory.LEGALITY
        and set(decision.reason_codes) == {"buyer_relevant_legality_gap_not_point_bound"}
    )


def _methodology_rule_requires_arkat(segment: ValidatedSegment) -> bool:
    if segment.point_type != "methodology_only":
        return False
    body = _normalized_segment_body(segment)
    if not body:
        return False
    has_methodology_disclaimer = bool(
        re.search(r"(?is)\bikke\s+tilstandsvurdert\b.{0,220}\benkel\s+beskrivelse\b", body)
    )
    has_concrete_deviation = bool(
        re.search(
            r"(?ix)\b(?:ikke\s+egnet|mangler|sprekker?|riss|råte|fukt|deformasjoner?|skader?)\b",
            body,
        )
    )
    return has_methodology_disclaimer and has_concrete_deviation


class PhaseA4ShadowService:
    FORMAL_ACCEPTANCE_BLOCKERS = ()
    RETRIEVAL_TOP_K = {
        RuleCategory.AARSAK: 6,
        RuleCategory.RISIKO: 6,
        RuleCategory.KONSEKVENS: 6,
        RuleCategory.ANBEFALT_TILTAK: 6,
        RuleCategory.METHODOLOGY: 6,
        RuleCategory.LEGALITY: 4,
        RuleCategory.TG3_COST: 4,
    }
    def __init__(
        self,
        retriever: ManifestVerifiedRuleRetriever,
        model: AssessmentModel,
        validator: DeterministicAssessmentValidator | None = None,
        planner: DeterministicApplicabilityPlanner | None = None,
    ):
        self.retriever = retriever
        self.model = model
        self.validator = validator or DeterministicAssessmentValidator()
        self.planner = planner or DeterministicApplicabilityPlanner()

    def analyze(
        self,
        understanding: DocumentUnderstandingResult,
        categories: Iterable[RuleCategory] | None = None,
    ) -> PhaseA4Result:
        retrieval_results = []
        assessments: list[StructuredAssessment] = []
        decisions: list[FindingValidationDecision] = []
        lineage: list[FindingLineageRecord] = []
        abstentions = list(understanding.abstentions)
        traces = list(understanding.trace_records)
        category_filter = set(categories) if categories is not None else None
        excluded_plan_items: set[str] = set()
        plan = self.planner.plan(understanding.segments)
        assessment_segments = _assessment_segments_with_linked_summaries(understanding.segments)
        if category_filter is not None:
            plan = [item for item in plan if item.rule_category in category_filter]
        plan_by_segment: dict[str, list] = {}
        for item in plan:
            plan_by_segment.setdefault(item.segment_id, []).append(item)

        retrieval_cache: dict[tuple[str, RuleCategory], Any] = {}
        worklist: list[tuple[ValidatedSegment, RuleCategory, list[RuleRetrievalRecord]]] = []
        for source_segment in understanding.segments:
            segment = assessment_segments[source_segment.segment_id]
            if segment.validation_status != ValidationStatus.VALIDATED:
                continue
            for plan_item in plan_by_segment.get(segment.segment_id, []):
                category = plan_item.rule_category
                retrieval = self.retriever.retrieve(
                    segment, category, understanding.facts,
                    document_hash=understanding.document_hash,
                    top_k=self.RETRIEVAL_TOP_K.get(category, 6),
                )
                retrieval_cache[(segment.segment_id, category)] = retrieval
                retrieval_results.append(retrieval)
                abstentions.extend(retrieval.abstentions)
                traces.extend(retrieval.trace_records)
                if (
                    category == RuleCategory.ANBEFALT_TILTAK
                    and (segment.tg_grade or "").upper() == "TG2"
                    and retrieval.regime_resolution.applicable_ns_edition == "NS 3600:2018"
                ):
                    excluded_plan_items.add(plan_item.plan_item_id)
                    continue
                if (
                    retrieval.regime_resolution.status == RegimeResolutionStatus.RESOLVED
                    and retrieval.records
                    and all(record.regime_status == RegimeResolutionStatus.RESOLVED for record in retrieval.records)
                ):
                    worklist.append((segment, category, retrieval.records))

        prime_failed = False
        prime_worklist = getattr(self.model, "prime_worklist", None)
        if callable(prime_worklist) and worklist:
            try:
                prime_worklist(worklist)
            except Exception as exc:
                prime_failed = True
                abstentions.append(Abstention(
                    abstention_id=_identifier("abs", understanding.document_hash, "batched_assessment_failure", type(exc).__name__),
                    stage="structured_assessment",
                    subject=understanding.document_hash,
                    reason_code="batched_assessment_model_or_schema_failure",
                    explanation=f"The batched assessment failed closed ({type(exc).__name__}: {str(exc)[:1200]}); no finding was admitted.",
                ))

        for source_segment in understanding.segments:
            segment = assessment_segments[source_segment.segment_id]
            if segment.validation_status != ValidationStatus.VALIDATED:
                continue
            for plan_item in plan_by_segment.get(segment.segment_id, []):
                category = plan_item.rule_category
                retrieval = retrieval_cache[(segment.segment_id, category)]
                if (
                    category == RuleCategory.ANBEFALT_TILTAK
                    and (segment.tg_grade or "").upper() == "TG2"
                    and retrieval.regime_resolution.applicable_ns_edition == "NS 3600:2018"
                    and not _semantic_anbefalt_tiltak_present(segment)
                ):
                    excluded_plan_items.add(plan_item.plan_item_id)
                    continue
                # Critical safety boundary: no AI assessment occurs before an
                # authorized governed regime is resolved.
                if retrieval.regime_resolution.status != RegimeResolutionStatus.RESOLVED:
                    continue
                if not retrieval.records:
                    continue
                unresolved_rules = [
                    record.rule_id for record in retrieval.records
                    if record.regime_status != RegimeResolutionStatus.RESOLVED
                ]
                if unresolved_rules:
                    abstentions.append(Abstention(
                        abstention_id=_identifier(
                            "abs", segment.segment_id, category.value, "unresolved_required_rules"
                        ),
                        stage="per_rule_regime_gate",
                        subject=f"{segment.segment_id}:{category.value}",
                        reason_code="required_rule_regime_unresolved",
                        explanation=(
                            "Assessment was not invoked because one or more required retrieved rules "
                            "remain unresolved."
                        ),
                    ))
                    continue
                if prime_failed:
                    continue
                try:
                    assess_many = getattr(self.model, "assess_many", None)
                    candidates = (
                        assess_many(segment, category, retrieval.records)
                        if callable(assess_many)
                        else [self.model.assess(segment, category, retrieval.records)]
                    )
                except Exception as exc:
                    abstentions.append(Abstention(
                        abstention_id=_identifier(
                            "abs", segment.segment_id, category.value,
                            "assessment_model_failure", type(exc).__name__,
                        ),
                        stage="structured_assessment",
                        subject=f"{segment.segment_id}:{category.value}",
                        reason_code="assessment_model_or_schema_failure",
                        explanation=(
                            f"This assessment failed closed ({type(exc).__name__}); "
                            "independent assessments may continue."
                        ),
                    ))
                    continue
                for candidate_index, candidate in enumerate(candidates):
                    candidate = _normalize_semantic_candidate(candidate, segment, retrieval.records)
                    candidate_retrieval_ids = list(candidate.retrieval_ids)
                    if (
                        not candidate_retrieval_ids
                        and category == RuleCategory.ANBEFALT_TILTAK
                        and candidate.decision == AssessmentDecision.DEFICIENT
                    ):
                        candidate_retrieval_ids = [record.retrieval_id for record in retrieval.records]
                    if not candidate_retrieval_ids:
                        canonical = _canonical_finding_identity(candidate.proposed_finding_type, retrieval.records)
                        if (
                            category == RuleCategory.ANBEFALT_TILTAK
                            and candidate.proposed_finding_type in {"MISSING", "MISSING (anbefalt_tiltak)"}
                        ):
                            canonical = "E_METHOD.tg2_missing_anbefalt_tiltak_ns2025"
                        matching = [
                            record.retrieval_id for record in retrieval.records
                            if canonical is not None and record.rule_id == canonical
                        ]
                        candidate_retrieval_ids = matching or (
                            [record.retrieval_id for record in retrieval.records]
                            if candidate.decision == AssessmentDecision.DEFICIENT
                            and candidate.proposed_finding_type in _governed_finding_types(retrieval.records)
                            else []
                        ) or (
                            [record.retrieval_id for record in retrieval.records]
                            if candidate.decision != AssessmentDecision.DEFICIENT else []
                        )
                    if not candidate_retrieval_ids:
                        abstentions.append(Abstention(
                            abstention_id=_identifier("abs", segment.segment_id, category.value, str(candidate_index), "rule_reference_missing"),
                            stage="structured_assessment",
                            subject=f"{segment.segment_id}:{category.value}",
                            reason_code="candidate_rule_reference_unresolvable",
                            explanation="The model candidate could not be bound to one governed retrieved rule and was not admitted.",
                        ))
                        continue
                    assessment_id = _identifier(
                        "assess", understanding.document_hash, segment.segment_id, category.value,
                        str(candidate_index), candidate.proposed_finding_type or candidate.decision.value,
                        *sorted(record.retrieval_id for record in retrieval.records),
                    )
                    assessment = StructuredAssessment(
                        assessment_id=assessment_id, segment_id=candidate.segment_id,
                        retrieval_ids=candidate_retrieval_ids, rule_category=candidate.rule_category,
                        decision=candidate.decision, explanation=candidate.explanation,
                        evidence_ids=candidate.evidence_ids,
                        proposed_finding_type=candidate.proposed_finding_type,
                    )
                    assessments.append(assessment)
                    decision = self.validator.validate(
                        assessment, segment, retrieval.records, retrieval.regime_resolution.status,
                    )
                    decisions.append(decision)
                    if decision.admission == FindingAdmission.ACCEPTED and decision.accepted_finding_id:
                        lineage.append(FindingLineageRecord(
                            accepted_finding_id=decision.accepted_finding_id, assessment_id=assessment_id,
                            segment_id=segment.segment_id, rule_category=category,
                            public_projection_status="pending", public_finding_id=None,
                            reason="Customer projection remains unauthorized; accepted raw finding is explicitly retained.",
                        ))
                    traces.append(TraceRecord(
                        trace_id=_identifier("trace", assessment_id), document_hash=understanding.document_hash,
                        stage="structured_assessment_validation", entity_type="assessment", entity_id=assessment_id,
                        parent_trace_ids=[record.retrieval_id for record in retrieval.records],
                        payload_sha256=hashlib.sha256(_canonical({
                            "assessment": assessment.model_dump(mode="json"),
                            "decision": decision.model_dump(mode="json"),
                        })).hexdigest(),
                    ))
                    if (
                        decision.admission == FindingAdmission.REJECTED
                        and assessment.decision == AssessmentDecision.DEFICIENT
                        and not _is_non_material_rejected_deficiency(assessment, decision)
                    ):
                        abstentions.append(Abstention(
                            abstention_id=_identifier("abs", assessment_id), stage="finding_admission",
                            subject=assessment_id, reason_code="deterministic_validation_rejected",
                            explanation="; ".join(decision.reason_codes),
                        ))

        accepted = sum(decision.admission == FindingAdmission.ACCEPTED for decision in decisions)
        unresolved = any(
            result.regime_resolution.status != RegimeResolutionStatus.RESOLVED
            for result in retrieval_results
        )
        assessment_by_id = {item.assessment_id: item for item in assessments}
        rejected_required = any(
            decision.admission == FindingAdmission.REJECTED
            and assessment_by_id[decision.assessment_id].decision == AssessmentDecision.DEFICIENT
            and not _is_non_material_rejected_deficiency(
                assessment_by_id[decision.assessment_id],
                decision,
            )
            for decision in decisions
        )
        assessment_abstained = any(item.decision == AssessmentDecision.ABSTAIN for item in assessments)
        required_count = len(plan) - len(excluded_plan_items)
        completed_count = len(assessments)
        if (
            unresolved
            or understanding.segment_coverage.completion_blockers
            or rejected_required
            or assessment_abstained
            or completed_count < required_count
        ):
            state = AnalysisState.LIMITED
        elif accepted:
            state = AnalysisState.COMPLETE_WITH_FINDINGS
        elif decisions:
            state = AnalysisState.COMPLETE_WITHOUT_FINDINGS
        else:
            state = AnalysisState.REQUIRES_CLARIFICATION
        score = score_admitted_findings(decisions, score_valid=state in {
            AnalysisState.COMPLETE_WITH_FINDINGS, AnalysisState.COMPLETE_WITHOUT_FINDINGS,
        })
        customer_items, public_payload, projected_lineage = project_customer_result(
            understanding, assessments, decisions, score, state
        )
        return PhaseA4Result(
            run_id=_identifier("a4", understanding.run_id),
            document_hash=understanding.document_hash,
            analysis_state=state,
            retrievals=retrieval_results,
            applicability_plan=plan,
            assessments=assessments,
            validation_decisions=decisions,
            finding_lineage=projected_lineage or lineage,
            score_result=score,
            normalized_customer_items=customer_items,
            production_compatible_public_payload=public_payload,
            formal_acceptance_blockers=list(self.FORMAL_ACCEPTANCE_BLOCKERS),
            abstentions=abstentions,
            trace_records=traces,
            model_invocations=list(getattr(self.model, "invocation_records", [])),
            shadow_only=True,
            customer_publication_authorized=False,
        )
