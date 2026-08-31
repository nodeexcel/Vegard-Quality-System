"""Isolated A2 document-fact extraction and segmentation.

Nothing in the v46 upload/report route imports this module. It is an additive
internal service which produces evidence-bound candidates for later Phase A
stages; it does not retrieve rules, assess deficiencies, score, or publish data.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from app.services.phase_a_contracts import (
    Abstention,
    CandidateBatch,
    CandidateDisposition,
    CandidateEvidence,
    CandidateOutcome,
    CoverageReconciliation,
    CoverageBucket,
    DocumentFactCandidate,
    DocumentUnderstandingResult,
    FactType,
    InventoryRole,
    ReportQualityObservation,
    SegmentCandidate,
    SegmentCoverage,
    SegmentKind,
    SourceEvidence,
    TraceRecord,
    UnderstandingStatus,
    ValidatedDocumentFact,
    ValidatedSegment,
    ValidationStatus,
)
from app.services.phase_a_source_inventory import PhysicalSourceInventoryBuilder


PAGE_MARKER_RE = re.compile(r"(?m)^\[SIDE\s+(\d+)\]\s*$")
DATE_TOKEN_RE = re.compile(
    r"(?<!\d)(?:(\d{4})[-./](\d{1,2})[-./](\d{1,2})|(\d{1,2})[-./](\d{1,2})[-./](\d{4}))(?!\d)"
)
NS_3600_RE = re.compile(r"(?i)\bNS\s*3600\s*[:\-]?\s*(2018|2025)\b")
DECLARED_STANDARD_RE = re.compile(
    r"(?ix)\b(?:norsk\s+standard|ns)\s*([0-9]{3,4})(?:\s*[:\-]?\s*([0-9]{4}))?\b"
)
TG_RE = re.compile(r"(?i)^TG\s*(0|1|2|3|IU)$")


@dataclass(frozen=True)
class PageSpan:
    page: int
    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class NormalizedTextMap:
    text: str
    original_starts: Tuple[int, ...]
    original_ends: Tuple[int, ...]


class CandidateExtractor(Protocol):
    def extract_candidates(
        self,
        *,
        document_hash: str,
        source_filename: str,
        pages: Sequence[PageSpan],
        batch_index: int,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Return a candidate payload and non-customer model metadata."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part or "") for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def split_page_spans(report_text: str) -> List[PageSpan]:
    """Return exact page slices with global offsets into the extracted text."""
    text = str(report_text or "")
    matches = list(PAGE_MARKER_RE.finditer(text))
    if not matches:
        return [PageSpan(page=1, text=text, char_start=0, char_end=len(text))] if text else []

    pages: List[PageSpan] = []
    for index, match in enumerate(matches):
        start = match.end()
        if start < len(text) and text[start] == "\n":
            start += 1
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pages.append(
            PageSpan(
                page=int(match.group(1)),
                text=text[start:end],
                char_start=start,
                char_end=end,
            )
        )
    return pages


def _date_values(text: str) -> List[str]:
    values: List[str] = []
    for match in DATE_TOKEN_RE.finditer(str(text or "")):
        if match.group(1):
            year, month, day = match.group(1), match.group(2), match.group(3)
        else:
            day, month, year = match.group(4), match.group(5), match.group(6)
        try:
            values.append(datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d"))
        except ValueError:
            continue
    return values


def _normalize_date_value(value: Optional[str]) -> Optional[str]:
    values = _date_values(str(value or ""))
    return values[0] if len(values) == 1 else None


def _normalize_declared_standard_value(text: Optional[str]) -> Optional[str]:
    matches = [
        (match.group(1), match.group(2))
        for match in DECLARED_STANDARD_RE.finditer(str(text or ""))
    ]
    if len(matches) != 1:
        return None
    standard_number, edition = matches[0]
    if edition:
        return f"NS {standard_number}:{edition}"
    return f"NS {standard_number}"


def _normalize_whitespace_with_map(value: str) -> NormalizedTextMap:
    """Collapse whitespace while retaining a reversible map to the original text."""
    source = str(value or "")
    output: List[str] = []
    starts: List[int] = []
    ends: List[int] = []
    index = 0
    pending_space: Optional[Tuple[int, int]] = None
    while index < len(source):
        if source[index].isspace():
            whitespace_start = index
            while index < len(source) and source[index].isspace():
                index += 1
            if output:
                pending_space = (whitespace_start, index)
            continue
        if pending_space is not None:
            output.append(" ")
            starts.append(pending_space[0])
            ends.append(pending_space[1])
            pending_space = None
        output.append(source[index])
        starts.append(index)
        ends.append(index + 1)
        index += 1
    return NormalizedTextMap("".join(output), tuple(starts), tuple(ends))


def _normalized_text(value: str) -> str:
    return _normalize_whitespace_with_map(value).text


def _provider_label_structurally_confirms(evidence_text: str, provider_name: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(evidence_text or "")).strip().casefold()
    provider = str(provider_name or "").strip().casefold()
    if not normalized or not provider or provider not in normalized:
        return False
    return any(
        marker in normalized
        for marker in (
            "autorisert foretak",
            "utført av",
            "utarbeidet av",
            "foretak:",
            "takstforetak",
            "rapportfirma",
        )
    )


def _all_occurrences(haystack: str, needle: str) -> List[int]:
    if not haystack or not needle:
        return []
    output: List[int] = []
    cursor = 0
    while True:
        index = haystack.find(needle, cursor)
        if index < 0:
            return output
        output.append(index)
        cursor = index + 1


class DeterministicEvidenceValidator:
    """Validate quotes and facts against the immutable extracted report text."""

    def __init__(
        self,
        report_text: str,
        pages: Sequence[PageSpan],
        minimum_confidence: float = 0.65,
        provider_identity_verifier: Optional[Callable[[DocumentFactCandidate, str], bool]] = None,
    ):
        self.report_text = str(report_text or "")
        self.document_hash = hashlib.sha256(self.report_text.encode("utf-8")).hexdigest()
        self.pages = list(pages)
        self.minimum_confidence = float(minimum_confidence)
        self.provider_identity_verifier = provider_identity_verifier

    def _candidate_pages(
        self,
        claimed_page: Optional[int],
        *,
        include_adjacent: bool = False,
    ) -> List[PageSpan]:
        if claimed_page is None:
            return list(self.pages)
        allowed = {claimed_page}
        if include_adjacent:
            allowed.update({claimed_page - 1, claimed_page + 1})
        return [page for page in self.pages if page.page in allowed]

    @staticmethod
    def _prefer_primary_extraction_occurrence(
        occurrences: Sequence[Tuple[PageSpan, int, int]],
    ) -> Optional[Tuple[PageSpan, int, int]]:
        """Resolve primary-text/table duplication without altering source evidence."""
        if len(occurrences) < 2 or len({item[0].page for item in occurrences}) != 1:
            return None
        page = occurrences[0][0]
        table_index = page.text.find("[TABELLDATA]")
        if table_index < 0:
            return None
        table_global_start = page.char_start + table_index
        primary = [item for item in occurrences if item[1] < table_global_start]
        table_or_later = [item for item in occurrences if item[1] >= table_global_start]
        if len(primary) == 1 and table_or_later:
            return primary[0]
        return None

    def _normalized_occurrences(
        self,
        quote: str,
        candidate_pages: Sequence[PageSpan],
        *,
        after_global_offset: int = -1,
    ) -> List[Tuple[PageSpan, int, int]]:
        normalized_quote = _normalized_text(quote)
        if not normalized_quote:
            return []
        matches: List[Tuple[PageSpan, int, int]] = []
        for page in candidate_pages:
            mapped = _normalize_whitespace_with_map(page.text)
            for normalized_start in _all_occurrences(mapped.text, normalized_quote):
                normalized_end = normalized_start + len(normalized_quote)
                local_start = mapped.original_starts[normalized_start]
                local_end = mapped.original_ends[normalized_end - 1]
                global_start = page.char_start + local_start
                global_end = page.char_start + local_end
                if global_start > after_global_offset:
                    matches.append((page, global_start, global_end))
        return matches

    @staticmethod
    def _multi_span_units(quote: str) -> List[str]:
        units: List[str] = []
        for line in re.split(r"[\r\n]+", str(quote or "")):
            for sentence in re.split(r"(?<=[.!?])\s+", line.strip()):
                normalized = _normalized_text(sentence)
                if normalized:
                    units.append(normalized)
        return units

    def _locate_multi_span(
        self,
        candidate: CandidateEvidence,
        entity_key: str,
        candidate_pages: Sequence[PageSpan],
    ) -> Tuple[List[SourceEvidence], List[str]]:
        units = self._multi_span_units(candidate.exact_quote)
        if len(units) < 2:
            return [], ["non_contiguous_evidence_unresolved"]

        spans: List[SourceEvidence] = []
        unit_index = 0
        previous_end = -1
        while unit_index < len(units):
            selected: Optional[Tuple[int, PageSpan, int, int]] = None
            for end_index in range(len(units), unit_index, -1):
                combined = " ".join(units[unit_index:end_index])
                matches = self._normalized_occurrences(
                    combined,
                    candidate_pages,
                    after_global_offset=previous_end - 1,
                )
                if len(matches) == 1:
                    page, start, end = matches[0]
                    selected = (end_index, page, start, end)
                    break
            if selected is None:
                return [], ["non_contiguous_evidence_unresolved"]
            end_index, page, start, end = selected
            original_slice = self.report_text[start:end]
            spans.append(
                self._evidence(
                    f"{entity_key}-span-{len(spans) + 1}",
                    original_slice,
                    page.page,
                    start,
                    end,
                    ["non_contiguous_candidate_mapped_to_explicit_span"],
                    match_method="multi_span_normalized",
                )
            )
            previous_end = end
            unit_index = end_index

        if len(spans) < 2:
            return [], ["non_contiguous_evidence_unresolved"]
        return spans, ["candidate_reconciled_as_multiple_source_spans"]

    def locate_spans(
        self,
        candidate: CandidateEvidence,
        entity_key: str,
        *,
        allow_multi_span: bool,
    ) -> Tuple[List[SourceEvidence], List[str]]:
        quote = candidate.exact_quote
        notes: List[str] = []
        if candidate.claimed_char_start is not None and candidate.claimed_char_end is not None:
            start = candidate.claimed_char_start
            end = candidate.claimed_char_end
            if end > start and self.report_text[start:end] == quote:
                page_match = next(
                    (page for page in self.pages if page.char_start <= start and end <= page.char_end),
                    None,
                )
                if page_match and (candidate.page is None or candidate.page == page_match.page):
                    return [self._evidence(entity_key, quote, page_match.page, start, end, notes)], notes
            notes.append("claimed_offsets_not_exact")

        occurrences: List[Tuple[PageSpan, int, int]] = []
        candidate_pages = self._candidate_pages(candidate.page, include_adjacent=allow_multi_span)
        if candidate.page is not None and not candidate_pages:
            return [], ["claimed_page_not_found"]
        for page in candidate_pages:
            cursor = 0
            while True:
                local_start = page.text.find(quote, cursor)
                if local_start < 0:
                    break
                start = page.char_start + local_start
                occurrences.append((page, start, start + len(quote)))
                cursor = local_start + 1
        if len(occurrences) == 1:
            page, start, end = occurrences[0]
            if notes:
                notes.append("offsets_recovered_from_exact_quote")
            return [self._evidence(entity_key, quote, page.page, start, end, notes)], notes

        preferred_exact = self._prefer_primary_extraction_occurrence(occurrences)
        if preferred_exact is not None:
            page, start, end = preferred_exact
            primary_notes = notes + ["duplicate_table_extraction_primary_source_selected"]
            return [self._evidence(entity_key, quote, page.page, start, end, primary_notes)], primary_notes

        exact_reason = "exact_quote_ambiguous" if occurrences else "exact_quote_not_found"
        normalized_occurrences = self._normalized_occurrences(quote, candidate_pages)
        if len(normalized_occurrences) == 1:
            page, start, end = normalized_occurrences[0]
            original_slice = self.report_text[start:end]
            normalized_notes = notes + [exact_reason, "whitespace_normalized_unique_match"]
            return [
                self._evidence(
                    entity_key,
                    original_slice,
                    page.page,
                    start,
                    end,
                    normalized_notes,
                    match_method="whitespace_normalized",
                )
            ], normalized_notes

        preferred_normalized = self._prefer_primary_extraction_occurrence(normalized_occurrences)
        if preferred_normalized is not None:
            page, start, end = preferred_normalized
            original_slice = self.report_text[start:end]
            normalized_notes = notes + [
                exact_reason,
                "whitespace_normalized_duplicate_table_primary_source_selected",
            ]
            return [
                self._evidence(
                    entity_key,
                    original_slice,
                    page.page,
                    start,
                    end,
                    normalized_notes,
                    match_method="whitespace_normalized",
                )
            ], normalized_notes

        normalized_reason = (
            "whitespace_normalized_quote_ambiguous"
            if len(normalized_occurrences) > 1
            else "whitespace_normalized_quote_not_found"
        )
        if allow_multi_span:
            multi_spans, multi_notes = self._locate_multi_span(candidate, entity_key, candidate_pages)
            if multi_spans:
                return multi_spans, notes + [exact_reason, normalized_reason] + multi_notes
        return [], notes + [exact_reason, normalized_reason]

    def locate(self, candidate: CandidateEvidence, entity_key: str) -> Tuple[Optional[SourceEvidence], List[str]]:
        spans, notes = self.locate_spans(candidate, entity_key, allow_multi_span=False)
        return (spans[0] if len(spans) == 1 else None), notes

    @staticmethod
    def _evidence(
        entity_key: str,
        quote: str,
        page: int,
        start: int,
        end: int,
        notes: List[str],
        match_method: str = "exact",
    ) -> SourceEvidence:
        return SourceEvidence(
            evidence_id=_stable_id("evidence", entity_key, page, start, end, quote),
            exact_quote=quote,
            page=page,
            char_start=start,
            char_end=end,
            quote_sha256=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
            match_method=match_method,
            validation_status=ValidationStatus.VALIDATED,
            validation_notes=list(notes),
        )

    def validate_fact(self, candidate: DocumentFactCandidate, ordinal: int) -> ValidatedDocumentFact:
        key = candidate.candidate_id or f"fact-{ordinal}"
        fact_id = _stable_id("fact", key, candidate.fact_type.value, candidate.raw_value)
        evidence, notes = self.locate(candidate.evidence, fact_id)
        status = ValidationStatus.VALIDATED
        normalized = candidate.normalized_value

        if evidence is None:
            status = ValidationStatus.AMBIGUOUS if "exact_quote_ambiguous" in notes else ValidationStatus.REJECTED
        elif candidate.confidence < self.minimum_confidence:
            status = ValidationStatus.AMBIGUOUS
            notes.append("confidence_below_validation_threshold")

        if candidate.fact_type in {
            FactType.INSPECTION_DATE,
            FactType.REPORT_DATE,
            FactType.ISSUE_DATE,
            FactType.REVISION_DATE,
            FactType.OTHER_DATE,
        }:
            normalized = _normalize_date_value(candidate.normalized_value or candidate.raw_value)
            evidenced_dates = _date_values(candidate.evidence.exact_quote)
            if not normalized:
                status = ValidationStatus.REJECTED
                notes.append("normalized_date_invalid_or_ambiguous")
            elif normalized not in evidenced_dates:
                status = ValidationStatus.REJECTED
                notes.append("normalized_date_not_present_in_evidence")
        elif candidate.fact_type == FactType.DECLARED_STANDARD:
            normalized_standard = _normalize_declared_standard_value(candidate.evidence.exact_quote)
            if not normalized_standard:
                status = ValidationStatus.REJECTED
                notes.append("declared_standard_not_unambiguous_in_evidence")
            else:
                normalized = normalized_standard
        elif candidate.fact_type in {FactType.PROVIDER, FactType.TEMPLATE}:
            normalized = str(candidate.normalized_value or candidate.raw_value).strip()
            if normalized.casefold() not in candidate.evidence.exact_quote.casefold():
                status = ValidationStatus.REJECTED
                notes.append("identity_value_not_present_in_evidence")
            elif candidate.fact_type == FactType.PROVIDER:
                # A person/company named on the report is not necessarily the report
                # platform/provider. A provider fast path may confirm this later; the
                # general AI candidate is retained but cannot self-authorize identity.
                structurally_confirmed = _provider_label_structurally_confirms(
                    candidate.evidence.exact_quote,
                    normalized,
                )
                if self.provider_identity_verifier and self.provider_identity_verifier(candidate, self.report_text):
                    structurally_confirmed = True
                if structurally_confirmed:
                    notes.append("provider_structurally_confirmed")
                else:
                    status = ValidationStatus.AMBIGUOUS
                    notes.append("provider_requires_structural_confirmation")

        return ValidatedDocumentFact(
            fact_id=fact_id,
            fact_type=candidate.fact_type,
            raw_value=candidate.raw_value,
            normalized_value=normalized,
            confidence=candidate.confidence,
            candidate_evidence=candidate.evidence,
            evidence=evidence,
            validation_status=status,
            validation_notes=notes,
        )

    def validate_segment(self, candidate: SegmentCandidate, ordinal: int) -> ValidatedSegment:
        key = candidate.candidate_id or f"segment-{ordinal}"
        segment_id = _stable_id("segment", key, candidate.kind.value, candidate.title)
        evidence_spans, notes = self.locate_spans(candidate.evidence, segment_id, allow_multi_span=True)
        evidence = evidence_spans[0] if evidence_spans else None
        if evidence is not None:
            segment_id = _stable_id(
                "segment", self.document_hash, evidence.page, evidence.char_start,
                evidence.char_end, candidate.point_label or "",
            )
        status = ValidationStatus.VALIDATED
        if evidence is None:
            status = (
                ValidationStatus.AMBIGUOUS
                if any("ambiguous" in note for note in notes)
                else ValidationStatus.REJECTED
            )
        elif candidate.confidence < self.minimum_confidence:
            status = ValidationStatus.AMBIGUOUS
            notes.append("confidence_below_validation_threshold")

        tg_grade = re.sub(r"\s+", "", candidate.tg_grade.strip().upper()) if candidate.tg_grade else None
        if tg_grade and not TG_RE.fullmatch(tg_grade):
            status = ValidationStatus.REJECTED
            notes.append("invalid_tg_grade")

        return ValidatedSegment(
            segment_id=segment_id,
            kind=candidate.kind,
            title=candidate.title,
            professional_subject=candidate.professional_subject,
            point_label=candidate.point_label,
            tg_grade=tg_grade,
            confidence=candidate.confidence,
            candidate_evidence=candidate.evidence,
            evidence=evidence,
            evidence_spans=evidence_spans,
            validation_status=status,
            validation_notes=notes,
        )


class BedrockDocumentCandidateExtractor:
    """Small JSON-only Bedrock adapter for A2 facts and structure candidates."""

    SYSTEM_PROMPT = """You extract document facts and structure from Norwegian condition reports.
Do not assess quality, compliance, ARKAT adequacy, legality, methodology, scoring, or findings.
Return candidates only. Every candidate must quote exact contiguous source text and its page.
If uncertain, omit the candidate and add an abstention. Never invent text, dates, standards, points,
providers, templates, or TG grades. Return only JSON matching the requested schema."""

    def __init__(self, bedrock_client: Optional[Any] = None, max_tokens: int = 7000):
        self._client = bedrock_client
        self.max_tokens = max_tokens

    def _bedrock(self):
        if self._client is None:
            from app.config import settings
            from app.services.bedrock_ai import BedrockAI

            self._client = BedrockAI(region=settings.AWS_REGION)
        return self._client

    def extract_candidates(
        self,
        *,
        document_hash: str,
        source_filename: str,
        pages: Sequence[PageSpan],
        batch_index: int,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        page_text = "\n\n".join(f"[SIDE {page.page}]\n{page.text}" for page in pages)
        schema = CandidateBatch.model_json_schema()
        user_prompt = (
            f"Document SHA-256: {document_hash}\n"
            f"Filename: {source_filename}\n"
            f"Batch: {batch_index}\n\n"
            "Extract candidate facts (inspection/report/issue/other dates, declared NS standard, provider, "
            "template) and candidate sections/report points. For each, return an exact_quote copied "
            "verbatim from one page. For every segment, quote enough adjacent context (normally at least "
            "80 characters) to make the excerpt unique on that page; never use ellipses or rewrite whitespace. "
            "claimed_char_start/end may be null; deterministic code resolves them. "
            "Confidence is 0..1. Use report_point only for an identifiable assessment point.\n\n"
            f"JSON schema:\n{_canonical_json(schema)}\n\n"
            f"Report pages:\n{page_text}"
        )
        payload, meta = self._bedrock().generate_json_with_claude(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=self.max_tokens,
            retry_json_prompt=True,
            return_meta=True,
        )
        return payload, dict(meta or {})


class DocumentUnderstandingService:
    """Run A2 in isolation with strict deterministic admission of AI candidates."""

    def __init__(
        self,
        candidate_extractor: CandidateExtractor,
        *,
        fast_path_extractor: Optional[CandidateExtractor] = None,
        maximum_batch_chars: int = 45000,
        minimum_confidence: float = 0.65,
        provider_identity_verifier: Optional[Callable[[DocumentFactCandidate, str], bool]] = None,
        verified_cover_tg_counts: Optional[Dict[str, int]] = None,
    ):
        if maximum_batch_chars < 1000:
            raise ValueError("maximum_batch_chars must be at least 1000")
        self.candidate_extractor = candidate_extractor
        self.fast_path_extractor = fast_path_extractor
        self.maximum_batch_chars = maximum_batch_chars
        self.minimum_confidence = minimum_confidence
        self.provider_identity_verifier = provider_identity_verifier
        self.verified_cover_tg_counts = {
            str(key).upper(): int(value)
            for key, value in (verified_cover_tg_counts or {}).items()
        }

    def _batches(self, pages: Sequence[PageSpan]) -> List[List[PageSpan]]:
        batches: List[List[PageSpan]] = []
        current: List[PageSpan] = []
        current_chars = 0
        for page in pages:
            page_chars = len(page.text)
            if current and current_chars + page_chars > self.maximum_batch_chars:
                batches.append(current)
                current = []
                current_chars = 0
            current.append(page)
            current_chars += page_chars
        if current:
            batches.append(current)
        return batches

    def _call_extractor(
        self,
        extractor: CandidateExtractor,
        *,
        document_hash: str,
        source_filename: str,
        pages: Sequence[PageSpan],
        batch_index: int,
    ) -> Tuple[CandidateBatch, Dict[str, Any]]:
        payload, metadata = extractor.extract_candidates(
            document_hash=document_hash,
            source_filename=source_filename,
            pages=pages,
            batch_index=batch_index,
        )
        return CandidateBatch.model_validate(payload), metadata

    def analyze(
        self,
        report_text: str,
        source_filename: str,
        *,
        source_pdf_sha256: Optional[str] = None,
    ) -> DocumentUnderstandingResult:
        text = str(report_text or "")
        if not text.strip():
            raise ValueError("report_text must contain readable text")
        if not str(source_filename or "").strip():
            raise ValueError("source_filename is required")
        if source_pdf_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", source_pdf_sha256):
            raise ValueError("source_pdf_sha256 must be a lowercase SHA-256 value")

        document_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        run_id = f"a2_{uuid.uuid4().hex}"
        pages = split_page_spans(text)
        validator = DeterministicEvidenceValidator(
            text,
            pages,
            self.minimum_confidence,
            self.provider_identity_verifier,
        )
        candidates: List[CandidateBatch] = []
        candidate_batch_indices: List[int] = []
        model_metadata: List[Dict[str, Any]] = []
        abstentions: List[Abstention] = []
        fallback_reasons: List[str] = []

        batches = self._batches(pages)
        for batch_index, batch_pages in enumerate(batches, start=1):
            chosen = self.fast_path_extractor or self.candidate_extractor
            try:
                candidate_batch, metadata = self._call_extractor(
                    chosen,
                    document_hash=document_hash,
                    source_filename=source_filename,
                    pages=batch_pages,
                    batch_index=batch_index,
                )
                # A provider fast path is an optimization only. If it cannot segment
                # a batch, fall back to the general AI extractor instead of stopping.
                if self.fast_path_extractor is not None and not candidate_batch.segments:
                    fallback_reasons.append(f"batch_{batch_index}:provider_fast_path_returned_no_segments")
                    candidate_batch, metadata = self._call_extractor(
                        self.candidate_extractor,
                        document_hash=document_hash,
                        source_filename=source_filename,
                        pages=batch_pages,
                        batch_index=batch_index,
                    )
                candidates.append(candidate_batch)
                candidate_batch_indices.append(batch_index)
                payload_hash = hashlib.sha256(
                    _canonical_json(candidate_batch.model_dump(mode="json")).encode("utf-8")
                ).hexdigest()
                model_metadata.append({
                    "batch_index": batch_index,
                    "candidate_payload_sha256": payload_hash,
                    **dict(metadata or {}),
                })
            except Exception as exc:
                abstentions.append(
                    Abstention(
                        abstention_id=_stable_id("abstention", document_hash, batch_index, type(exc).__name__),
                        stage="document_understanding",
                        subject=f"batch_{batch_index}",
                        reason_code="candidate_extraction_failed",
                        explanation=(
                            f"Candidate extraction failed closed: {type(exc).__name__}."
                        ),
                    )
                )

        facts: List[ValidatedDocumentFact] = []
        segment_records: List[Tuple[int, SegmentCandidate, ValidatedSegment]] = []
        for actual_batch_index, candidate_batch in zip(candidate_batch_indices, candidates):
            for item in candidate_batch.facts:
                fact = validator.validate_fact(item, len(facts) + 1)
                facts.append(fact)
            for item in candidate_batch.segments:
                segment = validator.validate_segment(item, len(segment_records) + 1)
                segment_records.append((actual_batch_index, item, segment))
            for index, raw in enumerate(candidate_batch.abstentions, start=1):
                abstentions.append(
                    Abstention(
                        abstention_id=_stable_id("abstention", document_hash, len(abstentions), index, raw),
                        stage="ai_candidate_extraction",
                        subject=str(raw.get("subject") or "unspecified")[:500],
                        reason_code=str(raw.get("reason_code") or "model_abstained")[:120],
                        explanation=str(raw.get("explanation") or "The model abstained.")[:2000],
                        candidate_id=str(raw.get("candidate_id"))[:120] if raw.get("candidate_id") else None,
                    )
                )

        validated_fact_types = {
            item.fact_type for item in facts if item.validation_status == ValidationStatus.VALIDATED
        }
        facts.extend(self._deterministic_labeled_date_facts(text, pages))
        facts = self._dedupe_facts(facts)
        facts = self._prefer_explicit_labeled_dates(facts)
        for fact in facts:
            if fact.validation_status != ValidationStatus.VALIDATED:
                abstentions.append(self._candidate_abstention("fact_validation", fact.fact_id, fact.validation_notes))
        segments, dispositions, segment_abstentions = self._reconcile_segments(segment_records)
        abstentions.extend(segment_abstentions)
        source_inventory = PhysicalSourceInventoryBuilder().build(report_text, document_hash)
        segments, coverage_reconciliation = self._reconcile_source_inventory(
            document_hash, source_inventory, segments
        )
        coverage = self._build_source_coverage(segments, dispositions)
        summary_blockers = self._declared_tg_summary_blockers(report_text, segments)
        body_blockers = [
            f"complete_point_body_missing:{segment.segment_id}"
            for segment in segments
            if segment.kind == SegmentKind.REPORT_POINT
            and segment.validation_status == ValidationStatus.VALIDATED
            and not segment.bound_body_spans
        ]
        structural_blockers = []
        reconciled_segment_by_inventory_id = {
            item.inventory_id: next(
                (segment for segment in segments if segment.segment_id == item.matched_segment_id),
                None,
            )
            for item in coverage_reconciliation
            if item.inventory_role == InventoryRole.PRIMARY and item.matched_segment_id
        }
        for point in source_inventory.points:
            if point.role == InventoryRole.PRIMARY:
                reconciled = reconciled_segment_by_inventory_id.get(point.inventory_id)
                if point.boundary_status != "validated":
                    structural_blockers.append(f"physical_boundary_uncertain:{point.inventory_id}")
                if point.point_type == "unknown" and not (
                    reconciled and reconciled.point_type != "unknown"
                ):
                    structural_blockers.append(f"physical_point_type_uncertain:{point.inventory_id}")
                if not point.body_spans or point.char_end != point.body_spans[-1].char_end:
                    structural_blockers.append(f"physical_body_span_incomplete:{point.inventory_id}")
            elif point.role == InventoryRole.SUMMARY and not point.linked_primary_id:
                reason = "ambiguous" if point.link_status == "ambiguous" else "unresolved"
                structural_blockers.append(f"summary_primary_link_{reason}:{point.inventory_id}")
        structural_blockers.extend(
            f"overlapping_assessment_identity_uncertain:{segment.segment_id}"
            for segment in segments
            if "structural_uncertainty_multiple_physical_overlap" in segment.validation_notes
        )
        structural_blockers.extend(
            self._structural_reconciliation_blockers(
                segments, coverage_reconciliation, abstentions
            )
        )
        if summary_blockers or body_blockers or structural_blockers:
            coverage = coverage.model_copy(update={
                "completion_blockers": [
                    *coverage.completion_blockers,
                    *summary_blockers,
                    *body_blockers,
                    *structural_blockers,
                ]
            })
        report_quality_observations = self._report_quality_observations(
            document_hash, report_text, segments, self.verified_cover_tg_counts
        )
        trace_records = self._trace(document_hash, facts, segments, dispositions)
        valid_segments = [item for item in segments if item.validation_status == ValidationStatus.VALIDATED]
        if not candidates or not valid_segments:
            status = UnderstandingStatus.FAILED
        elif abstentions or coverage.completion_blockers:
            status = UnderstandingStatus.COMPLETE_WITH_ABSTENTIONS
        else:
            status = UnderstandingStatus.COMPLETE

        if self.fast_path_extractor is None:
            route_used = "phase_a2_general_ai_document_understanding"
        elif fallback_reasons:
            route_used = "phase_a2_provider_fast_path_with_general_fallback"
        else:
            route_used = "phase_a2_provider_fast_path"

        return DocumentUnderstandingResult(
            run_id=run_id,
            document_hash=document_hash,
            source_filename=source_filename,
            route_used=route_used,
            fallback_reason="; ".join(fallback_reasons) if fallback_reasons else None,
            source_pdf_sha256=source_pdf_sha256,
            extracted_text_sha256=document_hash,
            status=status,
            facts=facts,
            segments=segments,
            abstentions=abstentions,
            batch_count=len(batches),
            candidate_batches=candidates,
            candidate_dispositions=dispositions,
            segment_coverage=coverage,
            source_inventory=source_inventory,
            coverage_reconciliation=coverage_reconciliation,
            report_quality_observations=report_quality_observations,
            model_metadata=model_metadata,
            trace_records=trace_records,
        )

    @staticmethod
    def _reconcile_source_inventory(
        document_hash: str,
        inventory,
        ai_segments: Sequence[ValidatedSegment],
    ) -> Tuple[List[ValidatedSegment], List[CoverageReconciliation]]:
        output: List[ValidatedSegment] = []
        reconciliation: List[CoverageReconciliation] = []
        primary_segment_by_inventory_id: Dict[str, str] = {}
        matched_ai_segment_ids: set[str] = set()
        for point in (item for item in inventory.points if item.role == InventoryRole.PRIMARY):
            overlaps = [
                segment for segment in ai_segments
                if segment.validation_status == ValidationStatus.VALIDATED
                and segment.evidence is not None
                and any(
                    span.char_start <= segment.evidence.char_start < span.char_end
                    for span in (point.body_spans or [point.body])
                )
            ]
            def norm(value: str | None) -> str:
                return re.sub(r"\W+", "", (value or "").casefold())

            def structural_tg_authoritative() -> bool:
                return point.detection_method in {
                    "physical_numbered_tg_heading",
                    "general_physical_tg_heading",
                    "physical_bolavi_tg_heading",
                    "physical_cross_page_uninvestigated_section",
                    "physical_uninvestigated_semantic",
                }

            def rescue_candidate(segment: ValidatedSegment) -> bool:
                if segment.validation_status == ValidationStatus.VALIDATED:
                    return False
                if segment.candidate_evidence.page != point.page:
                    return False
                return {
                    "exact_quote_not_found",
                    "whitespace_normalized_quote_not_found",
                } <= set(segment.validation_notes)

            def score(segment: ValidatedSegment) -> tuple[int, int]:
                value = 0
                if segment.evidence:
                    value += max(0, 1000 - abs(segment.evidence.char_start - point.char_start)) // 50
                if norm(segment.title) == norm(point.title):
                    value += 40
                elif norm(segment.title) in norm(point.title) or norm(point.title) in norm(segment.title):
                    value += 15
                if segment.point_label and point.point_label and segment.point_label == point.point_label:
                    value += 30
                if segment.tg_grade and point.tg_grade and segment.tg_grade == point.tg_grade:
                    value += 20
                elif (
                    structural_tg_authoritative()
                    and segment.tg_grade and point.tg_grade
                    and segment.tg_grade != point.tg_grade
                ):
                    value -= 50
                body = "\n".join(span.exact_quote for span in (point.body_spans or [point.body])).casefold()
                title_tokens = re.findall(r"\w+", segment.title.casefold())
                value += min(20, sum(2 for token in title_tokens if len(token) > 2 and token in body))
                section_tokens = {
                    token for token in re.findall(r"\w+", point.section_context.casefold()) if len(token) > 4
                }
                subject_tokens = {
                    token for token in re.findall(r"\w+", segment.professional_subject.casefold()) if len(token) > 4
                }
                value += 10 * bool(section_tokens & subject_tokens)
                return value, -(segment.evidence.char_start if segment.evidence else 0)

            def identity_compatible(segment: ValidatedSegment) -> bool:
                physical_title = norm(point.title)
                candidate_title = norm(segment.title)
                title_compatible = bool(
                    physical_title and candidate_title and (
                        physical_title == candidate_title
                        or physical_title in candidate_title
                        or candidate_title in physical_title
                    )
                )
                label_compatible = bool(
                    point.point_label and segment.point_label
                    and point.point_label == segment.point_label
                )
                physical_tokens = {
                    token for token in re.findall(r"\w+", point.title.casefold()) if len(token) > 3
                }
                candidate_tokens = {
                    token for token in re.findall(r"\w+", segment.title.casefold()) if len(token) > 3
                }
                token_compatible = bool(
                    physical_tokens and candidate_tokens
                    and len(physical_tokens & candidate_tokens) / min(
                        len(physical_tokens), len(candidate_tokens)
                    ) >= 0.6
                )
                tg_compatible = not (
                    structural_tg_authoritative()
                    and point.tg_grade and segment.tg_grade
                    and point.tg_grade != segment.tg_grade
                )
                return tg_compatible and (title_compatible or label_compatible or token_compatible)

            def resolved_point_type(segment: ValidatedSegment | None) -> str:
                if point.point_type != "unknown":
                    return point.point_type
                candidate_tg = str((segment.tg_grade if segment else point.tg_grade) or "").upper().replace(" ", "")
                if candidate_tg == "TGIU":
                    return "tgiu"
                if candidate_tg.startswith("TG"):
                    return "graded"
                return "unknown"

            compatible = [segment for segment in overlaps if identity_compatible(segment)]
            incompatible = [segment for segment in overlaps if segment not in compatible]
            rescuable = [
                segment for segment in ai_segments
                if rescue_candidate(segment) and identity_compatible(segment)
            ]
            ranked = sorted(compatible, key=score, reverse=True)
            rescue_ranked = sorted(rescuable, key=score, reverse=True)
            matched = ranked[0] if ranked and score(ranked[0])[0] >= 10 else None
            matched_from_rescue = False
            if matched is None and rescue_ranked and score(rescue_ranked[0])[0] >= 10:
                matched = rescue_ranked[0]
                matched_from_rescue = True
            conflict = len(ranked) > 1 and score(ranked[0])[0] == score(ranked[1])[0]
            if conflict:
                matched = None
                matched_from_rescue = False
            segment_id = _stable_id(
                "segment", document_hash, point.page, point.char_start,
                point.char_end, point.point_label or point.structural_marker,
            )
            output.append(ValidatedSegment(
                segment_id=segment_id,
                kind=SegmentKind.REPORT_POINT,
                title=point.title,
                section_context=point.section_context,
                professional_subject=matched.professional_subject if matched else point.title,
                point_label=point.point_label or (matched.point_label if matched else None),
                tg_grade=(
                    point.tg_grade
                    if point.tg_grade and (structural_tg_authoritative() or not matched or not matched.tg_grade)
                    else (matched.tg_grade if matched else None)
                ),
                point_type=resolved_point_type(matched),
                confidence=matched.confidence if matched else 1.0,
                candidate_evidence=(
                    matched.candidate_evidence if matched else CandidateEvidence(
                        exact_quote=point.structural_marker,
                        page=point.page,
                        claimed_char_start=point.char_start,
                        claimed_char_end=point.char_start + len(point.structural_marker),
                    )
                ),
                evidence=point.body,
                evidence_spans=matched.evidence_spans if matched and matched.evidence_spans else [point.body],
                bound_body_spans=point.body_spans or [point.body],
                bound_body_sha256=(
                    (point.body_spans or [point.body])[0].quote_sha256
                    if len(point.body_spans or [point.body]) == 1
                    else hashlib.sha256(
                        json.dumps(
                            [span.model_dump(mode="json") for span in (point.body_spans or [point.body])],
                            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                ),
                validation_status=ValidationStatus.VALIDATED,
                validation_notes=[
                    "source_inventory_authoritative_boundary",
                    "ai_candidate_matched" if matched else "source_inventory_materialized_without_ai_candidate",
                    *(["ai_candidate_semantically_reconciled_without_exact_evidence"] if matched_from_rescue else []),
                    *(
                        ["non_authoritative_structural_tg_replaced_by_ai_candidate"]
                        if matched and matched.tg_grade and point.tg_grade != matched.tg_grade and not structural_tg_authoritative()
                        else []
                    ),
                    *(["ai_reconciliation_conflict_traceable_no_first_match"] if conflict else []),
                    *(["ai_candidate_identity_conflict_rejected"] if incompatible else []),
                ],
            ))
            primary_segment_by_inventory_id[point.inventory_id] = segment_id
            if matched is not None:
                matched_ai_segment_ids.add(matched.segment_id)
            reconciliation.append(CoverageReconciliation(
                inventory_id=point.inventory_id,
                inventory_role=point.role,
                matched_segment_id=segment_id,
                status="matched" if matched else "source_materialized",
                reason=(
                    "AI candidate reconciled to independently derived physical point."
                    if matched else (
                        "Conflicting AI candidates were not silently attached; source inventory retained."
                        if conflict else
                        "Overlapping AI candidate identity conflicted with physical title/label/TG; source inventory retained."
                        if incompatible else
                        "Physical point absent from AI candidates; materialized from source inventory."
                    )
                ),
            ))
        for segment in ai_segments:
            if (
                segment.validation_status != ValidationStatus.VALIDATED
                or segment.kind != SegmentKind.REPORT_POINT
                or segment.segment_id in matched_ai_segment_ids
            ):
                continue
            if any(
                item.kind == SegmentKind.REPORT_POINT
                and re.sub(r"\W+", "", item.title.casefold()) == re.sub(r"\W+", "", segment.title.casefold())
                and item.section_context == segment.section_context
                for item in output
            ):
                continue
            if segment.evidence is None:
                continue
            body_spans = segment.bound_body_spans or segment.evidence_spans or [segment.evidence]
            output.append(segment.model_copy(update={
                "evidence_spans": body_spans,
                "bound_body_spans": body_spans,
                "point_type": (
                    "tgiu" if str(segment.tg_grade or "").upper().replace(" ", "") == "TGIU"
                    else "graded" if str(segment.tg_grade or "").upper().startswith("TG")
                    else segment.point_type
                ),
                "bound_body_sha256": (
                    body_spans[0].quote_sha256
                    if len(body_spans) == 1 else hashlib.sha256(
                        json.dumps(
                            [span.model_dump(mode="json") for span in body_spans],
                            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                ),
                "validation_notes": [
                    *segment.validation_notes,
                    "ai_candidate_authoritative_fallback_missing_physical_point",
                ],
            }))
            reconciliation.append(CoverageReconciliation(
                inventory_id=_stable_id("inventory", document_hash, segment.segment_id, "ai_fallback"),
                inventory_role=InventoryRole.PRIMARY,
                matched_segment_id=segment.segment_id,
                status="matched",
                reason=(
                    "Validated AI point was retained because the physical inventory missed the point; "
                    "reconciliation must not silently drop an admitted report point."
                ),
            ))
        for point in (item for item in inventory.points if item.role == InventoryRole.SUMMARY):
            primary_segment_id = primary_segment_by_inventory_id.get(point.linked_primary_id or "")
            summary_segment_id = _stable_id(
                "summary", document_hash, point.page, point.char_start,
                point.char_end, point.inventory_id,
            )
            output.append(ValidatedSegment(
                segment_id=summary_segment_id,
                kind=SegmentKind.SUMMARY,
                title=point.title,
                section_context=point.section_context,
                professional_subject=point.title,
                point_label=point.point_label,
                tg_grade=point.tg_grade,
                point_type="summary",
                confidence=1.0,
                candidate_evidence=CandidateEvidence(
                    exact_quote=point.structural_marker,
                    page=point.page,
                    claimed_char_start=point.char_start,
                    claimed_char_end=point.char_start + len(point.structural_marker),
                ),
                evidence=point.body,
                evidence_spans=point.body_spans or [point.body],
                bound_body_spans=point.body_spans or [point.body],
                bound_body_sha256=hashlib.sha256(
                    json.dumps(
                        [span.model_dump(mode="json") for span in (point.body_spans or [point.body])],
                        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                validation_status=ValidationStatus.VALIDATED,
                validation_notes=["hierarchy_validated_summary_support_only"],
                supporting_primary_segment_id=primary_segment_id,
            ))
            reconciliation.append(CoverageReconciliation(
                inventory_id=point.inventory_id,
                inventory_role=point.role,
                matched_segment_id=primary_segment_id,
                status="linked_summary" if primary_segment_id else "missing",
                reason=(
                    "Summary linked to primary point and excluded from independent assessment."
                    if primary_segment_id else
                    f"Summary excluded from assessment; {point.link_reason}"
                ),
            ))
        for point in (item for item in inventory.points if item.role == InventoryRole.NAVIGATION):
            reconciliation.append(CoverageReconciliation(
                inventory_id=point.inventory_id,
                inventory_role=point.role,
                matched_segment_id=None,
                status="missing",
                reason="Navigation content was classified and excluded from report-point assessment.",
            ))
        # AI sections overlapping one physical assessment object are supporting
        # context, not independent applicability objects. Ambiguous multi-parent
        # overlap remains traceable and blocks completion downstream.
        physical_segments = list(output)
        for segment in (
            item for item in ai_segments
            if item.kind not in {SegmentKind.REPORT_POINT, SegmentKind.SUMMARY}
        ):
            evidence = segment.evidence
            parents = []
            if evidence is not None:
                for physical in physical_segments:
                    if any(
                        span.char_start <= evidence.char_start < span.char_end
                        for span in physical.bound_body_spans
                    ):
                        parents.append(physical)
            if not parents:
                candidate_identity = re.sub(r"\W+", "", segment.title.casefold())
                semantic_parents = []
                for physical in physical_segments:
                    physical_identity = re.sub(r"\W+", "", physical.title.casefold())
                    if (
                        physical_identity and candidate_identity
                        and (physical_identity in candidate_identity or candidate_identity in physical_identity)
                    ):
                        semantic_parents.append(physical)
                if len(semantic_parents) == 1:
                    parents = semantic_parents
            if len(parents) == 1:
                output.append(segment.model_copy(update={
                    "supporting_primary_segment_id": parents[0].segment_id,
                    "validation_notes": [
                        *segment.validation_notes,
                        "supporting_context_only_physical_overlap",
                    ],
                }))
            elif len(parents) > 1:
                output.append(segment.model_copy(update={
                    "validation_notes": [
                        *segment.validation_notes,
                        "structural_uncertainty_multiple_physical_overlap",
                    ],
                }))
            else:
                output.append(segment)
        return output, reconciliation

    @staticmethod
    def _report_quality_observations(
        document_hash: str,
        report_text: str,
        segments: Sequence[ValidatedSegment],
        verified_cover_tg_counts: Dict[str, int],
    ) -> List[ReportQualityObservation]:
        normalized = _normalized_text(report_text).casefold()
        if "tilstandsgrader" not in normalized or "sammendrag" not in normalized:
            return []
        detailed: Dict[str, int] = {}
        for segment in segments:
            if segment.kind == SegmentKind.REPORT_POINT and segment.tg_grade:
                detailed[segment.tg_grade] = detailed.get(segment.tg_grade, 0) + 1
        discrepancies = {
            tg: {"cover": count, "detailed": detailed.get(tg, 0)}
            for tg, count in verified_cover_tg_counts.items()
            if detailed.get(tg, 0) != count
        }
        message = (
            f"Verified cover-summary discrepancies: {discrepancies}. "
            if discrepancies else ""
        ) + (
            "Detailed physical point enumeration is authoritative. Cover-summary discrepancies "
            f"are report-quality observations, not A2 coverage failures. Detailed counts: {detailed}."
        )
        return [ReportQualityObservation(
            observation_id=_stable_id("quality", document_hash, "cover_vs_detail"),
            observation_type="cover_summary_vs_detailed_points",
            message=message,
            evidence_ids=[],
            blocks_analysis_completion=False,
        )]

    @staticmethod
    def _bind_complete_point_bodies(
        report_text: str,
        segments: Sequence[ValidatedSegment],
    ) -> List[ValidatedSegment]:
        """Bind each report point from its anchor through the next point anchor.

        The short evidence anchor remains unchanged. The additional body slice is
        the semantic assessment input and maps directly back to original offsets.
        """
        points = sorted(
            (
                segment for segment in segments
                if segment.kind == SegmentKind.REPORT_POINT
                and segment.validation_status == ValidationStatus.VALIDATED
                and segment.evidence is not None
            ),
            key=lambda segment: segment.evidence.char_start,
        )
        starts = [segment.evidence.char_start for segment in points]
        page_ends = {page.page: page.char_end for page in split_page_spans(report_text)}
        replacements: Dict[str, ValidatedSegment] = {}
        for index, segment in enumerate(points):
            start = segment.evidence.char_start
            end = starts[index + 1] if index + 1 < len(starts) else page_ends.get(segment.evidence.page, len(report_text))
            exact = report_text[start:end].rstrip()
            end = start + len(exact)
            if not exact:
                continue
            page = segment.evidence.page
            span = SourceEvidence(
                evidence_id=_stable_id("body", segment.segment_id, start, end),
                exact_quote=exact,
                page=page,
                char_start=start,
                char_end=end,
                quote_sha256=hashlib.sha256(exact.encode("utf-8")).hexdigest(),
                match_method="exact",
                validation_status=ValidationStatus.VALIDATED,
                validation_notes=["complete_point_body_bound_to_next_report_point"],
            )
            replacements[segment.segment_id] = segment.model_copy(update={
                "bound_body_spans": [span],
                "bound_body_sha256": span.quote_sha256,
            })
        return [replacements.get(segment.segment_id, segment) for segment in segments]

    @staticmethod
    def _declared_tg_summary_blockers(
        report_text: str,
        segments: Sequence[ValidatedSegment],
    ) -> List[str]:
        declared: Dict[str, int] = {}
        for line in report_text.splitlines():
            compact = " ".join(line.split())
            if len(compact) > 200:
                continue
            for count, tg in re.findall(
                r"(?i)(?<![.\d])(\d{1,3})\s+(TG[0-3]|TGIU)(?:\s+(?:punkter?|forhold|avvik))?\b",
                compact,
            ):
                declared[tg.upper()] = int(count)
            for tg, count in re.findall(
                r"(?i)\b(TG[0-3]|TGIU)\s*[:=]\s*(\d{1,3})\b",
                compact,
            ):
                declared[tg.upper()] = int(count)
        actual: Dict[str, int] = {}
        for segment in segments:
            if segment.kind == SegmentKind.REPORT_POINT and segment.validation_status == ValidationStatus.VALIDATED:
                tg = (segment.tg_grade or "NO_TG").upper()
                actual[tg] = actual.get(tg, 0) + 1
        blockers = [
            f"declared_tg_summary_mismatch:{tg}:declared={count}:bound={actual.get(tg, 0)}"
            for tg, count in sorted(declared.items())
            if actual.get(tg, 0) != count
        ]
        return blockers

    @staticmethod
    def _looks_like_non_point_structural_title(title: str) -> bool:
        compact = " ".join(str(title or "").split())
        if not compact:
            return True
        if re.fullmatch(r"(?i)(?:\d+\s+)?stk", compact):
            return True
        lowered = compact.casefold()
        if lowered.startswith("utskrift:") or lowered.startswith("kontakt:"):
            return True
        word_tokens = re.findall(r"[A-Za-zÆØÅæøå]{2,}", compact)
        if not word_tokens:
            return True
        metadata_tokens = {"utskrift", "telefon", "kontakt", "org", "nettsiden", "side"}
        if ":" in compact and metadata_tokens & {token.casefold() for token in word_tokens}:
            return True
        if len(compact.split()) >= 8 and any(marker in compact for marker in (".", ";", ":")):
            return True
        return False

    @classmethod
    def _structural_reconciliation_blockers(
        cls,
        segments: Sequence[ValidatedSegment],
        coverage_reconciliation: Sequence[CoverageReconciliation],
        abstentions: Sequence[Abstention],
    ) -> List[str]:
        primary_segments = {
            item.segment_id: item
            for item in segments
            if item.kind == SegmentKind.REPORT_POINT
            and item.validation_status == ValidationStatus.VALIDATED
        }
        primary_reconciliation = [
            item for item in coverage_reconciliation
            if item.inventory_role == InventoryRole.PRIMARY
        ]
        if not primary_reconciliation:
            return []
        suspicious_materialized: List[str] = []
        ai_fallback_count = 0
        for item in primary_reconciliation:
            reason = item.reason or ""
            if "physical inventory missed the point" in reason:
                ai_fallback_count += 1
            if item.status != "source_materialized" or not item.matched_segment_id:
                continue
            segment = primary_segments.get(item.matched_segment_id)
            if segment and cls._looks_like_non_point_structural_title(segment.title):
                suspicious_materialized.append(segment.segment_id)
        structural_abstention_count = sum(
            1
            for item in abstentions
            if item.reason_code in {
                "provider_requires_structural_confirmation",
                "exact_quote_ambiguous",
                "exact_quote_not_found",
                "whitespace_normalized_quote_not_found",
            }
        )
        primary_count = len(primary_reconciliation)
        if not suspicious_materialized:
            return []
        materially_unreliable = (
            len(suspicious_materialized) >= 2
            or ai_fallback_count >= max(5, primary_count // 2)
            or structural_abstention_count >= 5
        )
        if not materially_unreliable:
            return []
        return [
            "structural_reconciliation_unreliable:"
            f"suspicious_materialized={len(suspicious_materialized)}:"
            f"ai_fallback={ai_fallback_count}:"
            f"structural_abstentions={structural_abstention_count}:"
            f"primary_points={primary_count}"
        ]

    @staticmethod
    def _deterministic_labeled_date_facts(
        report_text: str,
        pages: Sequence[PageSpan],
    ) -> List[ValidatedDocumentFact]:
        """Recover explicit labelled dates without choosing a governing date."""
        patterns = (
            (FactType.INSPECTION_DATE, r"(?i)\b(?:befaringsdato|befaring(?:sdato)?)(?:\s*\(cid:\d+\))*\s*[:–—-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})"),
            (FactType.REPORT_DATE, r"(?i)\b(?:rapportdato|rapport\s+utstedt|utstedelsesdato)(?:\s*\(cid:\d+\))*\s*[:–—-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})"),
            (FactType.INSPECTION_DATE, r"(?is)\bBefaring\s+Dato\s+Til\s+stede\s+Rolle\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})"),
            (FactType.REPORT_DATE, r"(?is)\bRevisjoner\s+Versjon\s+Ny\s+versjon\s+Kommentar\s+\d+\s+(\d{1,2}[./-]\d{1,2}[./-]\d{4})"),
        )
        output: List[ValidatedDocumentFact] = []
        for fact_type, pattern in patterns:
            for match in re.finditer(pattern, report_text):
                normalized = _normalize_date_value(match.group(1))
                if not normalized:
                    continue
                page = next((item.page for item in pages if item.char_start <= match.start() < item.char_end), 1)
                exact = match.group(0)
                evidence = SourceEvidence(
                    evidence_id=_stable_id("source", fact_type.value, match.start(), match.end()),
                    exact_quote=exact,
                    page=page,
                    char_start=match.start(),
                    char_end=match.end(),
                    quote_sha256=hashlib.sha256(exact.encode("utf-8")).hexdigest(),
                    validation_status=ValidationStatus.VALIDATED,
                    validation_notes=["deterministic_explicit_date_label"],
                )
                output.append(ValidatedDocumentFact(
                    fact_id=_stable_id("fact", fact_type.value, normalized, match.start()),
                    fact_type=fact_type,
                    raw_value=match.group(1),
                    normalized_value=normalized,
                    confidence=1.0,
                    candidate_evidence=CandidateEvidence(exact_quote=exact, page=page),
                    evidence=evidence,
                    validation_status=ValidationStatus.VALIDATED,
                    validation_notes=["deterministic_explicit_date_label"],
                ))
        return output

    @staticmethod
    def _candidate_abstention(stage: str, entity_id: str, notes: Iterable[str]) -> Abstention:
        reason = next(iter(notes), "validation_failed")
        return Abstention(
            abstention_id=_stable_id("abstention", stage, entity_id, reason),
            stage=stage,
            subject=entity_id,
            reason_code=str(reason)[:120],
            explanation="Candidate was not admitted because deterministic validation did not pass.",
            candidate_id=entity_id,
        )

    @staticmethod
    def _dedupe_facts(items: Sequence[ValidatedDocumentFact]) -> List[ValidatedDocumentFact]:
        output: List[ValidatedDocumentFact] = []
        index_by_key: Dict[Tuple[str, str], int] = {}
        rank = {ValidationStatus.REJECTED: 0, ValidationStatus.AMBIGUOUS: 1, ValidationStatus.VALIDATED: 2}
        for item in items:
            key = (item.fact_type.value, item.normalized_value or item.raw_value)
            if key not in index_by_key:
                index_by_key[key] = len(output)
                output.append(item)
            elif rank[item.validation_status] > rank[output[index_by_key[key]].validation_status]:
                output[index_by_key[key]] = item
        return output

    @staticmethod
    def _prefer_explicit_labeled_dates(items: Sequence[ValidatedDocumentFact]) -> List[ValidatedDocumentFact]:
        explicit_by_type = {
            fact.fact_type: fact
            for fact in items
            if "deterministic_explicit_date_label" in fact.validation_notes
        }
        if not explicit_by_type:
            return list(items)
        output: List[ValidatedDocumentFact] = []
        for item in items:
            explicit = explicit_by_type.get(item.fact_type)
            if explicit and item.normalized_value != explicit.normalized_value:
                continue
            output.append(item)
        return output

    @staticmethod
    def _segment_candidate_fingerprint(candidate: SegmentCandidate) -> str:
        identity = {
            "kind": candidate.kind.value,
            "title": _normalized_text(candidate.title).casefold(),
            "point_label": str(candidate.point_label or "").strip().casefold(),
            "tg_grade": str(candidate.tg_grade or "").strip().upper(),
            "professional_subject": _normalized_text(candidate.professional_subject).casefold(),
            "page": candidate.evidence.page,
            "claimed_char_start": candidate.evidence.claimed_char_start,
            "claimed_char_end": candidate.evidence.claimed_char_end,
            "normalized_source_span_sha256": hashlib.sha256(
                _normalized_text(candidate.evidence.exact_quote).encode("utf-8")
            ).hexdigest(),
        }
        return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()

    @classmethod
    def _reconcile_segments(
        cls,
        records: Sequence[Tuple[int, SegmentCandidate, ValidatedSegment]],
    ) -> Tuple[List[ValidatedSegment], List[CandidateDisposition], List[Abstention]]:
        output: List[ValidatedSegment] = []
        dispositions: List[CandidateDisposition] = []
        abstentions: List[Abstention] = []
        first_by_fingerprint: Dict[str, ValidatedSegment] = {}

        for batch_index, candidate, segment in records:
            fingerprint = cls._segment_candidate_fingerprint(candidate)
            existing = first_by_fingerprint.get(fingerprint)
            if existing is not None:
                outcome = CandidateOutcome.DUPLICATE
                reason_codes = ["duplicate_candidate_same_source_identity"]
                final_entity_id = None
                duplicate_of = existing.segment_id
            else:
                first_by_fingerprint[fingerprint] = segment
                output.append(segment)
                final_entity_id = segment.segment_id
                duplicate_of = None
                if segment.validation_status == ValidationStatus.VALIDATED:
                    outcome = CandidateOutcome.ADMITTED
                    reason_codes = []
                else:
                    outcome = CandidateOutcome.ABSTAINED
                    reason_codes = list(segment.validation_notes) or ["segment_validation_failed"]
                    suppress_abstention = (
                        candidate.kind in {SegmentKind.SECTION, SegmentKind.REPORT_POINT}
                        and set(reason_codes) <= {
                            "exact_quote_not_found",
                            "whitespace_normalized_quote_not_found",
                        }
                    )
                    if not suppress_abstention:
                        abstentions.append(
                            cls._candidate_abstention(
                                "segment_validation",
                                segment.segment_id,
                                reason_codes,
                            )
                        )

            dispositions.append(
                CandidateDisposition(
                    disposition_id=_stable_id("disposition", batch_index, fingerprint, len(dispositions) + 1),
                    batch_index=batch_index,
                    candidate_fingerprint=fingerprint,
                    candidate_id=candidate.candidate_id,
                    entity_kind=candidate.kind.value,
                    title=candidate.title,
                    page=candidate.evidence.page,
                    point_label=candidate.point_label,
                    tg_grade=str(candidate.tg_grade or "").strip().upper() or None,
                    professional_subject=candidate.professional_subject,
                    outcome=outcome,
                    final_entity_id=final_entity_id,
                    duplicate_of_entity_id=duplicate_of,
                    reason_codes=reason_codes,
                )
            )
        return output, dispositions, abstentions

    @staticmethod
    def _coverage_bucket(key: str, dispositions: Sequence[CandidateDisposition]) -> CoverageBucket:
        unique = [item for item in dispositions if item.outcome != CandidateOutcome.DUPLICATE]
        return CoverageBucket(
            key=key,
            candidates=len(dispositions),
            unique_candidates=len(unique),
            admitted=sum(item.outcome == CandidateOutcome.ADMITTED for item in dispositions),
            abstained=sum(item.outcome == CandidateOutcome.ABSTAINED for item in dispositions),
            duplicates=sum(item.outcome == CandidateOutcome.DUPLICATE for item in dispositions),
        )

    @classmethod
    def _build_coverage(cls, dispositions: Sequence[CandidateDisposition]) -> SegmentCoverage:
        by_kind: List[CoverageBucket] = []
        required_kinds = [SegmentKind.SECTION.value, SegmentKind.REPORT_POINT.value, SegmentKind.SUMMARY.value]
        other_kinds = sorted({item.entity_kind for item in dispositions} - set(required_kinds))
        for key in required_kinds + other_kinds:
            by_kind.append(cls._coverage_bucket(key, [item for item in dispositions if item.entity_kind == key]))

        tg_keys = ["TG0", "TG1", "TG2", "TG3", "TGIU", "NO_TG"]
        by_tg = []
        for key in tg_keys:
            matching = [
                item for item in dispositions
                if (item.tg_grade or "NO_TG") == key
            ]
            by_tg.append(cls._coverage_bucket(key, matching))

        blockers = [
            f"unresolved_tg3_report_point:{item.candidate_fingerprint}"
            for item in dispositions
            if item.entity_kind == SegmentKind.REPORT_POINT.value
            and item.tg_grade == "TG3"
            and item.outcome == CandidateOutcome.ABSTAINED
        ]
        return SegmentCoverage(
            raw_candidate_count=len(dispositions),
            unique_candidate_count=sum(item.outcome != CandidateOutcome.DUPLICATE for item in dispositions),
            admitted_count=sum(item.outcome == CandidateOutcome.ADMITTED for item in dispositions),
            abstained_count=sum(item.outcome == CandidateOutcome.ABSTAINED for item in dispositions),
            duplicate_count=sum(item.outcome == CandidateOutcome.DUPLICATE for item in dispositions),
            dispositions_count=len(dispositions),
            by_kind=by_kind,
            by_tg=by_tg,
            completion_blockers=blockers,
        )

    @classmethod
    def _build_source_coverage(
        cls,
        segments: Sequence[ValidatedSegment],
        dispositions: Sequence[CandidateDisposition],
    ) -> SegmentCoverage:
        valid = [item for item in segments if item.validation_status == ValidationStatus.VALIDATED]
        invalid = [item for item in segments if item.validation_status != ValidationStatus.VALIDATED]

        def bucket(key: str, items: Sequence[ValidatedSegment]) -> CoverageBucket:
            return CoverageBucket(
                key=key,
                candidates=len(items),
                unique_candidates=len(items),
                admitted=sum(item.validation_status == ValidationStatus.VALIDATED for item in items),
                abstained=sum(item.validation_status != ValidationStatus.VALIDATED for item in items),
                duplicates=0,
            )

        kinds = [SegmentKind.SECTION.value, SegmentKind.REPORT_POINT.value, SegmentKind.SUMMARY.value]
        kinds.extend(sorted({item.kind.value for item in segments} - set(kinds)))
        by_kind = [bucket(key, [item for item in segments if item.kind.value == key]) for key in kinds]
        tg_keys = ["TG0", "TG1", "TG2", "TG3", "TGIU", "NO_TG"]
        by_tg = [
            bucket(key, [item for item in segments if (item.tg_grade or "NO_TG") == key])
            for key in tg_keys
        ]
        return SegmentCoverage(
            raw_candidate_count=len(dispositions),
            unique_candidate_count=len(segments),
            admitted_count=len(valid),
            abstained_count=len(invalid),
            duplicate_count=sum(item.outcome == CandidateOutcome.DUPLICATE for item in dispositions),
            dispositions_count=len(dispositions),
            by_kind=by_kind,
            by_tg=by_tg,
            completion_blockers=[],
        )

    @staticmethod
    def _trace(
        document_hash: str,
        facts: Sequence[ValidatedDocumentFact],
        segments: Sequence[ValidatedSegment],
        dispositions: Sequence[CandidateDisposition],
    ) -> List[TraceRecord]:
        records: List[TraceRecord] = []
        for entity_type, items in (("document_fact", facts), ("segment", segments)):
            for item in items:
                payload = item.model_dump(mode="json")
                entity_id = item.fact_id if isinstance(item, ValidatedDocumentFact) else item.segment_id
                records.append(
                    TraceRecord(
                        trace_id=_stable_id("trace", document_hash, "a2_validation", entity_type, entity_id),
                        document_hash=document_hash,
                        stage="a2_validation",
                        entity_type=entity_type,
                        entity_id=entity_id,
                        payload_sha256=hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest(),
                    )
                )
        for disposition in dispositions:
            payload = disposition.model_dump(mode="json")
            records.append(
                TraceRecord(
                    trace_id=_stable_id(
                        "trace",
                        document_hash,
                        "a2_candidate_disposition",
                        disposition.disposition_id,
                    ),
                    document_hash=document_hash,
                    stage="a2_candidate_disposition",
                    entity_type="candidate_disposition",
                    entity_id=disposition.disposition_id,
                    payload_sha256=hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest(),
                )
            )
        return records
