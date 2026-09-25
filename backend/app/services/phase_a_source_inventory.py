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
_PAGE_FOOTER_RE = re.compile(r"(?i)^\d{1,2}[./-]\d{1,2}[./-]\d{4}\s+Side:\s+\d+\s+av\s+\d+$")
_ADDRESS_RE = re.compile(r"^\d{4}\s+[A-ZÆØÅ][A-ZÆØÅ .-]{2,}$")
_STREET_ADDRESS_RE = re.compile(
    r"(?i)^[A-ZÆØÅ][A-Za-zÆØÅæøå0-9 .'-]{2,}"
    r"(?:vei|veit|vegen|veg|gate|gata|all[eé]n|stien|plassen|lia|bakken)\s+\d+[A-Za-z]?$"
)
_PROPERTY_ADDRESS_RE = re.compile(
    r"(?i)^[A-ZÆØÅ][A-Za-zÆØÅæøå0-9 .'-]{2,},\s*\d{4}\s+[A-ZÆØÅ][A-ZÆØÅ .-]{2,}$"
)
_DOUBLE_ZIP_CITY_RE = re.compile(
    r"(?i)^.*\d{4}\s+[A-ZÆØÅ][A-ZÆØÅ .-]{2,}\s+\d{4}\s+[A-ZÆØÅ][A-ZÆØÅ .-]{2,}.*$"
)
_MULTI_ADDRESS_LINE_RE = re.compile(
    r"(?i)^(?:.*\b(?:takst|tilstandsrapport)\b.*\d{4}\s+[A-ZÆØÅ][A-ZÆØÅ .-]{2,}"
    r"|(?:\d{4}\s+[A-ZÆØÅ][A-ZÆØÅ .-]{2,}\s+){2,}.*)$"
)
_GENERIC_HEADING_LINES = {
    "tilstandsrapport",
    "beskrivelse",
    "kommentar",
    "anvendelse",
    "byggeår kommentar",
    "standard",
    "vedlikehold",
    "konsekvens/tiltak",
    "bruksareal bra m²",
    "bruksareal bra m2",
    "m2 takst as",
    "bygninger på eiendommen",
}
_SUMMARY_TG_CATEGORY_BY_LINE = {
    "store eller alvorlige avvik": "TG3",
    "avvik som kan kreve tiltak": "TG2",
    "vesentlige avvik": "TG2",
    "konstruksjoner som ikke er undersøkt": "TGIU",
}
_SUMMARY_CATEGORY_RESET_PREFIXES = (
    "fordeling av tilstandsgrader",
    "oppsummering av avvik",
    "vil du vite mer",
    "anslag på utbedringskostnad",
    "hva er anslag på utbedringskostnad",
    "tg0:",
    "tg1:",
    "tg2:",
    "tg3:",
    "tg iu:",
    "tiltak under kr",
    "tiltak mellom kr",
    "tiltak over kr",
)


def _id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def _tg(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s+", "", value.upper()).replace("TGIU", "TGIU")


def _normalized_identity(value: str | None) -> str:
    return re.sub(r"\W+", "", str(value or "").casefold())


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


def _is_header_artifact_line(value: str) -> bool:
    compact = " ".join(value.split())
    if not compact:
        return False
    low = compact.casefold()
    if low in {"tilstandsrapport", "m2 takst as"}:
        return True
    if re.fullmatch(r"[A-ZÆØÅ][A-Za-zÆØÅæøå0-9 .&'-]{2,40}\s+AS", compact):
        return True
    if low.startswith(("årstall:", "kilde:")):
        return True
    if _PAGE_FOOTER_RE.match(compact):
        return True
    if _ADDRESS_RE.match(compact):
        return True
    if _STREET_ADDRESS_RE.match(compact):
        return True
    if _PROPERTY_ADDRESS_RE.match(compact):
        return True
    if _DOUBLE_ZIP_CITY_RE.match(compact):
        return True
    if _MULTI_ADDRESS_LINE_RE.match(compact):
        return True
    if re.fullmatch(r"\d{4}\s+[A-ZÆØÅ][A-ZÆØÅ .-]{2,}", compact):
        return True
    return False


def _trim_body_artifacts(exact: str, *, is_first_span: bool) -> str:
    lines = exact.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if lines:
        preserved = [lines[0]]
        for line in lines[1:]:
            if _is_header_artifact_line(line.strip()):
                continue
            preserved.append(line)
        lines = preserved

    if not is_first_span:
        while lines and _is_header_artifact_line(lines[0].strip()):
            lines.pop(0)
        while lines and re.fullmatch(r"[A-ZÆØÅ0-9 >_/().,-]{4,}", lines[0].strip()):
            lines.pop(0)
            if lines and lines[0].strip().casefold() == "tilstandsrapport":
                lines.pop(0)
            else:
                break

    while lines and _is_header_artifact_line(lines[-1].strip()):
        lines.pop()
    for index, line in enumerate(lines[1:], start=1):
        compact = line.strip()
        if re.fullmatch(r"(?:[A-ZÆØÅ0-9. ]+(?:\s*>\s*[A-ZÆØÅ0-9. ]+)+)", compact):
            lines = lines[:index]
            break

    return "\n".join(lines).strip()


def _body_span_chunks(raw: str, *, is_first_span: bool) -> list[tuple[int, int, str]]:
    def _trim_trailing_heading_fragment(start: int, end: int) -> tuple[int, int]:
        snippet = raw[start:end].rstrip()
        if not snippet:
            return start, end
        while True:
            lines = snippet.splitlines()
            if len(lines) <= 1:
                return start, end
            tail = lines[-1].strip()
            penultimate = lines[-2].strip()
            if not (
                _is_probable_heading(tail)
                and penultimate.casefold().startswith("kostnadsestimat:")
            ):
                return start, end
            last_newline = snippet.rfind("\n")
            if last_newline < 0:
                return start, end
            snippet = snippet[:last_newline].rstrip()
            end = start + len(snippet)

    records = []
    offset = 0
    for line in raw.splitlines(keepends=True):
        end = offset + len(line)
        text = line[:-1] if line.endswith("\n") else line
        records.append({
            "start": offset,
            "end": end,
            "text": text,
            "stripped": text.strip(),
        })
        offset = end
    if not records and raw:
        records.append({"start": 0, "end": len(raw), "text": raw, "stripped": raw.strip()})
    if not records:
        return []

    lo = 0
    hi = len(records) - 1
    while lo <= hi and not records[lo]["stripped"]:
        lo += 1
    while hi >= lo and not records[hi]["stripped"]:
        hi -= 1
    if lo > hi:
        return []

    if not is_first_span:
        while lo <= hi and _is_header_artifact_line(str(records[lo]["stripped"])):
            lo += 1
        while lo <= hi and re.fullmatch(r"[A-ZÆØÅ0-9 >_/().,-]{4,}", str(records[lo]["stripped"])):
            lo += 1
            if lo <= hi and str(records[lo]["stripped"]).casefold() == "tilstandsrapport":
                lo += 1
            else:
                break

    while hi >= lo and _is_header_artifact_line(str(records[hi]["stripped"])):
        hi -= 1
    if lo > hi:
        return []

    for index in range(lo + 1, hi + 1):
        compact = str(records[index]["stripped"])
        if re.fullmatch(r"(?:[A-ZÆØÅ0-9. ]+(?:\s*>\s*[A-ZÆØÅ0-9. ]+)+)", compact):
            hi = index - 1
            break
    if lo > hi:
        return []

    dropped = {
        index
        for index in range(lo, hi + 1)
        if records[index]["stripped"] and _is_header_artifact_line(str(records[index]["stripped"]))
    }
    chunks: list[tuple[int, int, str]] = []
    chunk_start: int | None = None
    chunk_end = 0
    for index in range(lo, hi + 1):
        if index in dropped:
            if chunk_start is not None:
                excerpt = raw[chunk_start:chunk_end].strip()
                if excerpt:
                    leading = len(raw[chunk_start:chunk_end]) - len(raw[chunk_start:chunk_end].lstrip())
                    trailing = len(raw[chunk_start:chunk_end].rstrip())
                    start = chunk_start + leading
                    end = chunk_start + trailing
                    start, end = _trim_trailing_heading_fragment(start, end)
                    chunks.append((start, end, raw[start:end]))
                chunk_start = None
            continue
        if chunk_start is None:
            chunk_start = int(records[index]["start"])
        chunk_end = int(records[index]["end"])
    if chunk_start is not None:
        excerpt = raw[chunk_start:chunk_end].strip()
        if excerpt:
            leading = len(raw[chunk_start:chunk_end]) - len(raw[chunk_start:chunk_end].lstrip())
            trailing = len(raw[chunk_start:chunk_end].rstrip())
            start = chunk_start + leading
            end = chunk_start + trailing
            start, end = _trim_trailing_heading_fragment(start, end)
            chunks.append((start, end, raw[start:end]))
    return chunks


def _is_probable_heading(value: str) -> bool:
    compact = " ".join(value.split())
    if not compact or len(compact) > 160:
        return False
    low = compact.casefold()
    if low == "uidentifisert rapportpunkt" or _is_header_artifact_line(compact):
        return False
    if low in _GENERIC_HEADING_LINES:
        return False
    if compact.startswith(("•", "[", "(")):
        return False
    if compact[0].islower():
        return False
    if _PAGE_FOOTER_RE.match(compact):
        return False
    if _ADDRESS_RE.match(compact):
        return False
    if _STREET_ADDRESS_RE.match(compact):
        return False
    if _PROPERTY_ADDRESS_RE.match(compact):
        return False
    if _DOUBLE_ZIP_CITY_RE.match(compact):
        return False
    if low.startswith("kostnadsestimat:") or "gå til side" in low:
        return False
    if compact.rstrip().endswith((".", ":", ";")):
        return False
    if low.startswith(("punktet må sees", "se også", "jf.")):
        return False
    if low.startswith(("det ", "ved ", "på ", "i ", "er ", "var ", "har ", "kan ")):
        return False
    word_count = len(re.findall(r"\w+", compact))
    if word_count >= 5 and re.search(r"\b(?:er|har|kan|skal|må|blir|vurdert|inneholder)\b", low):
        return False
    if compact.endswith(")") and compact.count(")") > compact.count("(") and word_count <= 4:
        return False
    if word_count >= 10 and any(marker in compact for marker in (",", ".", ";", ":")):
        return False
    if low in {"oppdragsnr.", "bygningssakkyndig", "enebolig"}:
        return False
    return True


def _looks_like_wrapped_heading_fragment(value: str) -> bool:
    compact = " ".join(value.split())
    if not compact:
        return False
    tokens = re.findall(r"\w+", compact)
    if compact.endswith(")") and compact.count(")") > compact.count("("):
        return True
    return bool(tokens) and compact[0].islower() and len(tokens) <= 4


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
            if _looks_like_wrapped_heading_fragment(value):
                combined = value
                combined_start = heading.start()
                for previous_index in range(heading_index - 1, -1, -1):
                    previous = lines[previous_index].group(1).strip()
                    if not previous or _is_header_artifact_line(previous):
                        break
                    candidate = f"{previous} {combined}".strip()
                    if _is_probable_heading(candidate):
                        combined = candidate
                        combined_start = lines[previous_index].start()
                        return combined_start, combined
                    if _is_probable_heading(previous):
                        break
            if not _is_probable_heading(value):
                continue
            return heading.start(), value
    return _line_start(text, position), "Uidentifisert rapportpunkt"


def _last_trailing_heading(text: str) -> tuple[int, str] | None:
    lines = list(re.finditer(r"(?m)^([^\n]{2,180})$", text))
    for match in reversed(lines):
        value = match.group(1).strip()
        if not value or _is_header_artifact_line(value):
            continue
        if value.casefold() == "beskrivelse":
            continue
        if _is_probable_heading(value):
            return match.start(), value
    return None


def _next_heading_boundary_offset(text: str) -> int | None:
    offset = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        line_offset = text.find(raw_line, offset)
        offset = line_offset + len(raw_line) + 1
        if not line or _is_header_artifact_line(line):
            continue
        if line.casefold() == "beskrivelse":
            continue
        if ">" in line:
            parts = [part.strip() for part in line.split(">") if part.strip()]
            if (
                len(parts) >= 2
                and all(len(part) <= 80 for part in parts)
                and all(_is_probable_heading(part) for part in parts)
            ):
                return line_offset
        if _is_probable_heading(line):
            return line_offset
    return None


def _section_context(text: str, position: int) -> str:
    """Return the nearest active hierarchy without accumulating prior rooms."""
    window_start = max(0, position - 5000)
    candidates: list[tuple[int, str]] = []
    main_sections = {
        "utvendig", "innvendig", "våtrom", "kjøkken", "tomteforhold",
        "tekniske installasjoner", "helse, miljø og sikkerhet", "hms",
        "lovlighet", "metodikk", "forutsetninger",
    }
    for match in re.finditer(r"(?m)^([^\n]{2,160})$", text[window_start:position]):
        value = match.group(1).strip()
        if not value or "gå til side" in value.casefold():
            continue
        absolute = window_start + match.start()
        if ">" in value:
            parts = [part.strip() for part in value.split(">") if part.strip()]
            if (
                len(parts) >= 2
                and all(len(part) <= 80 for part in parts)
                and all(_is_probable_heading(part) for part in parts)
            ):
                candidates.append((absolute, " > ".join(parts)))
        elif value.casefold() in main_sections:
            candidates.append((absolute, value))
        elif re.match(r"^\d+(?:\.\d+)?\.\s+(?:bad|vaskerom|kjøkken|våtrom)\b", value.casefold()):
            candidates.append((absolute, value))
    return max(candidates, default=(0, ""), key=lambda item: item[0])[1]


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
    if marker.role == InventoryRole.SUMMARY:
        parts = [part.strip() for part in marker.title.split(">") if part.strip()]
        if len(parts) >= 2:
            return " > ".join(parts[:-1])
        hierarchy = _title_hierarchy(marker.title)
        if len(hierarchy) >= 2:
            return " > ".join(hierarchy[:-1])
    hierarchy = _title_hierarchy(marker.title)
    if marker.method == "physical_detached_building_methodology_section" and len(hierarchy) <= 1:
        return ""
    main = _main_section_context(text, marker.start)
    parent = " > ".join(hierarchy[:-1])
    components = [item for item in (main, parent) if item]
    if components:
        return " > ".join(dict.fromkeys(components))
    return _section_context(text, min(len(text), marker.start + len(marker.title) + 1))


_TGIU_RE = re.compile(
    r"(?i)\b(?:TG\s*IU|TGIU|ikke\s+(?:undersøkt|inspisert|befart)|"
    r"(?:ikke|var\s+ikke)\s+tilgjengelig\s+for\s+(?:undersøkelse|inspeksjon)|"
    r"(?:ikke|var\s+ikke)\s+mulig\s+å\s+(?:undersøke|inspisere)|"
    r"kunne\s+ikke\s+(?:undersøkes|inspiseres|kontrolleres)|utilgjengelig\s+for\s+(?:undersøkelse|inspeksjon)|"
    r"hulltaking\s+(?:er\s+)?ikke\s+(?:utført|foretatt)|hulltaking\s+(?:var\s+)?ikke\s+mulig)\b"
)


def _context_type(text: str, start: int, end: int, explicit_tg: str | None, title: str = "") -> tuple[str | None, str]:
    """Classify from the physical section, never from one repeated phrase alone."""
    # For physical point headings we classify from the point's own heading/body,
    # not trailing wording from the previous point.
    context_start = start if title.strip() else max(0, start - 120)
    context = (title + " " + text[context_start:min(len(text), start + 220)]).casefold()
    title_low = title.casefold()
    if explicit_tg == "TGIU":
        return "TGIU", "tgiu"
    if re.search(r"\b(elektrisk(?:e)?\s+anlegg|el-anlegg|el anlegg)\b", title_low):
        return None, "electrical_no_tg"
    if re.search(r"\b(helse, miljø og sikkerhet|hms|radon|branntekniske forhold)\b", title_low):
        return None, "hms_no_tg"
    if "innvendige trapper" in title_low and re.search(r"\b(håndløper|sikkerhet|tilgjengelighet)\b", context):
        return None, "hms_no_tg"
    if re.search(r"\b(lovlighet|ferdigattest|brukstillatelse|bruksendring)\b", title_low):
        return None, "legality_no_tg"
    if re.search(r"\b(metodikk|forutsetninger|avgrensning|oppdragets rammer)\b", title_low):
        return None, "methodology_only"
    if explicit_tg:
        return explicit_tg, "graded"
    if re.search(r"\b(helse, miljø og sikkerhet|hms|radon|rekkverk)\b", context):
        return None, "hms_no_tg"
    if re.search(r"\b(lovlighet|ferdigattest|brukstillatelse|bruksendring)\b", context):
        return None, "legality_no_tg"
    if re.search(r"\b(metodikk|forutsetninger|avgrensning|oppdragets rammer)\b", context):
        return None, "methodology_only"
    return explicit_tg, "graded" if explicit_tg else "unknown"


def _summary_tg_category(line: str) -> str | None:
    compact = " ".join(str(line or "").split())
    low = compact.casefold()
    return _SUMMARY_TG_CATEGORY_BY_LINE.get(low)


def _is_summary_category_reset(line: str) -> bool:
    compact = " ".join(str(line or "").split())
    low = compact.casefold()
    if not low:
        return True
    if low == "enebolig":
        return True
    if low.startswith(_SUMMARY_CATEGORY_RESET_PREFIXES):
        return True
    if re.fullmatch(r"\d+", compact):
        return True
    return False


def _summary_section_start(primary_text: str) -> int:
    low = primary_text.casefold()
    return min(
        (
            position
            for position in (
                low.find("oppsummering av avvik"),
                low.find("oppsummering / konklusjon"),
                (
                    low.find("sammendrag av boligens tilstand")
                    if "fordeling av tilstandsgrader" in low else -1
                ),
            )
            if position >= 0
        ),
        default=-1,
    )


def _summary_category_before_position(primary_text: str, position: int) -> str | None:
    summary_start = _summary_section_start(primary_text)
    if summary_start < 0 or position <= summary_start:
        return None
    current: str | None = None
    for match in re.finditer(r"(?m)^([^\n]+)$", primary_text):
        if match.start() <= summary_start:
            continue
        if match.start() >= position:
            break
        compact = " ".join(match.group(1).split())
        category = _summary_tg_category(compact)
        if category:
            current = category
            continue
        if _is_summary_category_reset(compact):
            current = None
    return current


def _summary_navigation_path(primary_text: str, position: int) -> str:
    snippet = primary_text[position:]
    lines = [" ".join(line.split()) for line in snippet.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    current = lines[0]
    combined = current
    for compact in lines[1:4]:
        if "gå til side" in compact.casefold():
            break
        if _summary_tg_category(compact) or _is_summary_category_reset(compact):
            break
        if _is_header_artifact_line(compact):
            break
        if re.fullmatch(r"[A-ZÆØÅ0-9 /().,-]{4,}", compact):
            break
        combined = f"{combined} {compact}".strip()
        if ">" not in compact:
            break
    path = re.sub(r"(?i)\bGå\s+til\s+side\b", "", combined)
    path = re.sub(r"^(?:\d+\s+|llatnA\s+)+", "", path).strip(" -")
    path = re.sub(r"\s+llatnA$", "", path).strip()
    return re.sub(r"\s+", " ", path)


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
            low = primary.casefold()
            summary_navigation_starts: set[int] = set()
            # IVIT/standard Norwegian layout: point heading + Beskrivelse + Vurdering av avvik.
            for match in re.finditer(r"(?im)^Vurdering av avvik\s*:\s*$", primary):
                local_start, title = _previous_heading_start(primary, match.start())
                if title == "Uidentifisert rapportpunkt":
                    continue
                tg_grade, point_type = _context_type(primary, local_start, match.end(), None, title)
                if tg_grade is None and point_type == "unknown":
                    tg_grade, point_type = "TG2", "graded"
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
                summary_start = _summary_section_start(primary)
                if summary_start >= 0 and match.start() >= summary_start:
                    continue
                if (
                    "beskrivelse" not in primary.casefold()
                    and re.search(r"(?i)\bTGIU\s*[–-]\s*Ikke undersøkt\b", primary)
                    and re.search(r"(?i)\bHMS\s*/\s*sikkerhetsforhold\b", primary)
                ):
                    continue
                explicit_tg = _tg(match.group(3))
                markers.append(_Marker(
                    InventoryRole.PRIMARY, page.number, base + match.start(), match.group(1),
                    match.group(2).strip(), explicit_tg, match.group(0).strip(),
                    "physical_numbered_tg_heading", "tgiu" if explicit_tg == "TGIU" else "graded",
                ))

            # General fallback for a standalone title followed by a TG marker.
            for match in re.finditer(
                r"(?im)^((?:(\d+(?:\.\d+)*)[ \t]+)?[^\n]{2,180}?)[ \t]+(TG\s*(?:[0-3]|IU))(?:\s*[–-][^\n]*)?$",
                primary,
            ):
                title = match.group(1).strip()
                if not _is_probable_heading(title):
                    continue
                excluded = (
                    "oppsummering", "tilstandsgrad", "kostnad", "konsekvens og tiltak",
                    "tilstandsrapport", "total:", "tg 0 tg", "ved",
                )
                if any(term in title.casefold() for term in excluded):
                    continue
                summary_start = _summary_section_start(primary)
                if summary_start >= 0 and match.start() >= summary_start:
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
                if match.start() - local_start > 1400 or not re.search(r"(?im)^Beskrivelse\s*$", local_block):
                    continue
                context_window = primary[local_start:min(len(primary), match.end() + 2000)]
                if "hulltaking" in match.group(0).casefold() and re.search(r"\b(?:måling er utført|ingen fukt målt)\b", following):
                    continue
                if re.search(r"(?i)ikke\s+tilstandsvurdert.{0,200}(?:NS\s*3600|avhendingslova)", context_window):
                    continue
                if (
                    title == "Uidentifisert rapportpunkt"
                    or title.isupper()
                    or not _is_probable_heading(title)
                    or len(title) > 120
                ):
                    continue
                if any(item.start == base + local_start for item in markers):
                    continue
                markers.append(_Marker(
                    InventoryRole.PRIMARY, page.number, base + local_start, None,
                    title, "TGIU", match.group(0).strip(), "physical_uninvestigated_semantic", "tgiu",
                ))

            # Structurally assessable no-TG modules. These detectors use the
            # surrounding document grammar, not provider names or report text
            # outcomes, and therefore remain extraction-only optimisations.
            for match in re.finditer(r"(?im)^([^\n]{2,120})\s*\nAnvendelse\s*$", primary):
                title = match.group(1).strip()
                following = primary[match.end():match.end() + 6000]
                if not re.search(r"(?im)^Byggeår(?:\s+Kommentar)?\s*$", following):
                    continue
                if not re.search(r"(?im)^Beskrivelse\s*$", following):
                    continue
                if not re.search(r"(?i)ikke\s+tilstandsvurdert.{0,120}(?:NS\s*3600|avhendingslova)", following):
                    continue
                markers.append(_Marker(
                    InventoryRole.PRIMARY, page.number, base + match.start(), None,
                    title, None, match.group(0).strip(),
                    "physical_detached_building_methodology_section", "methodology_only",
                ))

            for heading, point_type, required_pattern in (
                ("Elektrisk anlegg", "electrical_no_tg", r"(?i)eltilsynsrapport|elektriske\s+anlegget"),
                ("Lovlighet", "legality_no_tg", r"(?i)byggetegninger|bruksendring|ferdigattest"),
            ):
                for match in re.finditer(rf"(?im)^{re.escape(heading)}\s*$", primary):
                    following = primary[match.end():match.end() + 5000]
                    if not re.search(required_pattern, following):
                        continue
                    markers.append(_Marker(
                        InventoryRole.PRIMARY, page.number, base + match.start(), None,
                        heading, None, match.group(0).strip(),
                        f"physical_{point_type}_section", point_type,
                    ))

            summary_start = _summary_section_start(primary)
            if summary_start >= 0:
                detector_parts.append("summary_sections")
                if "sammendrag av boligens tilstand" in low and "fordeling av tilstandsgrader" in low:
                    current_summary_tg: str | None = None
                    lines = list(re.finditer(r"(?m)^([^\n]+)$", primary))
                    index = 0
                    while index < len(lines):
                        line_match = lines[index]
                        if line_match.start() <= summary_start:
                            index += 1
                            continue
                        raw_line = line_match.group(1)
                        compact_line = " ".join(raw_line.split())
                        category = _summary_tg_category(compact_line)
                        if category:
                            current_summary_tg = category
                            index += 1
                            continue
                        if current_summary_tg and "gå til side" in compact_line.casefold():
                            row_start = line_match.start()
                            row_end = line_match.end()
                            combined = compact_line
                            while index + 1 < len(lines):
                                continuation = " ".join(lines[index + 1].group(1).split())
                                if not continuation:
                                    index += 1
                                    row_end = lines[index].end()
                                    continue
                                if "gå til side" in continuation.casefold():
                                    break
                                if _summary_tg_category(continuation) or _is_summary_category_reset(continuation):
                                    break
                                if _is_header_artifact_line(continuation):
                                    break
                                if re.fullmatch(r"[A-ZÆØÅ0-9 /().,-]{4,}", continuation):
                                    break
                                combined = f"{combined} {continuation}".strip()
                                index += 1
                                row_end = lines[index].end()
                                if ">" not in continuation:
                                    break
                            path = re.sub(r"(?i)\bGå\s+til\s+side\b", "", combined)
                            path = re.sub(r"^\d+\s+", "", path).strip(" -")
                            path = re.sub(r"\s+", " ", path)
                            if ">" in path and len(path) <= 240:
                                summary_navigation_starts.add(row_start)
                                markers.append(_Marker(
                                    InventoryRole.SUMMARY,
                                    page.number,
                                    base + row_start,
                                    None,
                                    path,
                                    current_summary_tg,
                                    primary[row_start:row_end].strip(),
                                    "physical_hierarchical_summary_row",
                                    "unknown",
                                ))
                            index += 1
                            continue
                        if _is_summary_category_reset(compact_line):
                            current_summary_tg = None
                        index += 1
                # Clickable "Gå til side" rows are navigation, not substantive
                # summaries. They remain traceable but never enter assessment.
                for match in re.finditer(r"(?im)^([^\n]{2,240}?)\s+Gå\s+til\s+side\s*$", primary):
                    if match.start() <= summary_start:
                        continue
                    if match.start() in summary_navigation_starts:
                        continue
                    path = match.group(1).strip()
                    if path.casefold() in {"arealer", "forutsetninger og vedlegg", "lovlighet"}:
                        continue
                    leaf = path.split(">")[-1].strip()
                    if not leaf:
                        continuation_lines = [
                            " ".join(line.split())
                            for line in primary[match.end():].splitlines()
                            if line.strip()
                        ]
                        next_line = continuation_lines[0] if continuation_lines else ""
                        if (
                            next_line
                            and "gå til side" not in next_line.casefold()
                            and not _summary_tg_category(next_line)
                            and not _is_summary_category_reset(next_line)
                            and not _is_header_artifact_line(next_line)
                        ):
                            path = f"{path} {next_line}".strip()
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
                r"AREALER,\s*BYGGETEGNINGER OG BRANNCELLER|"
                r"OPPSUMMERING / KONKLUSJON)\s*$",
                r"(?im)^(?:KILDER OG VEDLEGG|BEFARINGS\s*-\s*OG EIENDOMSOPPLYSNINGER|ROMFORDELING|TILSTANDSRAPPORTENS|FORUTSETNINGER)\s*$",
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

            # Every physical point heading is a stopping boundary, including
            # TG0/TG1 or other points that are not eligible for A4 assessment.
            # Otherwise a TG2 body can absorb the following point's description
            # before the next assessable "Vurdering av avvik" marker.
            for match in re.finditer(r"(?im)^Beskrivelse\s*$", primary):
                preceding = list(re.finditer(r"(?m)^([^\n]{2,180})$", primary[:match.start()]))
                heading = None
                for candidate in reversed(preceding):
                    value = candidate.group(1).strip()
                    if not _is_probable_heading(value):
                        continue
                    heading = candidate
                    break
                if heading is None:
                    continue
                local_start, title = heading.start(), heading.group(1).strip()
                absolute_start = base + local_start
                if any(item.role == InventoryRole.BOUNDARY and item.start == absolute_start for item in markers):
                    continue
                markers.append(_Marker(
                    InventoryRole.BOUNDARY, page.number, absolute_start, None,
                    title, None, title, "physical_point_heading_boundary", "unknown",
                ))

            # Some PDFs place the visible section title inside their table text
            # layer while the page's ordinary layer contains only explanatory
            # boilerplate. In that case the whole page is a structural boundary
            # for the preceding assessable point.
            if re.search(r"(?im)^Arealer,\s*byggetegninger og brannceller\s*$", page.text):
                absolute_start = page.start + (len(primary) - len(primary.lstrip()))
                if not any(item.role == InventoryRole.BOUNDARY and item.start == absolute_start for item in markers):
                    markers.append(_Marker(
                        InventoryRole.BOUNDARY, page.number, absolute_start, None,
                        "Arealer, byggetegninger og brannceller", None,
                        "Arealer, byggetegninger og brannceller",
                        "physical_table_layer_section_boundary", "unknown",
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
                    if (
                        title.isupper()
                        or "gå til side" in title.casefold()
                        or not _is_probable_heading(title)
                    ):
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
            trailing = _last_trailing_heading(primary)
            if trailing is None:
                continue
            title_pos, title = trailing
            next_description = re.search(r"(?im)^Beskrivelse\s*$", next_primary)
            next_tgiu = _TGIU_RE.search(next_primary[:900])
            if (
                not next_description or not next_tgiu or next_description.start() > next_tgiu.start()
                or len(title) > 120 or title.startswith(("•", "["))
                or not _is_probable_heading(title)
            ):
                continue
            if any(item.start == base + title_pos for item in markers):
                continue
            markers.append(_Marker(
                InventoryRole.PRIMARY, page.number, base + title_pos, None,
                title, "TGIU", title, "physical_cross_page_uninvestigated_section", "tgiu",
            ))

        # Preserve point headings that appear as the final visible line on one
        # page while the next page begins with the point body.
        for page_index, page in enumerate(pages[:-1]):
            primary, base = _primary_text(page)
            next_primary, _ = _primary_text(pages[page_index + 1])
            trailing = _last_trailing_heading(primary)
            if trailing is None:
                continue
            title_pos, title = trailing
            continued_description = re.search(r"(?im)^\s*Beskrivelse\s*$", next_primary[:1400])
            continued_prefix = next_primary[:continued_description.start()] if continued_description else ""
            prefix_lines = [
                line.strip()
                for line in continued_prefix.splitlines()
                if line.strip() and not _is_header_artifact_line(line.strip())
            ]
            prefix_has_new_major_heading = bool(
                re.search(r"(?m)^[A-ZÆØÅ][A-ZÆØÅ0-9 /-]{3,}$", continued_prefix[80:])
            )
            prefix_has_new_point_heading = any(
                _is_probable_heading(line)
                and ">" not in line
                and line.casefold() != title.casefold()
                for line in prefix_lines
            )
            starts_with_body = (
                continued_description is not None
                and continued_description.start() < 500
                and not prefix_has_new_major_heading
                and not prefix_has_new_point_heading
            )
            no_tg_continued_body = (
                continued_description is not None
                and not prefix_has_new_major_heading
                and not prefix_has_new_point_heading
                and _context_type(next_primary, 0, min(len(next_primary), 1400), None, title)[1]
                in {"electrical_no_tg", "legality_no_tg", "hms_no_tg", "methodology_only"}
            )
            has_next_vurdering = re.search(r"(?im)^Vurdering av avvik\s*:\s*$", next_primary[:2000]) is not None
            has_next_tgiu = _TGIU_RE.search(next_primary[:2200]) is not None
            if continued_description is not None and starts_with_body:
                after_description = next_primary[continued_description.end():min(len(next_primary), continued_description.end() + 2200)]
                next_heading_offset = _next_heading_boundary_offset(after_description)
                vurdering_after_description = re.search(r"(?im)^Vurdering av avvik\s*:\s*$", after_description)
                tgiu_after_description = _TGIU_RE.search(after_description[:2200])
                signal_positions = [
                    match.start()
                    for match in (vurdering_after_description, tgiu_after_description)
                    if match is not None
                ]
                if not signal_positions:
                    starts_with_body = False
                    no_tg_continued_body = False
                else:
                    first_signal = min(signal_positions)
                    if next_heading_offset is not None and first_signal > next_heading_offset:
                        starts_with_body = False
            if not (starts_with_body or no_tg_continued_body) or not (has_next_vurdering or has_next_tgiu):
                continue
            if any(item.role != InventoryRole.BOUNDARY and item.start == base + title_pos for item in markers):
                continue
            explicit_tg = "TGIU" if has_next_tgiu and not has_next_vurdering else None
            tg_grade, point_type = _context_type(next_primary, 0, min(len(next_primary), 2200), explicit_tg, title)
            if point_type == "hms_no_tg" and title == title.upper():
                continue
            if tg_grade is None and point_type == "unknown":
                tg_grade, point_type = "TG2", "graded"
            markers.append(_Marker(
                InventoryRole.PRIMARY, page.number, base + title_pos, None,
                title, tg_grade, title, "physical_cross_page_heading_continued_section", point_type,
            ))

        # Some layouts end a page immediately after "Title\nBeskrivelse", while
        # the descriptive body and "Vurdering av avvik" continue on the next
        # page. Preserve the title as the physical point rather than dropping it
        # and letting the following page header become the next title.
        for page_index, page in enumerate(pages[:-1]):
            primary, base = _primary_text(page)
            next_primary, _ = _primary_text(pages[page_index + 1])
            if not re.search(r"(?im)^Vurdering av avvik\s*:\s*$", next_primary[:1400]):
                continue
            trailing_description = list(re.finditer(r"(?im)^Beskrivelse\s*$", primary))
            if not trailing_description:
                continue
            description = trailing_description[-1]
            trailing_after_description = _trim_body_artifacts(
                primary[description.end():],
                is_first_span=False,
            ).strip()
            if trailing_after_description:
                continue
            local_start, title = _previous_heading_start(primary, description.end())
            if title == "Uidentifisert rapportpunkt" or not _is_probable_heading(title):
                continue
            if any(item.role != InventoryRole.BOUNDARY and item.start == base + local_start for item in markers):
                continue
            next_boundary_offset = _next_heading_boundary_offset(next_primary)
            next_vurdering = re.search(r"(?im)^Vurdering av avvik\s*:\s*$", next_primary[:1800])
            if next_vurdering is None:
                continue
            if next_boundary_offset is not None and next_vurdering.start() > next_boundary_offset:
                continue
            tg_grade, point_type = _context_type(next_primary, 0, len(next_primary[:1400]), None, title)
            if tg_grade is None and point_type == "unknown":
                tg_grade, point_type = "TG2", "graded"
            markers.append(_Marker(
                InventoryRole.PRIMARY, page.number, base + local_start, None,
                title, tg_grade, title, "physical_cross_page_continued_section", point_type,
            ))

        # Preserve report points that start near the end of one page and continue
        # on the next page after the footer/image layer, even when some body text
        # already appears after "Beskrivelse" before the page break.
        for page_index, page in enumerate(pages[:-1]):
            primary, base = _primary_text(page)
            next_primary, _ = _primary_text(pages[page_index + 1])
            vurdering = re.search(r"(?im)^Vurdering av avvik\s*:\s*$", next_primary[:1800])
            if not vurdering:
                continue
            trailing_description = list(re.finditer(r"(?im)^Beskrivelse\s*$", primary))
            if not trailing_description:
                continue
            description = trailing_description[-1]
            local_start, title = _previous_heading_start(primary, description.end())
            if title == "Uidentifisert rapportpunkt" or not _is_probable_heading(title):
                continue
            remainder = _trim_body_artifacts(
                primary[description.end():],
                is_first_span=False,
            ).strip()
            if not remainder:
                continue
            if re.search(r"(?im)^[A-ZÆØÅ][A-ZÆØÅ ,/&()\\-]{4,}$", remainder):
                continue
            if any(item.role != InventoryRole.BOUNDARY and item.start == base + local_start for item in markers):
                continue
            tg_grade, point_type = _context_type(next_primary, 0, len(next_primary[:1800]), None, title)
            if tg_grade is None and point_type == "unknown":
                continue
            markers.append(_Marker(
                InventoryRole.PRIMARY, page.number, base + local_start, None,
                title, tg_grade, title, "physical_cross_page_body_continued_section", point_type,
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
            if marker.method == "physical_detached_building_methodology_section":
                later_markers = [
                    item for item in later_markers
                    if not (
                        item.role == InventoryRole.BOUNDARY
                        and item.method == "physical_point_heading_boundary"
                    )
                ]
            if marker.method == "physical_cross_page_heading_continued_section":
                later_markers = [
                    item for item in later_markers
                    if item.role != InventoryRole.BOUNDARY
                    and item.start > marker.start + len(marker.title) + 5
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
                for relative_start, relative_end, exact in _body_span_chunks(
                    raw,
                    is_first_span=not body_spans,
                ):
                    exact_start = span_start + relative_start
                    exact_end = span_start + relative_end
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
            if (
                marker.method == "physical_cross_page_heading_continued_section"
                and len(body_spans) == 1
                and body_spans[0].exact_quote.strip() == marker.title
            ):
                next_page = next((item for item in pages if item.number == marker.page + 1), None)
                if next_page is not None:
                    next_primary, next_base = _primary_text(next_page)
                    descriptions = list(re.finditer(r"(?im)^Beskrivelse\s*$", next_primary))
                    if descriptions:
                        boundary_local = len(next_primary)
                        for description in descriptions[1:]:
                            heading_start, title = _previous_heading_start(next_primary, description.start())
                            if title != "Uidentifisert rapportpunkt" and _is_probable_heading(title):
                                boundary_local = heading_start
                                break
                        raw = next_primary[:boundary_local]
                        for relative_start, relative_end, exact in _body_span_chunks(raw, is_first_span=False):
                            exact_start = next_base + relative_start
                            exact_end = next_base + relative_end
                            body_spans.append(SourceEvidence(
                                evidence_id=_id("source", document_hash, next_page.number, exact_start, exact_end),
                                exact_quote=exact,
                                page=next_page.number,
                                char_start=exact_start,
                                char_end=exact_end,
                                quote_sha256=hashlib.sha256(exact.encode()).hexdigest(),
                                match_method="exact",
                                validation_status=ValidationStatus.VALIDATED,
                                validation_notes=["physical_source_inventory_reversible_page_span"],
                            ))
            if not body_spans:
                continue
            body_spans = [
                span for span in body_spans
                if span.exact_quote.strip()
                and not (
                    marker.role == InventoryRole.PRIMARY
                    and len(span.exact_quote.strip().splitlines()) == 1
                    and _is_probable_heading(span.exact_quote.strip())
                )
            ]
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

        # Canonicalize local or omitted point labels under an explicitly
        # numbered main section. This is structural identity only: no provider,
        # TG or substantive assessment rule participates in the decision.
        section_ordinals: dict[str, int] = {}
        canonicalized: list[PhysicalReportPoint] = []
        for point in points:
            if point.role != InventoryRole.PRIMARY:
                canonicalized.append(point)
                continue
            section_match = re.match(r"^\s*(\d+)\.", point.section_context or "")
            if not section_match:
                canonicalized.append(point)
                continue
            section = section_match.group(1)
            section_ordinals[section] = section_ordinals.get(section, 0) + 1
            local = (point.point_label or "").strip().rstrip(".")
            if local.startswith(f"{section}."):
                canonical = local
            elif re.fullmatch(r"\d+(?:\.\d+)*", local):
                canonical = f"{section}.{local}"
            else:
                canonical = f"{section}.{section_ordinals[section]}"
            canonicalized.append(point.model_copy(update={"point_label": canonical}))
        points = canonicalized

        # Cross-page title preservation may briefly coexist with a later
        # in-page heading recovery of the same physical point. Keep the higher
        # fidelity body/title instance rather than materializing duplicate
        # physical points.
        deduped: list[PhysicalReportPoint] = []
        primary_index: dict[tuple[str, str, str, str], int] = {}
        for point in points:
            if point.role != InventoryRole.PRIMARY:
                deduped.append(point)
                continue
            key = (
                _normalized_identity(point.title),
                _normalized_identity(point.section_context),
                str(point.tg_grade or ""),
                point.point_type,
            )
            existing_index = primary_index.get(key)
            if existing_index is None:
                primary_index[key] = len(deduped)
                deduped.append(point)
                continue
            existing = deduped[existing_index]
            same_window = abs(point.char_start - existing.char_start) <= 4000
            cross_page_duplicate = {
                point.detection_method,
                existing.detection_method,
            } & {"physical_cross_page_heading_continued_section"}
            if not same_window or not cross_page_duplicate:
                primary_index[key] = len(deduped)
                deduped.append(point)
                continue

            def quality(item: PhysicalReportPoint) -> tuple[int, int, int]:
                body_chars = sum(len(span.exact_quote.strip()) for span in item.body_spans)
                return (
                    int(item.detection_method != "physical_cross_page_heading_continued_section"),
                    body_chars,
                    -item.char_start,
                )

            if quality(point) > quality(existing):
                deduped[existing_index] = point
        points = deduped

        page_text_by_number = {}
        page_base_by_number = {}
        for page in pages:
            primary_text, base = _primary_text(page)
            page_text_by_number[page.number] = primary_text
            page_base_by_number[page.number] = base
        summary_support: list[PhysicalReportPoint] = []
        for point in points:
            if (
                point.role == InventoryRole.NAVIGATION
                and point.detection_method == "physical_navigation_summary_row"
                and ">" in point.title
            ):
                page_text = page_text_by_number.get(point.page, "")
                relative_pos = max(0, point.char_start - page_base_by_number.get(point.page, point.char_start))
                inferred_tg = _summary_category_before_position(page_text, relative_pos)
                if inferred_tg in {"TG2", "TG3", "TGIU"}:
                    resolved_title = _summary_navigation_path(page_text, relative_pos) or point.title
                    hierarchy = [part.strip() for part in resolved_title.split(">") if part.strip()]
                    summary_support.append(point.model_copy(update={
                        "title": resolved_title,
                        "section_context": " > ".join(hierarchy[:-1]) if len(hierarchy) >= 2 else point.section_context,
                        "tg_grade": inferred_tg,
                        "detection_method": "physical_hierarchical_summary_row",
                    }))
                    continue
            summary_support.append(point)
        points = summary_support

        # Link summaries to a primary only when the complete hierarchy is safe.
        # Local-title similarity alone is intentionally insufficient.
        primaries = [item for item in points if item.role == InventoryRole.PRIMARY]
        linked: list[PhysicalReportPoint] = []
        assigned_primary_ids: set[str] = set()
        for point in points:
            summary_like = (
                point.role == InventoryRole.SUMMARY
                or point.detection_method == "physical_hierarchical_summary_row"
            )
            if not summary_like:
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
            summary_leaf = point.title.split(">")[-1].strip()
            summary_leaf_norm = normalized(summary_leaf)
            summary_leaf_tokens = tokens(summary_leaf)
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
                if (
                    point.detection_method == "physical_hierarchical_summary_row"
                ):
                    leaf_overlap = summary_leaf_tokens & primary_tokens
                    if primary_norm and summary_leaf_norm and (
                        primary_norm == summary_leaf_norm
                        or primary_norm in summary_leaf_norm
                        or summary_leaf_norm in primary_norm
                    ):
                        details["leaf_title"] = 90
                        score += details["leaf_title"]
                    elif not leaf_overlap:
                        return None
                    else:
                        details["leaf_title_tokens"] = 20 * len(leaf_overlap)
                        score += details["leaf_title_tokens"]
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
                if (
                    point.detection_method != "physical_hierarchical_summary_row"
                    and primary.char_start >= point.char_start
                ):
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

        summary_tg_by_primary_id: dict[str, set[str]] = {}
        for point in linked:
            if (
                (
                    point.role == InventoryRole.SUMMARY
                    or point.detection_method == "physical_hierarchical_summary_row"
                )
                and point.linked_primary_id
                and point.tg_grade in {"TG2", "TG3", "TGIU"}
            ):
                summary_tg_by_primary_id.setdefault(point.linked_primary_id, set()).add(point.tg_grade)

        promoted: list[PhysicalReportPoint] = []
        for point in linked:
            if point.role != InventoryRole.PRIMARY:
                promoted.append(point)
                continue
            promoted_tgs = summary_tg_by_primary_id.get(point.inventory_id, set())
            if (
                point.detection_method == "physical_vurdering_av_avvik"
                and len(promoted_tgs) == 1
            ):
                promoted_tg = next(iter(promoted_tgs))
                if point.tg_grade != promoted_tg:
                    promoted.append(point.model_copy(update={
                        "tg_grade": promoted_tg,
                        "point_type": "tgiu" if promoted_tg == "TGIU" else "graded",
                        "detection_method": "physical_vurdering_av_avvik_summary_grounded",
                    }))
                    continue
            promoted.append(point)
        linked = promoted

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
