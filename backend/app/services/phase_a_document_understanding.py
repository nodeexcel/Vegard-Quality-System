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
    CoverageBucket,
    DocumentFactCandidate,
    DocumentUnderstandingResult,
    FactType,
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


PAGE_MARKER_RE = re.compile(r"(?m)^\[SIDE\s+(\d+)\]\s*$")
DATE_TOKEN_RE = re.compile(
    r"(?<!\d)(?:(\d{4})[-./](\d{1,2})[-./](\d{1,2})|(\d{1,2})[-./](\d{1,2})[-./](\d{4}))(?!\d)"
)
NS_3600_RE = re.compile(r"(?i)\bNS\s*3600\s*[:\-]?\s*(2018|2025)\b")
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
        self.pages = list(pages)
        self.minimum_confidence = float(minimum_confidence)
        self.provider_identity_verifier = provider_identity_verifier

    def _candidate_pages(self, claimed_page: Optional[int]) -> List[PageSpan]:
        if claimed_page is None:
            return list(self.pages)
        return [page for page in self.pages if page.page == claimed_page]

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
        candidate_pages = self._candidate_pages(candidate.page)
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
            standards = {f"NS 3600:{match.group(1)}" for match in NS_3600_RE.finditer(candidate.evidence.exact_quote)}
            normalized_standard = next(iter(standards)) if len(standards) == 1 else None
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
                if self.provider_identity_verifier and self.provider_identity_verifier(candidate, self.report_text):
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
    ):
        if maximum_batch_chars < 1000:
            raise ValueError("maximum_batch_chars must be at least 1000")
        self.candidate_extractor = candidate_extractor
        self.fast_path_extractor = fast_path_extractor
        self.maximum_batch_chars = maximum_batch_chars
        self.minimum_confidence = minimum_confidence
        self.provider_identity_verifier = provider_identity_verifier

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
                        explanation=f"Candidate extraction failed closed: {type(exc).__name__}",
                    )
                )

        facts: List[ValidatedDocumentFact] = []
        segment_records: List[Tuple[int, SegmentCandidate, ValidatedSegment]] = []
        for actual_batch_index, candidate_batch in zip(candidate_batch_indices, candidates):
            for item in candidate_batch.facts:
                fact = validator.validate_fact(item, len(facts) + 1)
                facts.append(fact)
                if fact.validation_status != ValidationStatus.VALIDATED:
                    abstentions.append(self._candidate_abstention("fact_validation", fact.fact_id, fact.validation_notes))
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

        facts = self._dedupe_facts(facts)
        segments, dispositions, segment_abstentions = self._reconcile_segments(segment_records)
        segments = self._bind_complete_point_bodies(report_text, segments)
        abstentions.extend(segment_abstentions)
        coverage = self._build_coverage(dispositions)
        summary_blockers = self._declared_tg_summary_blockers(report_text, segments)
        body_blockers = [
            f"complete_point_body_missing:{segment.segment_id}"
            for segment in segments
            if segment.kind == SegmentKind.REPORT_POINT
            and segment.validation_status == ValidationStatus.VALIDATED
            and not segment.bound_body_spans
        ]
        if summary_blockers or body_blockers:
            coverage = coverage.model_copy(update={
                "completion_blockers": [*coverage.completion_blockers, *summary_blockers, *body_blockers]
            })
        trace_records = self._trace(document_hash, facts, segments, dispositions)
        valid_segments = [item for item in segments if item.validation_status == ValidationStatus.VALIDATED]
        if not candidates or not valid_segments:
            status = UnderstandingStatus.FAILED
        elif abstentions:
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
            model_metadata=model_metadata,
            trace_records=trace_records,
        )

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
        normalized_report = _normalized_text(report_text).casefold()
        if (
            not declared
            and "tilstandsgrader" in normalized_report
            and "sammendrag" in normalized_report
        ):
            blockers.append("declared_tg_summary_counts_not_extractable")
        return blockers

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
        seen = set()
        for item in items:
            key = (item.fact_type.value, item.normalized_value or item.raw_value)
            if key not in seen:
                output.append(item)
                seen.add(key)
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
