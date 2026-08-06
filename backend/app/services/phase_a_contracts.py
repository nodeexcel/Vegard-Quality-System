"""Versioned contracts for the additive Phase A document-understanding path.

These contracts are deliberately independent from the v46 customer pipeline. They
describe evidence and trace data only; they do not select regimes, create governed
findings, score reports, or alter public responses.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


CONTRACT_VERSION = "phase_a_contracts_v1"


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactType(str, Enum):
    INSPECTION_DATE = "inspection_date"
    REPORT_DATE = "report_date"
    ISSUE_DATE = "issue_date"
    REVISION_DATE = "revision_date"
    OTHER_DATE = "other_date"
    DECLARED_STANDARD = "declared_standard"
    PROVIDER = "provider"
    TEMPLATE = "template"


class SegmentKind(str, Enum):
    REPORT_POINT = "report_point"
    SECTION = "section"
    SUMMARY = "summary"
    LEGALITY = "legality"
    METHODOLOGY = "methodology"
    OTHER = "other"


class ValidationStatus(str, Enum):
    VALIDATED = "validated"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"


class CandidateOutcome(str, Enum):
    ADMITTED = "admitted"
    ABSTAINED = "abstained"
    DUPLICATE = "duplicate"


class UnderstandingStatus(str, Enum):
    COMPLETE = "complete"
    COMPLETE_WITH_ABSTENTIONS = "complete_with_abstentions"
    FAILED = "failed"


class AnalysisState(str, Enum):
    COMPLETE_WITH_FINDINGS = "complete_with_findings"
    COMPLETE_WITHOUT_FINDINGS = "complete_without_findings"
    LIMITED = "limited"
    REQUIRES_CLARIFICATION = "requires_clarification"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class AssessmentDecision(str, Enum):
    SATISFIED = "satisfied"
    DEFICIENT = "deficient"
    ABSTAIN = "abstain"


class FindingAdmission(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RuleCategory(str, Enum):
    AARSAK = "aarsak"
    RISIKO = "risiko"
    KONSEKVENS = "konsekvens"
    ANBEFALT_TILTAK = "anbefalt_tiltak"
    METHODOLOGY = "methodology"
    LEGALITY = "legality"
    TG3_COST = "tg3_cost"


class RegimeResolutionStatus(str, Enum):
    PENDING_GOVERNED_DECISION = "pending_governed_decision"
    RESOLVED = "resolved"
    REQUIRES_CLARIFICATION = "requires_clarification"


class RuleApplicability(str, Enum):
    CANDIDATE_ONLY = "candidate_only"
    REGIME_RESOLVED = "regime_resolved"


class CandidateEvidence(StrictContract):
    exact_quote: str = Field(min_length=1, max_length=100000)
    page: Optional[int] = Field(default=None, ge=1)
    claimed_char_start: Optional[int] = Field(default=None, ge=0)
    claimed_char_end: Optional[int] = Field(default=None, ge=1)


class SourceEvidence(StrictContract):
    evidence_id: str = Field(min_length=8, max_length=80)
    exact_quote: str = Field(min_length=1, max_length=100000)
    page: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=1)
    quote_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    match_method: str = Field(default="exact", pattern=r"^(exact|whitespace_normalized|multi_span_normalized)$")
    validation_status: ValidationStatus
    validation_notes: List[str] = Field(default_factory=list)


class DocumentFactCandidate(StrictContract):
    candidate_id: Optional[str] = Field(default=None, max_length=120)
    fact_type: FactType
    raw_value: str = Field(min_length=1, max_length=1000)
    normalized_value: Optional[str] = Field(default=None, max_length=1000)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: CandidateEvidence


class ValidatedDocumentFact(StrictContract):
    fact_id: str = Field(min_length=8, max_length=80)
    fact_type: FactType
    raw_value: str
    normalized_value: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    candidate_evidence: CandidateEvidence
    evidence: Optional[SourceEvidence] = None
    validation_status: ValidationStatus
    validation_notes: List[str] = Field(default_factory=list)


class SegmentCandidate(StrictContract):
    candidate_id: Optional[str] = Field(default=None, max_length=120)
    kind: SegmentKind
    title: str = Field(min_length=1, max_length=500)
    professional_subject: str = Field(min_length=1, max_length=500)
    point_label: Optional[str] = Field(default=None, max_length=120)
    tg_grade: Optional[str] = Field(default=None, max_length=40)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: CandidateEvidence


class ValidatedSegment(StrictContract):
    segment_id: str = Field(min_length=8, max_length=80)
    kind: SegmentKind
    title: str
    professional_subject: str
    point_label: Optional[str] = None
    tg_grade: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    candidate_evidence: CandidateEvidence
    evidence: Optional[SourceEvidence] = None
    evidence_spans: List[SourceEvidence] = Field(default_factory=list)
    bound_body_spans: List[SourceEvidence] = Field(default_factory=list)
    bound_body_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    validation_status: ValidationStatus
    validation_notes: List[str] = Field(default_factory=list)


class Abstention(StrictContract):
    abstention_id: str = Field(min_length=8, max_length=80)
    stage: str = Field(min_length=1, max_length=120)
    subject: str = Field(min_length=1, max_length=500)
    reason_code: str = Field(min_length=1, max_length=120)
    explanation: str = Field(min_length=1, max_length=2000)
    candidate_id: Optional[str] = Field(default=None, max_length=120)


class CandidateBatch(StrictContract):
    facts: List[DocumentFactCandidate] = Field(default_factory=list)
    segments: List[SegmentCandidate] = Field(default_factory=list)
    abstentions: List[Dict[str, Any]] = Field(default_factory=list)


class CandidateDisposition(StrictContract):
    disposition_id: str = Field(min_length=8, max_length=80)
    batch_index: int = Field(ge=1)
    candidate_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_id: Optional[str] = Field(default=None, max_length=120)
    entity_kind: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=500)
    page: Optional[int] = Field(default=None, ge=1)
    point_label: Optional[str] = Field(default=None, max_length=120)
    tg_grade: Optional[str] = Field(default=None, max_length=40)
    professional_subject: str = Field(min_length=1, max_length=500)
    outcome: CandidateOutcome
    final_entity_id: Optional[str] = Field(default=None, max_length=120)
    duplicate_of_entity_id: Optional[str] = Field(default=None, max_length=120)
    reason_codes: List[str] = Field(default_factory=list)


class CoverageBucket(StrictContract):
    key: str = Field(min_length=1, max_length=120)
    candidates: int = Field(ge=0)
    unique_candidates: int = Field(ge=0)
    admitted: int = Field(ge=0)
    abstained: int = Field(ge=0)
    duplicates: int = Field(ge=0)


class SegmentCoverage(StrictContract):
    raw_candidate_count: int = Field(ge=0)
    unique_candidate_count: int = Field(ge=0)
    admitted_count: int = Field(ge=0)
    abstained_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    dispositions_count: int = Field(ge=0)
    by_kind: List[CoverageBucket] = Field(default_factory=list)
    by_tg: List[CoverageBucket] = Field(default_factory=list)
    completion_blockers: List[str] = Field(default_factory=list)


class DocumentUnderstandingResult(StrictContract):
    contract_version: str = CONTRACT_VERSION
    run_id: str = Field(min_length=8, max_length=80)
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_filename: str = Field(min_length=1, max_length=1000)
    route_used: str = Field(min_length=1, max_length=200)
    fallback_reason: Optional[str] = Field(default=None, max_length=500)
    source_pdf_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    extracted_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: UnderstandingStatus
    facts: List[ValidatedDocumentFact] = Field(default_factory=list)
    segments: List[ValidatedSegment] = Field(default_factory=list)
    abstentions: List[Abstention] = Field(default_factory=list)
    batch_count: int = Field(ge=0)
    candidate_batches: List[CandidateBatch] = Field(default_factory=list)
    candidate_dispositions: List[CandidateDisposition] = Field(default_factory=list)
    segment_coverage: SegmentCoverage
    model_metadata: List[Dict[str, Any]] = Field(default_factory=list)
    trace_records: List["TraceRecord"] = Field(default_factory=list)


class RuleRetrievalRecord(StrictContract):
    retrieval_id: str = Field(min_length=8, max_length=80)
    segment_id: str = Field(min_length=8, max_length=80)
    rule_category: RuleCategory
    asset_path: str = Field(min_length=1, max_length=1000)
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule_id: str = Field(min_length=1, max_length=200)
    json_pointer: str = Field(min_length=1, max_length=1000)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: Dict[str, Any]
    relevance_score: float = Field(ge=0.0, le=1.0)
    applicability: RuleApplicability = RuleApplicability.CANDIDATE_ONLY
    regime_status: RegimeResolutionStatus = RegimeResolutionStatus.PENDING_GOVERNED_DECISION
    regime_id: Optional[str] = Field(default=None, max_length=200)
    controlling_fact_ids: List[str] = Field(default_factory=list)
    regime_explanation: str = Field(min_length=1, max_length=2000)
    retrieval_reason: str = Field(min_length=1, max_length=2000)


class GovernedAssetVerification(StrictContract):
    asset_path: str = Field(min_length=1, max_length=1000)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actual_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verified: bool


class RuleRetrievalResult(StrictContract):
    segment_id: str = Field(min_length=8, max_length=80)
    rule_category: RuleCategory
    regime_resolution: "RegimeResolution"
    records: List[RuleRetrievalRecord] = Field(default_factory=list)
    asset_verifications: List[GovernedAssetVerification] = Field(default_factory=list)
    abstentions: List[Abstention] = Field(default_factory=list)
    trace_records: List["TraceRecord"] = Field(default_factory=list)


class StructuredAssessment(StrictContract):
    assessment_id: str = Field(min_length=8, max_length=80)
    segment_id: str = Field(min_length=8, max_length=80)
    retrieval_ids: List[str] = Field(min_length=1)
    rule_category: RuleCategory
    decision: AssessmentDecision
    explanation: str = Field(min_length=1, max_length=4000)
    evidence_ids: List[str] = Field(default_factory=list)
    proposed_finding_type: Optional[str] = Field(default=None, max_length=300)


class AssessmentCandidate(StrictContract):
    segment_id: str = Field(min_length=8, max_length=80)
    retrieval_ids: List[str] = Field(default_factory=list)
    rule_category: RuleCategory
    decision: AssessmentDecision
    explanation: str = Field(min_length=1, max_length=4000)
    evidence_ids: List[str] = Field(default_factory=list)
    proposed_finding_type: Optional[str] = Field(default=None, max_length=300)


class FindingValidationDecision(StrictContract):
    validation_id: str = Field(min_length=8, max_length=80)
    assessment_id: str = Field(min_length=8, max_length=80)
    admission: FindingAdmission
    reason_codes: List[str] = Field(default_factory=list)
    accepted_finding_id: Optional[str] = Field(default=None, max_length=200)


class ApplicabilityPlanItem(StrictContract):
    plan_item_id: str = Field(min_length=8, max_length=80)
    segment_id: str = Field(min_length=8, max_length=80)
    rule_category: RuleCategory
    required: bool
    reason_codes: List[str] = Field(default_factory=list)


class FindingLineageRecord(StrictContract):
    accepted_finding_id: str = Field(min_length=8, max_length=200)
    assessment_id: str = Field(min_length=8, max_length=80)
    segment_id: str = Field(min_length=8, max_length=80)
    rule_category: RuleCategory
    public_projection_status: str = Field(pattern=r"^(pending|projected|withheld)$")
    public_finding_id: Optional[str] = Field(default=None, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)


class PhaseA4Result(StrictContract):
    run_id: str = Field(min_length=8, max_length=80)
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_state: AnalysisState
    retrievals: List[RuleRetrievalResult] = Field(default_factory=list)
    applicability_plan: List[ApplicabilityPlanItem] = Field(default_factory=list)
    assessments: List[StructuredAssessment] = Field(default_factory=list)
    validation_decisions: List[FindingValidationDecision] = Field(default_factory=list)
    finding_lineage: List[FindingLineageRecord] = Field(default_factory=list)
    abstentions: List[Abstention] = Field(default_factory=list)
    trace_records: List["TraceRecord"] = Field(default_factory=list)
    shadow_only: bool = True
    customer_publication_authorized: bool = False


class TraceRecord(StrictContract):
    trace_id: str = Field(min_length=8, max_length=80)
    document_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    stage: str = Field(min_length=1, max_length=120)
    entity_type: str = Field(min_length=1, max_length=120)
    entity_id: str = Field(min_length=1, max_length=200)
    parent_trace_ids: List[str] = Field(default_factory=list)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RegimeResolution(StrictContract):
    rule_category: RuleCategory
    status: RegimeResolutionStatus
    regime_id: Optional[str] = Field(default=None, max_length=200)
    controlling_fact_ids: List[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1, max_length=2000)


DocumentUnderstandingResult.model_rebuild()
