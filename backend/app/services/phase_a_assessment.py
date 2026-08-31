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
        "complete_bound_body": [_prompt_span_payload(span) for span in spans],
    }


def _semantic_replay_version(rule_category: str | None) -> str:
    if rule_category == RuleCategory.RISIKO.value:
        return "risiko_v2"
    if rule_category == RuleCategory.AARSAK.value:
        return "aarsak_v2"
    if rule_category == RuleCategory.METHODOLOGY.value:
        return "methodology_v2"
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


def _should_refresh_risk_replay(segment_payload: dict[str, Any]) -> bool:
    body = " ".join(
        str(item.get("exact_quote") or "")
        for item in (segment_payload.get("complete_bound_body") or [])
    ).casefold()
    if not body:
        return False
    return (
        "spesielt utsatt" in body
        and any(token in body for token in ("lekk", "fukt", "vanninntreng"))
        and any(token in body for token in ("ekstremvær", "kraftig nedbør", "snø"))
    )


def _should_refresh_aarsak_replay(segment_payload: dict[str, Any]) -> bool:
    body = " ".join(
        str(item.get("exact_quote") or "")
        for item in (segment_payload.get("complete_bound_body") or [])
    ).casefold()
    if not body:
        return False
    service_life = (
        "forventet brukstid er passert" in body
        or "mer enn halvparten av forventet brukstid er passert" in body
        or "modent for modernisering" in body
    )
    age_related = (
        "eldre årgang" in body
        or "varierende årgang" in body
        or "fra byggeår" in body
        or "dårligere isolasjonsevne" in body
    )
    return service_life or age_related


def _should_refresh_methodology_replay(segment_payload: dict[str, Any]) -> bool:
    body = " ".join(
        str(item.get("exact_quote") or "")
        for item in (segment_payload.get("complete_bound_body") or [])
    ).casefold()
    if not body:
        return False
    return (
        "ingen opplysninger om at det er" in body
        and any(token in body for token in ("nedgravd", "skjult", "tilstede", "finnes"))
    )


_TG3_COST_INTERVAL_RE = re.compile(
    r"(?<!\d)\d{1,3}(?:[ .]\d{3})+\s*-\s*\d{1,3}(?:[ .]\d{3})+(?!\d)"
)
_TG3_COST_SINGLE_AMOUNT_RE = re.compile(r"(?<!\d)\d{1,3}(?:[ .]\d{3})+(?!\d)")
_TG3_COST_CLASS_RE = re.compile(
    r"(?i)\b(?:lav|middels?|høy)\s+kostnad\b|\bkostnad(?:sestimat|sklasse)?\s*:\s*(?:lav|middels?|høy)\b"
)


def _tg3_cost_status_from_segment(segment: ValidatedSegment) -> str:
    body = "\n".join(
        span.exact_quote for span in (segment.bound_body_spans or segment.evidence_spans)
    )
    normalized = re.sub(r"[–—]", "-", body)
    if _TG3_COST_INTERVAL_RE.search(normalized) or _TG3_COST_CLASS_RE.search(normalized):
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


def _rebind_replayed_candidate(
    candidate: AssessmentCandidate,
    segment: ValidatedSegment,
    category: RuleCategory,
    rules: list[RuleRetrievalRecord],
) -> AssessmentCandidate:
    spans = segment.bound_body_spans or segment.evidence_spans or ([segment.evidence] if segment.evidence else [])
    evidence_ids = [span.evidence_id for span in spans if span is not None]
    return candidate.model_copy(
        update={
            "segment_id": segment.segment_id,
            "rule_category": category,
            # Let deterministic admission resolve the precise governed rule for
            # the current retrieval set instead of reusing stale replay IDs.
            "retrieval_ids": [],
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

    SYSTEM_PROMPT = """You assess one bound point from a Norwegian condition report.
Use only the complete point body, its explicit source evidence, and the retrieved governed rules.
Assess meaning, not the presence or absence of headings. For TG2/TG3, Årsak, Risiko,
Konsekvens and recommended/necessary measures may be expressed in prose without an
'ANBEFALT TILTAK' label. Do not use text from another point to satisfy this point.
Search the entire bound point body for each semantic function. Text located under an
Årsak or Konsekvens heading may satisfy Risiko when it actually describes a possible
technical development; a missing heading alone is never a missing-field finding.
If any sentence anywhere in the same physical point substantively describes a possible
technical defect, damage development, function loss, or other future technical risk,
Risiko is satisfied even if that sentence appears under Konsekvens, Vurdering, or
combined prose. Never require a dedicated Risiko heading, standalone field, or
separate section when the semantic role is already performed in the point body.
Wording that a component is especially exposed or vulnerable to leakage, moisture,
water ingress, or similar technical harm during heavy rain, snow, extreme weather,
or comparable operating conditions is a real technical-risk statement and satisfies
Risiko even when embedded in consequence/measure prose.
In wet-room and moisture contexts, statements that water may escape the room, remain
standing, burden or stress an underlying membrane, or cause moisture load/damage to
named constructions are real technical risk descriptions and satisfy Risiko.
But increased moisture load on the same component by itself, without stating what
damage, defect, failure, or affected secondary building part may develop, is not
enough to satisfy Risiko.
For service-life-limited installations, wording that damage, failure, leakage, or
other defects can suddenly occur on older installations is a real technical-risk
statement and satisfies Risiko even if the sentence appears under
Konsekvens/tiltak rather than under a dedicated Risiko heading.
Concise cause labels such as 'Alder', 'Utførelse', 'Fuktbelastning fra bruk av dusj',
or 'Manglende montering av beslag' may satisfy Årsak when they genuinely explain why
the observed condition has occurred for that point. Do not require a longer narrative
merely because the explanation is brief.
For age- and service-life-based assessments, wording such as 'mer enn halvparten av
forventet brukstid er passert', 'eldre årgang', 'varierende årgang', 'fra byggeår',
'dårligere isolasjonsevne sammenlignet med dagens standard', or 'modent for
modernisering' may satisfy Årsak when it explains that the observed condition or
reduced performance follows from age, service life, or original vintage.
For Konsekvens, text that mainly states an existing damage condition or that repairs,
maintenance, or utbedring are needed without explaining the buyer-relevant effect
should be treated as TILTAK_AS_KONSEKVENS. Text that stops at a technical process or
load, such as increased moisture load, without explaining the resulting practical or
buyer-relevant effect should be treated as TECHNICAL_DEVELOPMENT_AS_KONSEKVENS.
However, when the text says moisture, leakage, or water can affect adjacent,
underlying, or surrounding constructions/building parts, that already states a
practical building consequence and should normally be treated as satisfied rather
than TECHNICAL_DEVELOPMENT_AS_KONSEKVENS.
When the sentence names actual damage to a secondary building part, such as
fuktskade on an underlying ceiling, membrane, wall, or neighboring construction,
it remains a valid consequence even if phrased as a "risk of" that damage.
Treat the complete consequence field holistically. If any sentence in the same
point already states a genuine buyer-relevant effect such as reduced quality,
esthetic-only impact, reduced expected service life, increased maintenance/repair
need, replacement need, uncertainty affecting later work, or named moisture/damage
to another building part, then Konsekvens is satisfied and you must not emit a
deficiency merely because another sentence in that same field is phrased more
technically.
For service-life-limited installations, statements about limited remaining technical
life or clearly reduced remaining lifetime count as practical consequence when they
communicate aging-related replacement/maintenance burden; do not reclassify those as
RISIKO_AS_KONSEKVENS merely because leak risk is also mentioned.
For TG3 cost, only a cost class/interval or other schematic estimate explicitly bound
to this physical point counts. Never borrow an amount from another point, another page
window, or a document-level estimate. If the cited point-bound evidence spans do not
themselves contain the amount or cost class, TG3 cost is deficient.
For Risiko, a pure inspection or documentation limitation is not sufficient unless it
names a possible technical defect, damage development, functional failure, or other
technical risk category. Use LIMITATION_USED_AS_RISK_SUBSTITUTE when a limitation is
used as the whole risk without naming that technical risk.
If the same sentence says that hidden execution defects, weakened membrane/drain
connection, hidden moisture damage, or similar defect categories cannot be detected
or documented, that still names the technical risk category and Risiko is satisfied;
do not treat that as a pure limitation.
Wording that execution, materials, or documentation "cannot be documented" or
"cannot be verified" is a documentation limitation and must use
LIMITATION_USED_AS_RISK_SUBSTITUTE; it is not PRESENT_STATE_AS_RISIKO unless the
text describes a present technical condition, deterioration, or function loss.
For an ARKAT field request, return only that field's semantic error types; do not
propose TG-setting, age-only, scoring, or another assessment category's finding.
Judge the complete ANBEFALT TILTAK field holistically. If the same point already
contains concrete action guidance about what should be monitored, repaired,
replaced, documented, controlled, or followed up, the field is satisfied even if a
later sentence also states that costs, replacement, or other consequences must be
expected over time.
Treat every supplied complete_bound_body span as part of the same hierarchy-validated
physical point. Summary, navigation, boilerplate and foreign-point prose are excluded
and cannot satisfy the semantic requirement. Never treat generic guidance as point-specific.
For TGIU, assess the reason for non-inspection and a concrete further-investigation
recommendation independently and emit one candidate for each deficient requirement.
When the point says there are no information/opplysninger that a suspected buried or
hidden installation/object exists on the property, that can itself satisfy the reason
for non-investigation because it explains why direct inspection basis was absent. Do
not mark TGIU_MISSING_REASON in that situation. Assess any missing further
investigation recommendation independently.
For methodology-only detached structures, evaluate the governed explanatory-structure
rule against the complete described deviations; do not treat absence of TG alone as a
defect. When the same physical point describes one or more concrete deviations or
inspection limitations tied to that detached structure, a general sentence about normal
age/wear or a disclaimer that the structure was not condition-graded does not satisfy
the explanatory-structure requirement by itself. In that situation, return the governed
methodology deficiency unless the same point substantively explains cause, technical
risk, buyer consequence, and what should be done. For legality, consider the complete
linked legality explanation before finding a deficiency; a deviation alone is not
sufficient when its status and implications are substantively explained elsewhere in the
supplied same-object evidence.
If evidence or applicability is uncertain, abstain. Return JSON only."""

    ADJUDICATION_PROMPT = """You are the final governed semantic adjudicator for Validert.
Use only the complete hierarchy-bound physical-point evidence and the retrieved governed rules.
Assess professional meaning, not headings, labels, field placement, or exact phrases. A semantic
role may be satisfied anywhere in the same physical point. A heading is never required.
If any sentence anywhere in the point body already performs the semantic role, mark it
satisfied. Never require a dedicated Risiko field, separate heading, or standalone
section when the same physical point already contains substantive technical-risk prose.
Wording that a component is especially exposed or vulnerable to leakage, moisture,
water ingress, or similar technical harm during heavy rain, snow, extreme weather,
or comparable operating conditions is a real technical-risk statement and satisfies
Risiko even when embedded in consequence/measure prose.
In wet-room and moisture contexts, statements that water may escape the room, remain
standing, burden or stress an underlying membrane, or cause moisture load/damage to
named constructions are real technical risk descriptions and satisfy Risiko.
But increased moisture load on the same component by itself, without stating what
damage, defect, failure, or affected secondary building part may develop, is not
enough to satisfy Risiko.
For service-life-limited installations, wording that damage, failure, leakage, or
other defects can suddenly occur on older installations is a real technical-risk
statement and satisfies Risiko even if the sentence appears under
Konsekvens/tiltak rather than under a dedicated Risiko heading.
Brief but genuine causal labels such as 'Alder', 'Utførelse', 'Fuktbelastning fra bruk
av dusj', or 'Manglende montering av beslag' can satisfy Årsak when they explain why
the observed condition has occurred.
Age- and service-life wording can also satisfy Årsak when it explains the current
condition through age, original vintage, or reduced performance over time. Examples
include 'mer enn halvparten av forventet brukstid er passert', 'eldre årgang',
'varierende årgang', 'fra byggeår', poorer performance compared with current standard,
or that the component is mature for modernization.
For Konsekvens, text that mainly says repairs are needed or repeats an existing damage
state without explaining the buyer-relevant effect should be treated as
TILTAK_AS_KONSEKVENS. Text that stops at a technical process or load, such as
increased moisture load, without explaining the resulting practical consequence should
be treated as TECHNICAL_DEVELOPMENT_AS_KONSEKVENS.
If the text says moisture, leakage, or water can affect adjacent, underlying, or
surrounding constructions/building parts, that already expresses a practical
building consequence and should normally be treated as satisfied.
When the sentence names actual damage to a secondary building part, such as
fuktskade on an underlying ceiling, membrane, wall, or neighboring construction,
it remains a valid consequence even if phrased as a "risk of" that damage.
Judge the whole consequence field together. If any sentence in that same point
already gives a genuine buyer-relevant effect such as reduced quality, esthetic-only
impact, reduced remaining lifetime, maintenance/repair burden, replacement need, or
uncertainty affecting future works, Konsekvens is satisfied even if another
sentence in the same field is more technical.
For service-life-limited installations, limited remaining technical lifetime or
clearly reduced remaining lifetime counts as a practical consequence when it tells
the buyer that aging-related maintenance or replacement burden is approaching.
For TG3 cost, accept only a cost class/interval or other schematic estimate that is
actually present in the cited point-bound evidence for that same point. Never use an
amount that belongs to another point or another page window.
If a limitation sentence also names hidden defect categories such as hidden
execution defects, weakened membrane/sluk connection, or hidden moisture damage,
that names the technical risk and should be treated as satisfied Risiko rather than
LIMITATION_USED_AS_RISK_SUBSTITUTE.
For TGIU reason, wording that there are no information/opplysninger that a suspected
buried or hidden installation/object exists on the property can satisfy the reason
requirement; it explains why there was no direct basis for investigation. Further
investigation remains a separate requirement.

Independently verify the initial assessment. For every requested category, identify in your
reasoning which source sentence does or does not substantively perform that semantic role.
Observation, cause, technical risk, buyer consequence and recommended measure are distinct.
Do not use summary, boilerplate, foreign-point text or generic methodology as point evidence.
Judge the complete ANBEFALT TILTAK field holistically. If the same point already
contains concrete action guidance about what should be monitored, repaired,
replaced, documented, controlled, or followed up, the field is satisfied even if a
later sentence also states that costs, replacement, or other consequences must be
expected over time.

For detached or optional assessed structures, absence of TG is not a defect. Concrete described
deviations must nevertheless be evaluated against the retrieved explanatory-structure rule.
If the point lists concrete deviations, limitations, or observed defects but gives only a
general age/wear statement or a disclaimer that the structure was not fully condition-graded,
that is still deficient unless the point itself substantively explains cause, technical risk,
buyer consequence, and what should be done.

Return the authoritative structured candidates only. Do not defer to the initial answer merely
because it was supplied. If uncertain, abstain. Return JSON only."""

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
                            if (
                                candidate.segment_id == segment_payload.get("segment_id")
                                and candidate.rule_category.value == assessment.get("rule_category")
                            ):
                                by_key.setdefault(key, []).append(candidate)
                                if phase == "initial_semantic_assessment":
                                    self._initial_replay_loose.setdefault(loose_key, []).append(candidate)
                                elif phase == "governed_semantic_adjudication":
                                    existing = self._adjudication_replay_loose.get(loose_key)
                                    if existing is None:
                                        self._adjudication_replay_loose[loose_key] = candidate
                                    elif existing.model_dump(mode="json") != candidate.model_dump(mode="json"):
                                        self._adjudication_replay_loose.pop(loose_key, None)
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
                elif phase == "governed_semantic_adjudication" and len(parsed) == 1:
                    self._adjudication_replay.setdefault(key, parsed[0])
                    existing = self._adjudication_replay_loose.get(loose_key)
                    if existing is None:
                        self._adjudication_replay_loose[loose_key] = parsed[0]
                    elif existing.model_dump(mode="json") != parsed[0].model_dump(mode="json"):
                        self._adjudication_replay_loose.pop(loose_key, None)

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
        strict_key = _replay_key_from_task(segment_payload, assessment_payload)
        replayed = self._adjudication_replay.get(strict_key)
        if replayed is not None:
            return replayed.model_copy(deep=True)
        loose_key = _loose_replay_key_from_task(segment_payload, assessment_payload)
        loose = self._adjudication_replay_loose.get(loose_key)
        return loose.model_copy(deep=True) if loose is not None else None

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
        else:
            prompt = {
                "segment": segment_payload,
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
        if segment.point_type == "methodology_only":
            adjudication = {
                "segment": segment_payload,
                "rule_category": category.value,
                "retrieved_rules": [record.model_dump(mode="json") for record in rules],
                "initial_candidates": [item.model_dump(mode="json") for item in candidates],
                "instruction": (
                    "Return every independently applicable governed methodology decision. "
                    "Concrete deviations must be evaluated even when general age-related wear is also stated; "
                    "do not find a defect from missing TG alone."
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
            for key, candidates in initial_by_key.items():
                self._primed[key].extend(candidates)

            adjudication_keys = set()
            for key in allowed:
                if key[1] not in self.ARKAT_CATEGORIES:
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
                    adjudication_prompt = {
                        "tasks": adjudication_tasks,
                        "instruction": (
                            "Return exactly one authoritative candidate for every requested segment/category pair. "
                            "Judge the semantic role across the complete bound point body; never require a heading. "
                            "Copy exact segment_id, rule_category, applicable retrieval_ids and evidence_ids."
                        ),
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
        and segment.point_type == "tgiu"
        and candidate.decision == AssessmentDecision.DEFICIENT
        and candidate.proposed_finding_type == "TGIU_MISSING_REASON"
    ):
        body = "\n".join(
            span.exact_quote for span in (segment.bound_body_spans or segment.evidence_spans)
        ).casefold()
        if (
            "ikke mulig" in body
            or "grunnet" in body
            or "på grunn av" in body
            or "fordi" in body
            or "da " in body
            or re.search(
                r"\bingen\s+opplysning(?:er)?\s+om\s+at\s+.{0,120}\b(?:er|finnes|foreligger)\b",
                body,
            )
        ):
            return candidate.model_copy(
                update={
                    "decision": AssessmentDecision.SATISFIED,
                    "proposed_finding_type": None,
                    "explanation": (
                        "The point explains why direct investigation basis was absent by stating that "
                        "there are no information/opplysninger that the suspected hidden installation "
                        "exists on the property."
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
