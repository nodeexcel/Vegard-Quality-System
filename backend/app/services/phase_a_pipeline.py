"""Explicitly disabled/shadow-only integration boundary for approved A1/A2 work."""

from __future__ import annotations

from dataclasses import dataclass

from typing import Iterable

from app.services.phase_a_assessment import PhaseA4ShadowService
from app.services.phase_a_contracts import DocumentUnderstandingResult, PhaseA4Result, RuleCategory
from app.services.phase_a_document_understanding import DocumentUnderstandingService


class PhaseANotEnabledError(RuntimeError):
    pass


class PhaseAPublicationNotAuthorizedError(RuntimeError):
    pass


@dataclass(frozen=True)
class PhaseAFeaturePolicy:
    enabled: bool = False
    shadow_only: bool = True


class PhaseAPipeline:
    """Internal entry point that cannot change or publish v46 customer results."""

    def __init__(
        self,
        understanding_service: DocumentUnderstandingService,
        policy: PhaseAFeaturePolicy = PhaseAFeaturePolicy(),
        assessment_service: PhaseA4ShadowService | None = None,
    ):
        self.understanding_service = understanding_service
        self.policy = policy
        self.assessment_service = assessment_service

    def understand_document(
        self,
        report_text: str,
        source_filename: str,
        *,
        source_pdf_sha256: str | None = None,
    ) -> DocumentUnderstandingResult:
        if not self.policy.enabled:
            raise PhaseANotEnabledError("Phase A is disabled; explicit internal enablement is required")
        if not self.policy.shadow_only:
            raise PhaseAPublicationNotAuthorizedError("A1/A2 are authorized for shadow/internal use only")
        return self.understanding_service.analyze(
            report_text,
            source_filename,
            source_pdf_sha256=source_pdf_sha256,
        )

    def analyze_shadow(
        self,
        understanding: DocumentUnderstandingResult,
        categories: Iterable[RuleCategory],
    ) -> PhaseA4Result:
        if not self.policy.enabled:
            raise PhaseANotEnabledError("Phase A is disabled; explicit internal enablement is required")
        if not self.policy.shadow_only:
            raise PhaseAPublicationNotAuthorizedError("Phase A4 is authorized for shadow/internal use only")
        if self.assessment_service is None:
            raise PhaseANotEnabledError("Phase A4 assessment service has not been configured")
        return self.assessment_service.analyze(understanding, categories)

    def build_customer_payload(self, _result: DocumentUnderstandingResult) -> dict:
        raise PhaseAPublicationNotAuthorizedError(
            "Customer publication is not authorized for the Phase A shadow pipeline"
        )
