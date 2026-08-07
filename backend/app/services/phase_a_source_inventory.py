"""Deterministic physical report-point inventory derived before AI reconciliation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from app.services.phase_a_contracts import (
    InventoryRole,
    PhysicalReportPoint,
    SourceEvidence,
    SourceInventoryResult,
    ValidationStatus,
)


PAGE_RE = re.compile(r"(?m)^\[SIDE\s+(\d+)\]\s*$")


def _id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def _tg(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s+", "", value.upper()).replace("TGIU", "TGIU")


@dataclass(frozen=True)
class _Page:
    number: int
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class _Marker:
    role: InventoryRole
    page: int
    start: int
    point_label: str | None
    title: str
    tg_grade: str | None
    marker: str
    method: str
    point_type: str = "graded"


def _pages(report_text: str) -> list[_Page]:
    matches = list(PAGE_RE.finditer(report_text))
    output = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(report_text)
        output.append(_Page(int(match.group(1)), report_text[start:end], start, end))
    if not output:
        output.append(_Page(1, report_text, 0, len(report_text)))
    return output


def _primary_text(page: _Page) -> tuple[str, int]:
    table = page.text.find("[TABELLDATA]")
    return (page.text[:table] if table >= 0 else page.text), page.start


def _line_start(text: str, position: int) -> int:
    return text.rfind("\n", 0, position) + 1


def _previous_heading_start(text: str, position: int) -> tuple[int, str]:
    before = text[:position]
    hms = before.casefold().rfind("helse, miljø og sikkerhet")
    if hms >= 0 and position - hms < 1800:
        return hms, before[hms:before.find("\n", hms)].strip()
    lines = list(re.finditer(r"(?m)^([^\n]{2,180})$", before))
    description_index = None
    for index in range(len(lines) - 1, -1, -1):
        value = lines[index].group(1).strip()
        if value.casefold() == "beskrivelse":
            description_index = index
            break
    if description_index is not None and description_index > 0:
        for heading_index in range(description_index - 1, -1, -1):
            heading = lines[heading_index]
            value = heading.group(1).strip()
            if value.casefold().startswith(("punktet må sees", "se også", "jf.")):
                continue
            return heading.start(), value
    if lines:
        heading = lines[-1]
        return heading.start(), heading.group(1).strip()
    return _line_start(text, position), "Uidentifisert rapportpunkt"


def _section_context(text: str, position: int) -> str:
    """Return nearby structural section/room headings, excluding page headers."""
    window = text[:position]
    accepted: list[str] = []
    section_words = {
        "utvendig", "innvendig", "våtrom", "kjøkken", "tomteforhold",
        "tekniske installasjoner", "helse, miljø og sikkerhet", "hms",
        "lovlighet", "metodikk", "forutsetninger",
    }
    for raw in window.splitlines():
        value = raw.strip()
        folded = value.casefold()
        if not value or len(value) > 120:
            continue
        if (
            folded in section_words
            or ">" in value
            or re.match(r"^\d+(?:\.\d+)?\.\s+(?:bad|vaskerom|kjøkken|våtrom)\b", folded)
        ):
            if value not in accepted:
                accepted.append(value)
    return " > ".join(accepted[-3:])


def _title_hierarchy(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s+[–—-]\s+", value) if part.strip()]


def _main_section_context(text: str, position: int) -> str:
    """Return the latest numbered DEL/section hierarchy before a physical point."""
    prefix = text[:position]
    result = ""
    for match in re.finditer(r"(?im)^DEL\s+(\d{1,3})\s*$", prefix):
        number = match.group(1)
        following = prefix[match.end():min(len(prefix), match.end() + 900)]
        heading = re.search(rf"(?im)^{re.escape(number)}\.\s+([^\n]{{2,240}})$", following)
        result = f"{number}. {heading.group(1).strip()}" if heading else f"DEL {number}"
    return result


def _physical_section_context(text: str, marker: _Marker) -> str:
    hierarchy = _title_hierarchy(marker.title)
    if marker.role == InventoryRole.SUMMARY:
        return " > ".join(hierarchy[:-1])
    main = _main_section_context(text, marker.start)
    parent = " > ".join(hierarchy[:-1])
    components = [item for item in (main, parent) if item]
    if components:
        return " > ".join(dict.fromkeys(components))
    return _section_context(text, min(len(text), marker.start + len(marker.title) + 1))


_TGIU_RE = re.compile(
    r"(?i)\b(?:TG\s*IU|TGIU|ikke\s+(?:undersøkt|inspisert|befart|kontrollert)|"
    r"(?:ikke|var\s+ikke)\s+tilgjengelig\s+for\s+(?:undersøkelse|inspeksjon)|"
    r"(?:ikke|var\s+ikke)\s+mulig\s+å\s+(?:undersøke|inspisere|kontrollere)|"
    r"kunne\s+ikke\s+(?:undersøkes|inspiseres|kontrolleres)|utilgjengelig\s+for\s+(?:undersøkelse|inspeksjon)|"
    r"hulltaking\s+(?:er\s+)?ikke\s+utført|hulltaking\s+(?:var\s+)?ikke\s+mulig)\b"
)


def _context_type(text: str, start: int, end: int, explicit_tg: str | None, title: str = "") -> tuple[str | None, str]:
    """Classify from the physical section, never from one repeated phrase alone."""
    context = (title + " " + text[max(0, start - 120):min(len(text), start + 220)]).casefold()
    if explicit_tg == "TGIU":
        return "TGIU", "tgiu"
    if re.search(r"\b(elektrisk(?:e)?\s+anlegg|el-anlegg|el anlegg)\b", title.casefold()):
        return None, "electrical_no_tg"
    if re.search(r"\b(helse, miljø og sikkerhet|hms|radon|rekkverk)\b", context):
        return None, "hms_no_tg"
    if re.search(r"\b(lovlighet|ferdigattest|brukstillatelse|bruksendring)\b", context):
        return None, "legality_no_tg"
    if re.search(r"\b(metodikk|forutsetninger|avgrensning|oppdragets rammer)\b", context):
        return None, "methodology_only"
    return explicit_tg, "graded" if explicit_tg else "unknown"


class PhysicalSourceInventoryBuilder:
    def build(self, report_text: str, document_hash: str) -> SourceInventoryResult:
        pages = _pages(report_text)
        markers: list[_Marker] = []
        structural_counts = {
            "vurdering_av_avvik": len(re.findall(r"(?i)Vurdering av avvik\s*:", report_text)),
        }
        detector_parts: list[str] = []

        for page in pages:
            primary, base = _primary_text(page)
            # IVIT/standard Norwegian layout: point heading + Beskrivelse + Vurdering av avvik.
            for match in re.finditer(r"(?im)^Vurdering av avvik\s*:\s*$", primary):
                local_start, title = _previous_heading_start(primary, match.start())
                tg_grade, point_type = _context_type(primary, local_start, match.end(), "TG2", title)
                markers.append(_Marker(
                    InventoryRole.PRIMARY, page.number, base + local_start, None,
                    title, tg_grade, match.group(0).strip(), "physical_vurdering_av_avvik", point_type,
                ))

            # BMTF detailed point heading.
            for match in re.finditer(
                r"(?im)^(\d+(?:\.\d+)*)\.\s+([^\n]{2,180}?)\s+(TG[0-3]|TGIU)(?:\s*[–-][^\n]*)?$",
                primary,
            ):
                if "oppsummering" in match.group(2).casefold():
                    continue
                explicit_tg = _tg(match.group(3))
                markers.append(_Marker(
                    InventoryRole.PRIMARY, page.number, base + match.start(), match.group(1),
                    match.group(2).strip(), explicit_tg, match.group(0).strip(),
                    "physical_numbered_tg_heading", "tgiu" if explicit_tg == "TGIU" else "graded",
                ))

            # General fallback for a standalone title followed by a TG marker.
            for match in re.finditer(
                r"(?im)^((?:(\d+(?:\.\d+)*)\s+)?[^\n]{2,180}?)\s+(TG\s*(?:[0-3]|IU))(?:\s*[–-][^\n]*)?$",
                primary,
            ):
                title = match.group(1).strip()
                excluded = (
                    "oppsummering", "tilstandsgrad", "kostnad", "konsekvens og tiltak",
                    "tilstandsrapport", "total:", "tg 0 tg", "ved",
                )
                if any(term in title.casefold() for term in excluded):
                    continue
                if any(
                    existing.page == page.number and existing.start == base + match.start()
                    for existing in markers
                ):
                    continue
                explicit_tg = _tg(match.group(3))
                markers.append(_Marker(
                    InventoryRole.PRIMARY, page.number, base + match.start(), match.group(2),
                    title, explicit_tg, match.group(0).strip(),
                    "general_physical_tg_heading", "tgiu" if explicit_tg == "TGIU" else "graded",
                ))

            # Bolavi/befar.io detailed point heading.
            for match in re.finditer(
                r"(?im)^TG\s*([0-3])\s+(\d+(?:\.\d+)*)\.?\s+([^\n]{2,180})$",
                primary,
            ):
                markers.append(_Marker(
                    InventoryRole.PRIMARY, page.number, base + match.start(), match.group(2),
                    match.group(3).strip(), f"TG{match.group(1)}", match.group(0).strip(),
                    "physical_bolavi_tg_heading",
                ))

            # General structural TGIU detection: locate the containing heading for
            # unseen titles and wording, instead of naming fixtures.
            for match in _TGIU_RE.finditer(primary):
                local_start, title = _previous_heading_start(primary, match.start())
                local_block = primary[local_start:match.end()]
                following = primary[match.end():match.end() + 350].casefold()
                if match.start() - local_start > 500 or not re.search(r"(?im)^Beskrivelse\s*$", local_block):
                    continue
                if "hulltaking" in match.group(0).casefold() and re.search(r"\b(?:måling er utført|ingen fukt målt)\b", following):
                    continue
                if (
                    title.isupper()
                    or title.casefold() in {"tilstandsrapport", "beskrivelse"}
                    or len(title) > 120
                    or title.rstrip().endswith((".", ":", ";"))
                ):
                    continue
                if any(item.start == base + local_start for item in markers):
                    continue
                markers.append(_Marker(
                    InventoryRole.PRIMARY, page.number, base + local_start, None,
                    title, "TGIU", match.group(0).strip(), "physical_uninvestigated_semantic", "tgiu",
                ))

            low = primary.casefold()
            summary_start = min(
                (position for position in (
                    low.find("oppsummering av avvik"), low.find("oppsummering / konklusjon")
                ) if position >= 0),
                default=-1,
            )
            if summary_start >= 0:
                detector_parts.append("summary_sections")
                # Clickable "Gå til side" rows are navigation, not substantive
                # summaries. They remain traceable but never enter assessment.
                for match in re.finditer(r"(?im)^([^\n]{2,240}?)\s+Gå\s+til\s+side\s*$", primary):
                    if match.start() <= summary_start:
                        continue
                    path = match.group(1).strip()
                    if path.casefold() in {"arealer", "forutsetninger og vedlegg", "lovlighet"}:
                        continue
                    leaf = path.split(">")[-1].strip()
                    if not leaf:
                        continue
                    markers.append(_Marker(
                        InventoryRole.NAVIGATION, page.number, base + match.start(), None,
                        path, None, match.group(0).strip(), "physical_navigation_summary_row", "unknown",
                    ))

            # Table-of-contents/navigation entries are retained for traceability
            # but are never eligible for assessment.
            for match in re.finditer(r"(?im)^([^\n]{3,160}?)\s*(?:\.{2,}|\s{3,})\s*(\d{1,3})\s*$", primary):
                title = match.group(1).strip()
                if title.casefold() in {"side", "innhold", "tilstandsrapport"}:
                    continue
                markers.append(_Marker(
                    InventoryRole.NAVIGATION, page.number, base + match.start(), None,
                    title, None, match.group(0).strip(), "physical_navigation_entry", "unknown",
                ))

            # Non-assessable structural headings are still hard boundaries. They
            # prevent the preceding point from absorbing electrical, HMS,
            # legality, valuation, methodology or summary sections.
            boundary_patterns = (
                r"(?im)^DEL\s+\d+\s*$",
                r"(?im)^\d+\.\s+[A-ZÆØÅ0-9][A-ZÆØÅ0-9 /&()\-–—]{3,}\s*$",
                r"(?im)^(?:ELEKTRISK(?:E)?\s+ANLEGG|TOMTEFORHOLD|LOVLIGHET|"
                r"FORHOLD SOM ÅPENBART[^\n]*|HELSE,\s*MILJØ[^\n]*|HMS|"
                r"BRANN(?:SIKKERHET|TEKNISKE FORHOLD)?|RADON|SKADEDYR[^\n]*|"
                r"MARKEDSVERDI[^\n]*|BYGNINGER PÅ EIENDOMMEN|"
                r"OPPSUMMERING / KONKLUSJON)\s*$",
                r"(?im)^Total:\s*\d+\s+OPPSUMMERING[^\n]*$",
                r"(?im)^\d+\.\s+Oppsummering / konklusjon\s*$",
            )
            for pattern in boundary_patterns:
                for match in re.finditer(pattern, primary):
                    absolute_start = base + match.start()
                    if any(item.role == InventoryRole.BOUNDARY and item.start == absolute_start for item in markers):
                        continue
                    markers.append(_Marker(
                        InventoryRole.BOUNDARY, page.number, absolute_start, None,
                        match.group(0).strip(), None, match.group(0).strip(),
                        "physical_non_assessable_section_boundary", "unknown",
                    ))

        # Parse substantive summary children after all primary markers exist.
        # Each child receives its own boundary and primary linkage; no aggregate
        # summary body can be linked to a single point.
        bolavi_summary_active = False
        bmtf_summary_active = False
        current_summary_tg: str | None = None
        for page in pages:
            primary, base = _primary_text(page)
            if re.search(r"(?im)^Total:\s*\d+\s+OPPSUMMERING[^\n]*TG2\s*$", primary):
                bolavi_summary_active = True
                current_summary_tg = "TG2"
            if re.search(r"(?im)^Total:\s*\d+\s+OPPSUMMERING[^\n]*TG3\s*$", primary):
                bolavi_summary_active = True
                current_summary_tg = "TG3"
            if re.search(r"(?im)^\d+\.\s+Oppsummering / konklusjon\s*$", primary):
                bmtf_summary_active = True
            bmtf_grade = re.search(r"(?im)^TG\s*([23])\s*[–-]", primary)
            if bmtf_summary_active and bmtf_grade:
                current_summary_tg = f"TG{bmtf_grade.group(1)}"

            if bolavi_summary_active:
                for match in re.finditer(
                    r"(?m)^(\d{1,3}(?:\.(?:\d+|\(cid:\d+\)))?)\s+([^\n]{3,180})$",
                    primary,
                ):
                    label, title = match.group(1), match.group(2).strip()
                    if title.upper() == title or title.casefold().startswith(("av ", "oppsummering")):
                        continue
                    markers.append(_Marker(
                        InventoryRole.SUMMARY, page.number, base + match.start(), label,
                        title, current_summary_tg, match.group(0).strip(),
                        "physical_bolavi_summary_child", "unknown",
                    ))

            if bmtf_summary_active:
                for match in re.finditer(r"(?m)^(\d{1,3})\.\s+([^\n]{3,240})$", primary):
                    label, title = match.group(1), match.group(2).strip()
                    if "oppsummering / konklusjon" in title.casefold() or "–" not in title and "-" not in title:
                        continue
                    markers.append(_Marker(
                        InventoryRole.SUMMARY, page.number, base + match.start(), label,
                        title, current_summary_tg, match.group(0).strip(),
                        "physical_bmtf_summary_child", "unknown",
                    ))

            # Generic numbered summary fallback for previously unseen layouts.
            generic_summary = re.search(r"(?im)^Oppsummering av avvik\s*$", primary)
            if generic_summary:
                for match in re.finditer(r"(?m)^(\d+(?:\.\d+)*)\.?\s+([^\n]{3,180})$", primary):
                    if match.start() <= generic_summary.start():
                        continue
                    title = match.group(2).strip()
                    if title.isupper() or "gå til side" in title.casefold():
                        continue
                    markers.append(_Marker(
                        InventoryRole.SUMMARY, page.number, base + match.start(), match.group(1),
                        title, None, match.group(0).strip(),
                        "physical_generic_summary_child", "unknown",
                    ))

        # Detect a point title at the end of one page whose description and
        # uninvestigated wording continue on the next page.
        for page_index, page in enumerate(pages[:-1]):
            primary, base = _primary_text(page)
            next_primary, _ = _primary_text(pages[page_index + 1])
            meaningful = [item.strip() for item in primary.splitlines() if item.strip()]
            if not meaningful:
                continue
            title = meaningful[-1]
            next_description = re.search(r"(?im)^Beskrivelse\s*$", next_primary)
            next_tgiu = _TGIU_RE.search(next_primary[:900])
            if (
                not next_description or not next_tgiu or next_description.start() > next_tgiu.start()
                or len(title) > 120 or title.startswith(("•", "["))
                or title.rstrip().endswith((".", ":", ";"))
            ):
                continue
            title_pos = primary.rfind(title)
            if any(item.start == base + title_pos for item in markers):
                continue
            markers.append(_Marker(
                InventoryRole.PRIMARY, page.number, base + title_pos, None,
                title, "TGIU", title, "physical_cross_page_uninvestigated_section", "tgiu",
            ))

        # Remove exact extraction duplicates while preserving distinct physical offsets.
        unique: dict[tuple, _Marker] = {}
        for marker in markers:
            key = (marker.role.value, marker.page, marker.start, marker.point_label, marker.title.casefold(), marker.tg_grade)
            unique[key] = marker
        markers = sorted(unique.values(), key=lambda item: (item.start, item.role.value))

        points: list[PhysicalReportPoint] = []
        for index, marker in enumerate(markers):
            if marker.role == InventoryRole.BOUNDARY:
                continue
            # Bound globally, across page delimiters. A page boundary is not a
            # semantic boundary. Stop only at the next structural marker.
            # Navigation is trace material, not a report-point boundary. Primary
            # bodies can cross pages and stop only at another primary or at an
            # explicit summary section. Summary rows stop at the next summary or
            # primary row. This prevents TOC rows from truncating source bodies.
            allowed_boundaries = {
                InventoryRole.PRIMARY: {InventoryRole.PRIMARY, InventoryRole.SUMMARY, InventoryRole.BOUNDARY},
                InventoryRole.SUMMARY: {InventoryRole.SUMMARY, InventoryRole.PRIMARY, InventoryRole.BOUNDARY},
                InventoryRole.NAVIGATION: {
                    InventoryRole.NAVIGATION, InventoryRole.SUMMARY,
                    InventoryRole.PRIMARY, InventoryRole.BOUNDARY,
                },
            }[marker.role]
            later_markers = [
                item for item in markers
                if item.start > marker.start and item.role in allowed_boundaries
            ]
            later = [item.start for item in later_markers]
            if later:
                boundary_end = min(later)
            else:
                # The document end is a valid physical boundary. This allows the
                # final point to continue over any remaining pages.
                boundary_end = len(report_text)

            # A body crossing pages is represented as reversible original source
            # spans. Exclude [TABELLDATA], image metadata and page separators
            # rather than silently joining non-contiguous source text.
            body_spans: list[SourceEvidence] = []
            for page in pages:
                primary_text, primary_base = _primary_text(page)
                span_start = max(marker.start, primary_base)
                span_end = min(boundary_end, primary_base + len(primary_text))
                if span_start >= span_end:
                    continue
                raw = report_text[span_start:span_end]
                left_trim = len(raw) - len(raw.lstrip())
                exact = raw.strip()
                if not exact:
                    continue
                exact_start = span_start + left_trim
                exact_end = exact_start + len(exact)
                body_spans.append(SourceEvidence(
                    evidence_id=_id("source", document_hash, page.number, exact_start, exact_end),
                    exact_quote=exact,
                    page=page.number,
                    char_start=exact_start,
                    char_end=exact_end,
                    quote_sha256=hashlib.sha256(exact.encode()).hexdigest(),
                    match_method="exact",
                    validation_status=ValidationStatus.VALIDATED,
                    validation_notes=["physical_source_inventory_reversible_page_span"],
                ))
            if not body_spans:
                continue
            exact = "\n".join(span.exact_quote for span in body_spans)
            end = body_spans[-1].char_end
            boundary_uncertain = (
                marker.role == InventoryRole.PRIMARY
                and not later_markers
                and len(body_spans) > 3
            )
            inventory_id = _id(
                "physical", document_hash, marker.page, marker.start, end,
                marker.point_label or marker.marker,
            )
            evidence = body_spans[0]
            points.append(PhysicalReportPoint(
                inventory_id=inventory_id,
                role=marker.role,
                page=marker.page,
                char_start=evidence.char_start,
                char_end=end,
                point_label=marker.point_label,
                title=marker.title,
                section_context=_physical_section_context(report_text, marker),
                tg_grade=marker.tg_grade,
                point_type=marker.point_type,
                structural_marker=marker.marker,
                detection_method=marker.method,
                body=evidence,
                body_spans=body_spans,
                boundary_status="uncertain" if boundary_uncertain else "validated",
                boundary_reason=(
                    "unresolved_document_end_after_excessive_page_span"
                    if boundary_uncertain else
                    f"terminated_by:{min(later_markers, key=lambda item: item.start).method}"
                    if later_markers else "terminated_by:document_end"
                ),
                linked_primary_id=None,
            ))

        # Link summaries to a primary only when the complete hierarchy is safe.
        # Local-title similarity alone is intentionally insufficient.
        primaries = [item for item in points if item.role == InventoryRole.PRIMARY]
        linked: list[PhysicalReportPoint] = []
        assigned_primary_ids: set[str] = set()
        for point in points:
            if point.role != InventoryRole.SUMMARY:
                linked.append(point)
                continue
            def normalized(value: str) -> str:
                return re.sub(r"\W+", "", value.casefold())

            def tokens(value: str) -> set[str]:
                ignored = {
                    "ved", "til", "for", "med", "eller", "som", "punkt", "vesentlige",
                    "avvik", "svake", "store", "anlegg", "konstruksjoner",
                }
                aliases = {
                    "bad": "våtrom", "våtrommet": "våtrom", "kjelleren": "kjeller",
                    "underetasje": "kjeller", "sokkel": "kjeller",
                }
                return {
                    aliases.get(token, token)
                    for token in re.findall(r"\w+", value.casefold())
                    if len(token) > 2 and token not in ignored
                }

            def content_tokens(value: str) -> set[str]:
                ignored = {
                    "det", "den", "som", "ikke", "til", "ved", "for", "med", "eller",
                    "kan", "har", "etter", "dette", "disse", "avvik", "risiko", "konsekvens",
                    "tiltak", "vurdering", "anbefalt", "vesentlige", "tg2", "tg3",
                }
                return {
                    token for token in re.findall(r"\w+", value.casefold())
                    if len(token) > 3 and token not in ignored
                }

            summary_norm = normalized(point.title)
            summary_tokens = tokens(point.title)
            summary_context = tokens(point.section_context)
            summary_body = "\n".join(span.exact_quote for span in point.body_spans)
            summary_content = content_tokens(summary_body)

            def link_score(primary: PhysicalReportPoint) -> tuple[int, dict[str, int]] | None:
                primary_norm = normalized(primary.title)
                primary_tokens = tokens(primary.title)
                primary_context = tokens(primary.section_context)
                hierarchy_overlap = summary_context & (primary_context | primary_tokens)
                # A substantive parent hierarchy is a hard constraint. This is
                # what prevents Bad/Våtrom from linking to Rom under terreng.
                if summary_context and not hierarchy_overlap:
                    return None
                score = 0
                details: dict[str, int] = {}
                details["hierarchy"] = 70 * len(hierarchy_overlap)
                score += details["hierarchy"]
                if primary_norm and (primary_norm in summary_norm or summary_norm in primary_norm):
                    details["normalized_title"] = 80
                elif primary_norm and summary_norm:
                    details["normalized_title"] = int(40 * SequenceMatcher(None, primary_norm, summary_norm).ratio())
                else:
                    details["normalized_title"] = 0
                score += details["normalized_title"]
                details["title_tokens"] = 12 * len(summary_tokens & primary_tokens)
                score += details["title_tokens"]
                if point.tg_grade and primary.tg_grade:
                    details["tg_type"] = 20 if point.tg_grade == primary.tg_grade else -50
                    score += details["tg_type"]
                primary_body = "\n".join(span.exact_quote for span in primary.body_spans)
                content_overlap = summary_content & content_tokens(primary_body)
                details["content"] = min(60, 4 * len(content_overlap))
                score += details["content"]
                if (
                    point.detection_method == "physical_bolavi_summary_child"
                    and point.point_label and primary.point_label == point.point_label
                ):
                    details["point_label"] = 35
                    score += details["point_label"]
                elif (
                    point.detection_method == "physical_bolavi_summary_child"
                    and point.point_label and primary.point_label
                    and point.point_label.split(".", 1)[0] == primary.point_label.split(".", 1)[0]
                ):
                    details["point_label"] = 15
                    score += details["point_label"]
                # Physical position is a validity constraint: a summary may
                # refer only to an already materialized primary point.
                if primary.char_start >= point.char_start:
                    return None
                return score, details

            scored = []
            for primary in primaries:
                result = link_score(primary)
                if result is not None:
                    score, details = result
                    scored.append((score, primary, details))
            scored.sort(key=lambda item: item[0], reverse=True)
            unused = [item for item in scored if item[1].inventory_id not in assigned_primary_ids]
            pool = (
                unused
                if unused and scored and unused[0][0] >= scored[0][0]
                else scored
            )
            selected = None
            link_status = "unresolved"
            link_reason = "No hierarchy-compatible primary point met the deterministic threshold."
            candidate_ids: list[str] = []
            if pool and pool[0][0] >= 25:
                # Near-equal plausible candidates are ambiguous; never silently
                # choose one based on list order or local title alone.
                plausible = [item for item in pool if item[0] >= pool[0][0] - 5]
                candidate_ids = [item[1].inventory_id for item in plausible]
                if len(plausible) == 1:
                    selected = pool[0][1]
                    link_status = "linked"
                    link_reason = (
                        "Unique hierarchy-compatible primary selected using main section, subsection, "
                        "normalized title, physical position, TG/type and content compatibility."
                    )
                else:
                    link_status = "ambiguous"
                    link_reason = "Multiple hierarchy-compatible primary points remained plausible."
            if selected:
                assigned_primary_ids.add(selected.inventory_id)
            linked.append(point.model_copy(update={
                "linked_primary_id": selected.inventory_id if selected else None,
                "link_status": link_status,
                "link_reason": link_reason,
                "link_candidate_ids": candidate_ids,
            }))

        detector = "+".join(sorted(set(detector_parts + [item.detection_method for item in linked]))) or "no_structure_detected"
        structural_counts["physical_primary_vurdering"] = sum(
            item.role == InventoryRole.PRIMARY
            and item.detection_method == "physical_vurdering_av_avvik"
            for item in linked
        )
        structural_counts["physical_primary_points"] = sum(
            item.role == InventoryRole.PRIMARY for item in linked
        )
        return SourceInventoryResult(
            document_hash=document_hash,
            detector=detector,
            structural_marker_counts=structural_counts,
            points=linked,
        )
