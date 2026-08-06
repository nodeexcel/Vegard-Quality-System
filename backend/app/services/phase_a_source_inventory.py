"""Deterministic physical report-point inventory derived before AI reconciliation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
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


_TGIU_RE = re.compile(
    r"(?i)\b(?:TG\s*IU|TGIU|ikke\s+(?:undersøkt|inspisert)|ikke\s+tilgjengelig\s+for\s+undersøkelse|ikke\s+mulig\s+å\s+undersøke|hulltaking\s+(?:er\s+)?ikke\s+utført|hulltaking\s+ikke\s+mulig)\b"
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
                markers.append(_Marker(
                    InventoryRole.PRIMARY, page.number, base + match.start(), match.group(1),
                    match.group(2).strip(), _tg(match.group(3)), match.group(0).strip(),
                    "physical_numbered_tg_heading",
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
                markers.append(_Marker(
                    InventoryRole.PRIMARY, page.number, base + match.start(), match.group(2),
                    title, _tg(match.group(3)), match.group(0).strip(),
                    "general_physical_tg_heading",
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
            if "oppsummering av avvik" in low or "oppsummering / konklusjon" in low:
                detector_parts.append("summary_sections")
                for match in re.finditer(r"(?im)^(\d+(?:\.\d+)*)\.?\s+([^\n]{2,180})$", primary):
                    if match.start() > low.find("oppsummering"):
                        markers.append(_Marker(
                            InventoryRole.SUMMARY, page.number, base + match.start(), match.group(1),
                            match.group(2).strip(), None, match.group(0).strip(), "physical_summary_entry",
                        ))
            for match in re.finditer(
                r"(?im)^Total:\s*\d+\s+OPPSUMMERING[^\n]*TG\s*([23])\s*$\s*^(\d+(?:\.\d+)*)\s+([^\n]{2,180})$",
                primary,
            ):
                markers.append(_Marker(
                    InventoryRole.SUMMARY, page.number, base + match.start(2), match.group(2),
                    match.group(3).strip(), f"TG{match.group(1)}", match.group(0).strip(),
                    "physical_bolavi_summary_entry",
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
            # Bound globally, across page delimiters. A page boundary is not a
            # semantic boundary. Stop only at the next structural marker.
            later = [item.start for item in markers if item.start > marker.start]
            end = min(later) if later else len(report_text)
            exact = report_text[marker.start:end].rstrip()
            if not exact:
                continue
            inventory_id = _id(
                "physical", document_hash, marker.page, marker.start, end,
                marker.point_label or marker.marker,
            )
            evidence = SourceEvidence(
                evidence_id=_id("source", document_hash, marker.page, marker.start, end),
                exact_quote=exact,
                page=marker.page,
                char_start=marker.start,
                char_end=marker.start + len(exact),
                quote_sha256=hashlib.sha256(exact.encode()).hexdigest(),
                match_method="exact",
                validation_status=ValidationStatus.VALIDATED,
                validation_notes=["physical_source_inventory_boundary"],
            )
            points.append(PhysicalReportPoint(
                inventory_id=inventory_id,
                role=marker.role,
                page=marker.page,
                char_start=evidence.char_start,
                char_end=evidence.char_end,
                point_label=marker.point_label,
                title=marker.title,
                tg_grade=marker.tg_grade,
                point_type=marker.point_type,
                structural_marker=marker.marker,
                detection_method=marker.method,
                body=evidence,
                linked_primary_id=None,
            ))

        # Link summaries to the best primary identity; summaries never become primary.
        primaries = [item for item in points if item.role == InventoryRole.PRIMARY]
        linked: list[PhysicalReportPoint] = []
        for point in points:
            if point.role != InventoryRole.SUMMARY:
                linked.append(point)
                continue
            leaf_title = re.split(r"[–—]", point.title)[-1].strip()
            normalized_title = re.sub(r"\W+", "", leaf_title.casefold())
            title_candidates = [
                primary for primary in primaries
                if normalized_title == re.sub(r"\W+", "", primary.title.casefold())
                or normalized_title in re.sub(r"\W+", "", primary.title.casefold())
                or re.sub(r"\W+", "", primary.title.casefold()) in normalized_title
            ]
            candidates = title_candidates or [
                primary for primary in primaries
                if point.point_label and primary.point_label == point.point_label
            ]
            linked.append(point.model_copy(update={
                "linked_primary_id": candidates[0].inventory_id if len(candidates) == 1 else None
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
