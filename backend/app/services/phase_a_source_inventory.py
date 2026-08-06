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
        heading = lines[description_index - 1]
        return heading.start(), heading.group(1).strip()
    if lines:
        heading = lines[-1]
        return heading.start(), heading.group(1).strip()
    return _line_start(text, position), "Uidentifisert rapportpunkt"


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
                markers.append(_Marker(
                    InventoryRole.PRIMARY, page.number, base + local_start, None,
                    title, "TG2", match.group(0).strip(), "physical_vurdering_av_avvik",
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

            # Explicit uninvestigated physical sections, including empty sections.
            for title in ("Septiktank", "Oljetank", "Tilliggende konstruksjoner våtrom"):
                for match in re.finditer(rf"(?im)^{re.escape(title)}\s*$", primary):
                    tail = primary[match.start():match.start() + 650].casefold()
                    summary_context = primary[max(0, match.start() - 300):match.start()].casefold()
                    explicit_uninvestigated = "ikke inspisert" in tail
                    empty_tilliggende = (
                        title == "Tilliggende konstruksjoner våtrom"
                        and ("[tabelldata]" in tail[:250] or len(tail.strip().splitlines()) <= 1)
                    )
                    if explicit_uninvestigated or empty_tilliggende or "konstruksjoner som ikke er undersøkt" in summary_context:
                        markers.append(_Marker(
                            InventoryRole.PRIMARY, page.number, base + match.start(), None,
                            title, "TGIU", match.group(0).strip(), "physical_uninvestigated_section",
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

        # Remove exact extraction duplicates while preserving distinct physical offsets.
        unique: dict[tuple, _Marker] = {}
        for marker in markers:
            key = (marker.role.value, marker.page, marker.start, marker.point_label, marker.title.casefold(), marker.tg_grade)
            unique[key] = marker
        markers = sorted(unique.values(), key=lambda item: (item.start, item.role.value))

        points: list[PhysicalReportPoint] = []
        for index, marker in enumerate(markers):
            page = next(item for item in pages if item.number == marker.page)
            later_on_page = [item.start for item in markers if item.page == marker.page and item.start > marker.start]
            primary_end = _primary_text(page)[1] + len(_primary_text(page)[0])
            end = min(later_on_page) if later_on_page else primary_end
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
