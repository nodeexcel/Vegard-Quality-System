"""Evidence-bound Phase A4 assessment and deterministic admission.

The service is internal/shadow-only. With the production default pending regime
resolver it cannot invoke a model or admit a finding.
"""

from __future__ import annotations

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


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _identifier(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:24]}"


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


class AssessmentModel(Protocol):
    def assess(
        self,
        segment: ValidatedSegment,
        category: RuleCategory,
        rules: list[RuleRetrievalRecord],
    ) -> AssessmentCandidate: ...


class BedrockSemanticAssessmentModel:
    """JSON-only semantic assessor; invoked only after regime resolution."""

    SYSTEM_PROMPT = """You assess one bound point from a Norwegian condition report.
Use only the complete point body, its explicit source evidence, and the retrieved governed rules.
Assess meaning, not the presence or absence of headings. For TG2/TG3, Årsak, Risiko,
Konsekvens and recommended/necessary measures may be expressed in prose without an
'ANBEFALT TILTAK' label. Do not use text from another point to satisfy this point.
Search the entire bound point body for each semantic function. Text located under an
Årsak or Konsekvens heading may satisfy Risiko when it actually describes a possible
technical development; a missing heading alone is never a missing-field finding.
For TG3 cost, only a cost class/interval or other schematic estimate explicitly bound
to this physical point counts.
For Risiko, a pure inspection or documentation limitation is not sufficient unless it
names a possible technical defect, damage development, functional failure, or other
technical risk category. Use LIMITATION_USED_AS_RISK_SUBSTITUTE when a limitation is
used as the whole risk without naming that technical risk.
Wording that execution, materials, or documentation "cannot be documented" or
"cannot be verified" is a documentation limitation and must use
LIMITATION_USED_AS_RISK_SUBSTITUTE; it is not PRESENT_STATE_AS_RISIKO unless the
text describes a present technical condition, deterioration, or function loss.
For an ARKAT field request, return only that field's semantic error types; do not
propose TG-setting, age-only, scoring, or another assessment category's finding.
Treat every supplied complete_bound_body span as part of the same hierarchy-validated
physical point. Summary, navigation, boilerplate and foreign-point prose are excluded
and cannot satisfy the semantic requirement. Never treat generic guidance as point-specific.
For TGIU, assess the reason for non-inspection and a concrete further-investigation
recommendation independently and emit one candidate for each deficient requirement.
For methodology-only detached structures, evaluate the governed explanatory-structure
rule against the complete described deviations; do not treat absence of TG alone as a
defect. For legality, consider the complete linked legality explanation before finding
a deficiency; a deviation alone is not sufficient when its status and implications
are substantively explained elsewhere in the supplied same-object evidence.
If evidence or applicability is uncertain, abstain. Return JSON only."""

    def __init__(self, bedrock_client: Any | None = None, max_tokens: int = 3000):
        self._client = bedrock_client
        self.max_tokens = max_tokens
        self._primed: dict[tuple[str, RuleCategory], list[AssessmentCandidate]] = {}
        self.invocation_records: list[dict[str, Any]] = []

    def _bedrock(self):
        if self._client is None:
            from app.config import settings
            from app.services.bedrock_ai import BedrockAI

            self._client = BedrockAI(region=settings.AWS_REGION)
        return self._client

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
            "segment": {
                "segment_id": segment.segment_id,
                "kind": segment.kind.value,
                "title": segment.title,
                "point_label": segment.point_label,
                "tg_grade": segment.tg_grade,
                "professional_subject": segment.professional_subject,
                "complete_bound_body": [span.model_dump(mode="json") for span in source_spans],
            },
            "rule_category": category.value,
            "retrieved_rules": [record.model_dump(mode="json") for record in rules],
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
        prompt = {
            "segment": {
                "segment_id": segment.segment_id, "kind": segment.kind.value,
                "title": segment.title, "point_label": segment.point_label,
                "tg_grade": segment.tg_grade, "point_type": segment.point_type,
                "section_context": segment.section_context,
                "complete_bound_body": [span.model_dump(mode="json") for span in source_spans],
            },
            "rule_category": category.value,
            "retrieved_rules": [record.model_dump(mode="json") for record in rules],
            "instruction": (
                "Return one candidate for every independently satisfied, deficient, or abstained governed requirement "
                "that applies to this physical point. Do not combine distinct governed error types. Do not emit a "
                "deficiency unless its exact proposed_finding_type occurs in a retrieved rule. "
                + (
                    "This is a TGIU point: assess missing reason and missing concrete further investigation "
                    "as separate governed candidates. "
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
        payload = self._bedrock().generate_json_with_claude(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=json.dumps(prompt, ensure_ascii=False, sort_keys=True),
            max_tokens=self.max_tokens,
            retry_json_prompt=True,
        )
        values = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            raise ValueError("assessment response has no candidates array")
        candidates: list[AssessmentCandidate] = []
        for item in values:
            candidate = AssessmentCandidate.model_validate(item)
            candidates.extend(_split_compound_tgiu_candidate(candidate, rules))
        return candidates

    def prime_worklist(
        self,
        worklist: list[tuple[ValidatedSegment, RuleCategory, list[RuleRetrievalRecord]]],
        *,
        batch_size: int = 2,
    ) -> None:
        """Batch invocations without changing segment/category admission units."""
        by_segment: dict[str, dict[str, Any]] = {}
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
            entry = by_segment.setdefault(segment.segment_id, {"segment": segment, "categories": {}})
            entry["categories"][category] = rules
            self._primed[(segment.segment_id, category)] = []
        entries = list(by_segment.values())
        for offset in range(0, len(entries), batch_size):
            batch = entries[offset:offset + batch_size]
            tasks: list[dict[str, Any]] = []
            allowed: set[tuple[str, RuleCategory]] = set()
            for entry in batch:
                segment = entry["segment"]
                spans = segment.bound_body_spans or segment.evidence_spans or (
                    [segment.evidence] if segment.evidence else []
                )
                assessments = []
                governed_rules: dict[str, dict[str, Any]] = {}
                for category, rules in entry["categories"].items():
                    allowed.add((segment.segment_id, category))
                    assessments.append({
                        "rule_category": category.value,
                        "retrieval_ids": [record.retrieval_id for record in rules],
                        "governed_rule_ids": [record.rule_id for record in rules],
                    })
                    for record in rules:
                        content_hash = hashlib.sha256(_canonical(record.content)).hexdigest()
                        key = f"{record.asset_path}:{record.rule_id}:{content_hash}"
                        governed_rules.setdefault(key, {
                            "asset_path": record.asset_path,
                            "rule_id": record.rule_id,
                            "content": record.content,
                        })
                tasks.append({
                    "segment": {
                        "segment_id": segment.segment_id,
                        "kind": segment.kind.value,
                        "title": segment.title,
                        "point_label": segment.point_label,
                        "tg_grade": segment.tg_grade,
                        "point_type": segment.point_type,
                        "section_context": segment.section_context,
                        "complete_bound_body": [span.model_dump(mode="json") for span in spans],
                    },
                    "assessments": assessments,
                    "governed_rules": list(governed_rules.values()),
                })
            prompt = {
                "tasks": tasks,
                "instruction": (
                    "Assess every requested segment/category pair independently. Return at least one candidate "
                    "for every pair, including SATISFIED or ABSTAIN when no deficiency is present. Copy the exact "
                    "segment_id, rule_category, applicable retrieval_ids, and evidence_ids from the task. Never "
                    "use evidence from another segment."
                ),
                "required_output_schema": {
                    "type": "object", "required": ["candidates"],
                    "properties": {"candidates": {"type": "array", "items": AssessmentCandidate.model_json_schema()}},
                },
            }
            payload = self._bedrock().generate_json_with_claude(
                system_prompt=self.SYSTEM_PROMPT,
                user_prompt=json.dumps(prompt, ensure_ascii=False, sort_keys=True),
                max_tokens=12000,
                retry_json_prompt=True,
            )
            raw_candidates = payload.get("candidates") if isinstance(payload, dict) else None
            if not isinstance(raw_candidates, list):
                raise ValueError("batched assessment response has no candidates array")
            rules_by_key = {
                (entry["segment"].segment_id, category): rules
                for entry in batch
                for category, rules in entry["categories"].items()
            }
            for item in raw_candidates:
                candidate = AssessmentCandidate.model_validate(item)
                key = (candidate.segment_id, candidate.rule_category)
                if key in allowed:
                    self._primed[key].extend(
                        _split_compound_tgiu_candidate(candidate, rules_by_key[key])
                    )
            bedrock = self._bedrock()
            self.invocation_records.append({
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


def _semantic_risiko_present(segment: ValidatedSegment) -> bool:
    """Recognize explicit technical risk semantics in the isolated point body."""
    body = "\n".join(
        span.exact_quote
        for span in (segment.bound_body_spans or segment.evidence_spans)
    ).casefold()
    body = re.sub(r"\s+", " ", body)
    body = re.sub(
        r"hvordan\s+kontrollen\s+er\s+utført.*?konklusjon\s+bygningsdel\s*:?",
        " ", body,
    )
    risk_patterns = (
        r"\b(?:økt\s+)?risiko(?:en)?\s+(?:for|av)\s+[^.\n;]{2,220}",
        r"\bfare\s+for\s+[^.\n;]{2,220}",
        r"\bøkt\s+sannsynlighet\s+for\s+[^.\n;]{2,220}",
        r"\b(?:kan|vil\s+kunne|kan\s+over\s+tid)\s+[^.\n;]{0,120}"
        r"(?:føre\s+til|medføre|resultere\s+i|utvikle|belaste|påvirke)\s+[^.\n;]{2,180}",
    )
    harm = re.compile(
        r"\b(?:\w*skad\w*|fukt\w*|råte\w*|vanninntreng\w*|snøras\w*|"
        r"\w*svikt\w*|nedbryt\w*|lekk\w*|kondens\w*|brann\w*|helse\w*|"
        r"oppsvell\w*|membran\w*|fuktbelast\w*|"
        r"funksjonstap\w*|redusert\s+levetid|setningsskad\w*|korrosjon\w*)\b"
    )
    return any(
        harm.search(match.group(0))
        for pattern in risk_patterns
        for match in re.finditer(pattern, body)
    )


def _normalize_semantic_candidate(
    candidate: AssessmentCandidate,
    segment: ValidatedSegment,
    records: Iterable[RuleRetrievalRecord],
) -> AssessmentCandidate:
    """Apply deterministic evidence guards without inventing a finding."""
    if (
        candidate.decision == AssessmentDecision.DEFICIENT
        and candidate.rule_category == RuleCategory.RISIKO
        and candidate.proposed_finding_type in {"MISSING", "MISSING (risiko)"}
        and _semantic_risiko_present(segment)
    ):
        return candidate.model_copy(update={
            "decision": AssessmentDecision.SATISFIED,
            "proposed_finding_type": None,
            "explanation": (
                candidate.explanation
                + " Deterministic evidence validation found explicit technical-risk semantics "
                  "inside the isolated physical point body; a missing-Risiko finding is not admissible."
            ),
        })
    if (
        candidate.decision == AssessmentDecision.DEFICIENT
        and candidate.rule_category == RuleCategory.AARSAK
        and candidate.proposed_finding_type == "OBSERVATION_AS_AARSAK"
    ):
        body = "\n".join(
            span.exact_quote for span in (segment.bound_body_spans or segment.evidence_spans)
        )
        cause = re.search(
            r"(?is)\bÅrsak\s*:\s*(.*?)\s*(?:Konsekvens(?:/tiltak)?|Risiko|Anbefalt\s+tiltak)\s*:?",
            body,
        )
        if cause and re.fullmatch(
            r"\s*(?:alder|elde)(?:\s+og\s+slitasje)?\s*\.?\s*", cause.group(1), re.I
        ):
            return candidate.model_copy(update={
                "decision": AssessmentDecision.SATISFIED,
                "proposed_finding_type": None,
                "explanation": (
                    candidate.explanation
                    + " The isolated Årsak field is an age/elde statement, not an observation repeated as cause; "
                      "OBSERVATION_AS_AARSAK is therefore not the applicable governed taxonomy."
                ),
            })
    if (
        candidate.decision == AssessmentDecision.SATISFIED
        and candidate.rule_category == RuleCategory.KONSEKVENS
    ):
        body = "\n".join(
            span.exact_quote for span in (segment.bound_body_spans or segment.evidence_spans)
        )
        field = re.search(
            r"(?is)\bKonsekvens\s*:\s*(.*?)(?:\n(?:Anbefalt(?:e)?\s+tiltak|Tiltak|"
            r"[A-ZÆØÅ0-9][^\n]{0,100}\s+TG[0-3])\b|\Z)",
            body,
        )
        consequence = re.sub(r"\s+", " ", field.group(1)).strip() if field else ""
        governed = _governed_finding_types(records)
        finding_type = None
        if (
            "TILTAK_AS_KONSEKVENS" in governed
            and re.search(r"\b(?:trenger|må|bør)\s+(?:vedlikehold\w*\s+og\s+)?utbedr\w*", consequence, re.I)
            and not re.search(r"\b(?:kan|medfører|fører\s+til|resulterer|risiko|fare|redusert\s+levetid)\b", consequence, re.I)
        ):
            finding_type = "TILTAK_AS_KONSEKVENS"
        elif (
            "RISIKO_AS_KONSEKVENS" in governed
            and re.fullmatch(
                r"(?is)\s*(?:det\s+er\s+)?risiko\s+for\s+[^.]+\.?\s*",
                consequence,
            )
        ):
            finding_type = "RISIKO_AS_KONSEKVENS"
        elif (
            "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS" in governed
            and re.fullmatch(r"(?is)\s*økt\s+(?:fukt)?belast\w*\s+(?:på|mot)\s+[^.]+\.?\s*", consequence)
        ):
            finding_type = "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS"
        if finding_type:
            return candidate.model_copy(update={
                "decision": AssessmentDecision.DEFICIENT,
                "proposed_finding_type": finding_type,
                "explanation": (
                    candidate.explanation
                    + f" Deterministic semantic validation classifies the isolated Konsekvens text as {finding_type}."
                ),
            })
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
            and any(record.rule_id == "GATE_TG2_ARK_MISSING" for record in rules)
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


class PhaseA4ShadowService:
    FORMAL_ACCEPTANCE_BLOCKERS = ()
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
                    document_hash=understanding.document_hash, top_k=12,
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
                    if decision.admission == FindingAdmission.REJECTED and assessment.decision == AssessmentDecision.DEFICIENT:
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
