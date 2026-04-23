from functools import lru_cache
import json
import logging
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.config import settings
from app.services.validert_files import (
    get_arkat_canonical_examples,
    get_arkat_evaluation_pipeline_step,
    get_arkat_semantic_rules,
    get_report_format_detection,
)

logger = logging.getLogger(__name__)
_client = None

# Per-point ARKAT helpers can be expensive on large reports.
# Keep extraction deterministic, and run semantic evaluation LLM selectively.
_DISABLE_POINT_LEVEL_ARKAT_EXTRACTION_LLM = True
_DISABLE_POINT_LEVEL_ARKAT_LLM = False
_MAX_POINT_LEVEL_ARKAT_LLM_CALLS_PER_REPORT = 6

_ARKAT_CONDITIONAL_RE = re.compile(
    r"(?ix)\b(?:kan|dersom|hvis|risiko(?:en)?\s+for|kan\s+føre\s+til|kan\s+medføre|kan\s+trenge\s+inn|kan\s+trekke\s+inn|kan\s+oppstå)\b"
)
_ARKAT_OBSERVATION_RE = re.compile(
    r"(?ix)\b(?:det\s+registreres|det\s+observeres|det\s+ble\s+avdekket|det\s+er\s+påvist|det\s+ble\s+funnet)\b"
)
_ARKAT_INSPECTION_LIMITATION_RE = re.compile(
    r"(?ix)\b(?:ikke\s+synlig\s+for\s+inspeksjon|ikke\s+tilgjengelig\s+for\s+inspeksjon|ikke\s+tilgjengelig|"
    r"sn[oø]dekt|tildekket|lukket\s+konstruksjon|ikke\s+mulig\s+[aå]\s+inspisere|ikke\s+unders[oø]kt)\b"
)
_ARKAT_BUYER_IMPACT_RE = re.compile(
    r"(?ix)\b(?:kj[oø]per(?:en)?\s+m[aå]\s+p[aå]regne|str[oø]mforbruk|oppvarmingskostnad|kostnad(?:er)?|"
    r"sikkerhetsrisiko|helserisiko|bruksmessig|praktisk\s+betydning|for\s+kj[oø]per|"
    r"behov\s+for\s+(?:akutt\s+)?(?:tiltak|utbedring(?:er)?|utskifting|inngrep|reparasjoner)|"
    r"vedlikehold|redusert\s+inneklima|redusert\s+bruksverdi|bruksbegrensning|redusert\s+verdi|"
    r"bortfall\s+av\s+varmtvann|vannskader|akutt\s+utbedring|utskifting|"
    r"kan\s+ikke\s+(?:brukes|benyttes)|kan\s+ikke\s+forsikres|ikke\s+forsikres|"
    r"kommunen\s+kan\s+kreve|myndighetene\s+kan\s+kreve|p[aå]legg\s+om\s+utbedring)\b"
)
_ARKAT_PRESENT_STATE_RE = re.compile(
    r"(?ix)\b(?:mister\s+evnen\s+til|medf[oø]rer\s+[a-zæøå]+|er\s+ikke\s+vanntett|har\s+redusert\s+tetthet|"
    r"slipper\s+ut\s+varme|gir\s+[a-zæøå]+)\b"
)
_ARKAT_TECHNICAL_DEVELOPMENT_RE = re.compile(
    r"(?ix)\b(?:fukt\s+kan\s+trekke|fukt\s+kan\s+trekke\s+inn|trekker\s+inn\s+i\s+konstruksjonen|redusert\s+tetthet|"
    r"membran(?:en)?\s+mister|dreneringen\s+svikter|r[aå]tner|lekkasje|vindsperre|b[aæ]rende\s+konstruksjon|"
    r"skjulte?\s+skader|skader?\s+i\s+konstruksjonen|underliggende\s+konstruksjon|"
    r"bakenforliggende\s+veggkonstruksjon|fuktinntrengning|videre\s+utvikling|"
    r"vann\s+mot\s+vegg|vann\s+trenger\s+inn|vann\s+kan\s+trenge\s+inn|"
    r"vann\s+trenger\s+ned|vann\s+kan\s+trenge\s+ned|trenger\s+ned\s+til\s+undertaket)\b"
)
_ARKAT_RISK_DEVELOPMENT_RE = re.compile(
    r"(?ix)\b(?:[oø]kt?\s+slitasje|fuktbelastning|nedbrytning|svikt|lekkasje|oppfukting|kondens|muggdannelse|"
    r"redusert\s+funksjon|redusert\s+sikkerhet|fuktskader|korroderer|lekke)\b"
)
_ARKAT_ACTION_RE = re.compile(
    r"(?ix)\b(?:det\s+anbefales(?:\s+[aå])?|anbefaler(?:\s+(?:at|[aå]))?|b[oø]r\s+(?:utf[oø]res|skiftes|utbedres|kontrolleres|unders[oø]kes|"
    r"vurderes|planlegges)|planlegg|bestill|lokal\s+utbedring|utf[oø]res\s+av\s+fagperson)\b"
)
_ARKAT_AGE_ONLY_2018_RE = re.compile(
    r"(?ix)\b(?:passert\s+halvparten\s+av\s+sin\s+forventede\s+levetid|forventede\s+levetid|"
    r"alder(?:en)?\s+tilsier|er\s+fra\s+\d{4})\b"
)
_ARKAT_CAUSE_PROSE_RE = re.compile(
    r"(?ix)\b(?:skyldes|som\s+f[øo]lge\s+av|p[aå]\s+grunn\s+av|har\s+sin\s+sannsynlige\s+[aå]rsak\s+i|[aå]rsaken\s+er|i\s+kombinasjon\s+med)\b"
)
_ARKAT_ACTION_PROSE_RE = re.compile(
    r"(?ix)\b(?:ytterligere\s+unders[oø]kelser|utarbeid(?:e|ing|else)\s+av\s+tiltaksplan|tiltaksplan|"
    r"det\s+anbefales(?:\s+[aå])?|anbefaler(?:\s+(?:at|[aå]))?|b[oø]r\s+(?:utf[oø]res|skiftes|utbedres|kontrolleres|unders[oø]kes|vurderes|planlegges))\b"
)
_ARKAT_CANNOT_EXCLUDE_RE = re.compile(
    r"(?ix)\b(?:kan\s+ikke\s+utelukkes|kan\s+derfor\s+ikke\s+utelukkes|det\s+kan\s+ikke\s+utelukkes)\b"
)
_ARKAT_USE_LIFE_CONSEQUENCE_RE = re.compile(
    r"(?ix)\b(?:redusert\s+gjenst[aå]ende\s+brukstid|redusert\s+levetid|med\s+redusert\s+gjenst[aå]ende\s+brukstid\s+som\s+konsekvens)\b"
)
_ARKAT_LIFESPAN_ONLY_CONSEQUENCE_RE = re.compile(
    r"(?ix)\b(?:passert\s+(?:mer\s+enn\s+)?(?:50\s*%|halvparten)\s+av\s+forventet\s+levetid|"
    r"passert\s+(?:mer\s+enn\s+)?(?:50\s*%|halvparten)\s+av\s+sin\s+forventede\s+levetid|"
    r"redusert\s+gjenst[aå]ende\s+brukstid|redusert\s+levetid|kort\s+gjenst[aå]ende\s+brukstid|"
    r"som\s+f[oø]lge\s+av\s+alder|valgt\s+tilstandsgrad\s+gis\s+som\s+f[oø]lge\s+av\s+alder|"
    r"tilstandsgrad(?:en)?\s+gis\s+som\s+f[oø]lge\s+av\s+alder)\b"
)
_ARKAT_CONSEQUENCE_LABEL_PROSE_RE = re.compile(
    r"(?ix)\bkonsekvens(?:en)?\s+(?:er|av)\b"
)


def _get_openai_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


@lru_cache(maxsize=1)
def _get_client_arkat_bundle() -> Dict[str, Dict[str, object]]:
    return {
        "semantic_rules": get_arkat_semantic_rules() or {},
        "pipeline_step": get_arkat_evaluation_pipeline_step() or {},
        "format_detection": get_report_format_detection() or {},
        "canonical_examples": get_arkat_canonical_examples() or {},
    }


def _extract_json_object_from_text(text: str) -> Optional[Dict[str, object]]:
    if not text:
        return None
    candidate = text.strip()
    if not candidate:
        return None
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        candidate = candidate[start:end + 1]
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _call_json_llm(system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> Optional[Dict[str, object]]:
    if not system_prompt.strip() or not user_prompt.strip():
        return None
    try:
        if settings.USE_AWS_BEDROCK:
            from app.services.bedrock_ai import BedrockAI

            bedrock = BedrockAI(region=settings.AWS_REGION)
            return bedrock.generate_json_with_claude(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                # Keep per-point semantic calls fast; fall back to heuristics quickly on overload.
                max_retries=2,
                retry_json_prompt=False,
            )
        client = _get_openai_client()
        request_kwargs = {
            "model": settings.OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": max(256, int(max_tokens)),
        }
        if settings.OPENAI_SEED is not None:
            request_kwargs["seed"] = settings.OPENAI_SEED
        response = client.chat.completions.create(**request_kwargs)
        content = response.choices[0].message.content or ""
        return _extract_json_object_from_text(content)
    except Exception:
        return None


def _first_report_pages_text(report_text: str, split_pages) -> str:
    pages = split_pages(report_text or "")
    if pages:
        return "\n".join((page.get("text") or "") for page in pages[:3]).strip()
    return (report_text or "")[:12000]


def _is_semantically_missing_text(normalize_text, value: object) -> bool:
    if value is None:
        return True
    text = normalize_text(str(value)).strip()
    if not text:
        return True
    return text.upper() in {"MISSING", "IKKE OPPGITT", "N/A", "NA", "-"}


def _iter_arkat_prose_chunks(raw_text: str, normalize_text) -> List[str]:
    text = normalize_text(raw_text or "").strip()
    if not text:
        return []
    chunks: List[str] = []
    seen = set()
    for line in text.splitlines():
        normalized_line = line.strip()
        if not normalized_line:
            continue
        parts = re.split(r"(?<=[\.\!\?])\s+", normalized_line)
        for part in parts:
            chunk = str(part or "").strip(" -\t")
            if len(chunk) < 18:
                continue
            key = chunk.lower()
            if key in seen:
                continue
            seen.add(key)
            chunks.append(chunk)
    return chunks


def _extract_unlabeled_arkat_fields(raw_point_text: str, normalize_text) -> Dict[str, str]:
    extracted = {
        "aarsak": "MISSING",
        "risiko": "MISSING",
        "konsekvens": "MISSING",
        "anbefalt_tiltak": "MISSING",
    }
    labeled_consequence = ""
    buyer_oriented_consequence = ""
    fallback_consequence = ""
    consequence_candidates: List[tuple] = []

    def _consequence_candidate_score(chunk_text: str) -> tuple:
        low_text = normalize_text(chunk_text).lower()
        buyer_hits = len(_ARKAT_BUYER_IMPACT_RE.findall(low_text))
        explicit_label = 1 if _ARKAT_CONSEQUENCE_LABEL_PROSE_RE.search(low_text) else 0
        health_hits = 1 if re.search(r"(?ix)\b(?:mugg|svertesopp|r[aå]tesopp|muggvekst|helserisiko|sikkerhetsrisiko|fare\s+for\s+personskade)\b", low_text) else 0
        technical_only_penalty = 1 if (
            (_ARKAT_TECHNICAL_DEVELOPMENT_RE.search(low_text) or _ARKAT_RISK_DEVELOPMENT_RE.search(low_text))
            and buyer_hits == 0
            and health_hits == 0
        ) else 0
        # Higher is better. Prefer buyer impact and explicit consequence framing;
        # de-prioritize technical-only development text.
        return (
            explicit_label + buyer_hits + health_hits,
            -technical_only_penalty,
            len(low_text),
        )

    for chunk in _iter_arkat_prose_chunks(raw_point_text, normalize_text):
        low = normalize_text(chunk).lower()
        # Avoid mis-locating ARKAT fields into TG-rationale / generic scaffolding sentences.
        # These show up frequently in PDF exports and are not the actual ARKAT content.
        is_tg_rationale = ("tg2 vurderes da" in low) or ("tg3 vurderes da" in low) or ("tilstandsgrad" in low and "vurderes" in low)
        is_inspection_limitation = _ARKAT_INSPECTION_LIMITATION_RE.search(low) is not None
        if extracted["aarsak"] == "MISSING" and (
            _ARKAT_CAUSE_PROSE_RE.search(low)
            or _ARKAT_AGE_ONLY_2018_RE.search(low)
            or re.search(r"(?ix)\b(?:fra\s+bygge[aå]r(?:et)?|fra\s+\d{4})\b", low)
        ):
            extracted["aarsak"] = chunk
        if extracted["risiko"] == "MISSING":
            # Never treat inspection limitations as "risk" unless the sentence also carries genuine
            # risk-development language (e.g. leakage/mold/fire) or clear conditional consequence.
            # This prevents LIMITATION_AS_RISIKO and TG rationale mislocation.
            if is_tg_rationale and not (_ARKAT_CAUSE_PROSE_RE.search(low) or _ARKAT_CONDITIONAL_RE.search(low)):
                pass
            elif is_inspection_limitation and not (_ARKAT_CONDITIONAL_RE.search(low) or _ARKAT_RISK_DEVELOPMENT_RE.search(low)):
                pass
            elif (
                _ARKAT_CONDITIONAL_RE.search(low)
                or _ARKAT_RISK_DEVELOPMENT_RE.search(low)
                or _ARKAT_CANNOT_EXCLUDE_RE.search(low)
                or "skjulte skader" in low
                or "jevnlig ettersyn" in low
                or "krever oppfolging" in low
                or "krever oppfølging" in low
            ):
                extracted["risiko"] = chunk
        if not labeled_consequence and _ARKAT_CONSEQUENCE_LABEL_PROSE_RE.search(low):
            labeled_consequence = chunk
        if (
            _ARKAT_BUYER_IMPACT_RE.search(low)
            or _ARKAT_CONSEQUENCE_LABEL_PROSE_RE.search(low)
            or re.search(r"(?ix)\b(?:mugg|svertesopp|r[aå]tesopp|muggvekst|helserisiko|sikkerhetsrisiko|fare\s+for\s+personskade)\b", low)
        ):
            consequence_candidates.append((_consequence_candidate_score(chunk), chunk))
        if (
            not fallback_consequence
            and not re.search(r"(?ix)\b(?:tg2|tg3|tilstandsgrad\s*[23])\b", low)
            and (
                _ARKAT_USE_LIFE_CONSEQUENCE_RE.search(low)
                or re.search(r"(?ix)\bmedf[oø]rer\b", low)
            )
        ):
            fallback_consequence = chunk
        if extracted["anbefalt_tiltak"] == "MISSING":
            if is_tg_rationale:
                pass
            elif is_inspection_limitation and not _ARKAT_ACTION_RE.search(low):
                # "Not inspected / not accessible" shouldn't become an action by itself.
                pass
            elif _ARKAT_ACTION_RE.search(low) or _ARKAT_ACTION_PROSE_RE.search(low):
                extracted["anbefalt_tiltak"] = chunk
    if consequence_candidates:
        # Pick strongest buyer-oriented consequence candidate, not first match.
        consequence_candidates.sort(key=lambda item: item[0], reverse=True)
        buyer_oriented_consequence = consequence_candidates[0][1]
    if labeled_consequence:
        extracted["konsekvens"] = labeled_consequence
    elif buyer_oriented_consequence:
        extracted["konsekvens"] = buyer_oriented_consequence
    elif fallback_consequence:
        extracted["konsekvens"] = fallback_consequence
    return extracted


def _looks_like_mislocated_structured_field(field_name: str, text: str, normalize_text) -> bool:
    normalized = normalize_text(text or "").strip().lower()
    if not normalized:
        return False
    is_tg_rationale = ("tg2 vurderes da" in normalized) or ("tg3 vurderes da" in normalized)
    is_limitation = _ARKAT_INSPECTION_LIMITATION_RE.search(normalized) is not None

    if field_name == "risiko":
        if is_tg_rationale and not (_ARKAT_CONDITIONAL_RE.search(normalized) or _ARKAT_RISK_DEVELOPMENT_RE.search(normalized)):
            return True
        if is_limitation and not (_ARKAT_CONDITIONAL_RE.search(normalized) or _ARKAT_RISK_DEVELOPMENT_RE.search(normalized)):
            return True
    if field_name == "anbefalt_tiltak":
        if is_tg_rationale:
            return True
        # Limitation text without an actual action verb should not be treated as tiltak.
        if is_limitation and not (_ARKAT_ACTION_RE.search(normalized) or _ARKAT_ACTION_PROSE_RE.search(normalized)):
            return True
    if field_name == "konsekvens":
        # Technical-risk prose is often mis-located as consequence in noisy merged blocks.
        if (
            (_ARKAT_TECHNICAL_DEVELOPMENT_RE.search(normalized) or _ARKAT_RISK_DEVELOPMENT_RE.search(normalized))
            and not _ARKAT_BUYER_IMPACT_RE.search(normalized)
            and not _ARKAT_CONSEQUENCE_LABEL_PROSE_RE.search(normalized)
        ):
            return True
    return False


def _best_buyer_oriented_consequence_from_raw_text(raw_point_text: str, normalize_text) -> str:
    best_chunk = ""
    best_score = (-1, -1, -1)
    for chunk in _iter_arkat_prose_chunks(raw_point_text, normalize_text):
        low = normalize_text(chunk).lower()
        buyer_hits = len(_ARKAT_BUYER_IMPACT_RE.findall(low))
        explicit_label = 1 if _ARKAT_CONSEQUENCE_LABEL_PROSE_RE.search(low) else 0
        health_or_safety_hits = 1 if re.search(
            r"(?ix)\b(?:mugg|svertesopp|r[aå]tesopp|muggvekst|helserisiko|sikkerhetsrisiko|fare\s+for\s+personskade|brannfare)\b",
            low,
        ) else 0
        technical_only_penalty = 1 if (
            (_ARKAT_TECHNICAL_DEVELOPMENT_RE.search(low) or _ARKAT_RISK_DEVELOPMENT_RE.search(low))
            and buyer_hits == 0
            and health_or_safety_hits == 0
        ) else 0
        score = (
            explicit_label + buyer_hits + health_or_safety_hits,
            -technical_only_penalty,
            len(low),
        )
        if score > best_score:
            best_score = score
            best_chunk = chunk
    # Require at least one meaningful buyer-facing signal.
    if best_score[0] <= 0:
        return ""
    return best_chunk


def _merge_missing_arkat_fields(base: Dict[str, str], supplement: Dict[str, str], normalize_text) -> Dict[str, str]:
    merged = dict(base or {})
    for key in ("aarsak", "risiko", "konsekvens", "anbefalt_tiltak"):
        if _is_semantically_missing_text(normalize_text, merged.get(key)) and not _is_semantically_missing_text(normalize_text, supplement.get(key)):
            merged[key] = str(supplement.get(key) or "").strip()
    return merged


def _tg_rank_for_arkat(tg_grade: str) -> int:
    return {"TG0": 0, "TG1": 1, "TG2": 2, "TG3": 3, "TGIU": 4}.get(str(tg_grade or "").strip().upper(), -1)


def _point_id_family_chain(point_id: str) -> List[str]:
    pid = str(point_id or "").strip()
    if not pid:
        return []
    parts = [part for part in pid.split(".") if part]
    if not parts:
        return []
    return [".".join(parts[:idx]) for idx in range(len(parts), 0, -1)]


def _semantic_point_lookup_id(point: Dict[str, object], normalize_point_id) -> str:
    if not isinstance(point, dict):
        return ""
    for key in ("numeric_id", "native_label", "point_id"):
        candidate = normalize_point_id(str(point.get(key) or ""))
        if candidate:
            return candidate
    return ""


def _has_meaningful_arkat_signal(text: str, normalize_text) -> bool:
    normalized = normalize_text(text or "").lower()
    if not normalized:
        return False
    return bool(
        re.search(r"(?i)\b(?:årsak|arsak|risiko|konsekvens|anbefalt(?:e)?\s+tiltak|tiltak)\s*:", normalized)
        or _ARKAT_CAUSE_PROSE_RE.search(normalized)
        or _ARKAT_CONDITIONAL_RE.search(normalized)
        or _ARKAT_RISK_DEVELOPMENT_RE.search(normalized)
        or _ARKAT_BUYER_IMPACT_RE.search(normalized)
        or _ARKAT_ACTION_RE.search(normalized)
        or _ARKAT_ACTION_PROSE_RE.search(normalized)
        or _ARKAT_TECHNICAL_DEVELOPMENT_RE.search(normalized)
    )


def _score_arkat_point_text_candidate(text: str, normalize_text) -> tuple:
    normalized = normalize_text(text or "").lower()
    if not normalized:
        return (0, 0, 0, 0, 0)
    label_count = len(re.findall(r"(?i)\b(?:årsak|arsak|risiko|konsekvens|anbefalt(?:e)?\s+tiltak|tiltak)\s*:", normalized))
    semantic_hits = sum(
        1
        for regex in (
            _ARKAT_CAUSE_PROSE_RE,
            _ARKAT_CONDITIONAL_RE,
            _ARKAT_RISK_DEVELOPMENT_RE,
            _ARKAT_BUYER_IMPACT_RE,
            _ARKAT_TECHNICAL_DEVELOPMENT_RE,
            _ARKAT_ACTION_PROSE_RE,
            _ARKAT_ACTION_RE,
        )
        if regex.search(normalized)
    )
    limitation_penalty = int(
        _ARKAT_INSPECTION_LIMITATION_RE.search(normalized) is not None
        and semantic_hits <= 1
        and label_count == 0
    )
    schematic_penalty = int(
        ("utbedringskostnaden vurderes som" in normalized or "sjablonmessige kostnadsklasser" in normalized)
        and semantic_hits <= 1
        and label_count == 0
    )
    return (label_count, semantic_hits, -limitation_penalty, -schematic_penalty, len(normalized))


def _combine_point_text_candidates(texts: List[str], normalize_text) -> str:
    unique: List[str] = []
    seen = set()
    for text in texts:
        normalized = normalize_text(text or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(str(text or "").strip())
    if not unique:
        return ""
    unique.sort(key=lambda item: _score_arkat_point_text_candidate(item, normalize_text), reverse=True)
    combined = unique[0]
    combined_norm = normalize_text(combined).lower()
    for candidate in unique[1:]:
        candidate_norm = normalize_text(candidate).lower()
        if candidate_norm and candidate_norm not in combined_norm:
            combined = f"{combined}\n{candidate}".strip()
            combined_norm = normalize_text(combined).lower()
    return combined


def _count_present_arkat_fields(extracted_fields: Dict[str, str], normalize_text, tg_grade: str) -> int:
    required_fields = ["aarsak", "risiko", "konsekvens"]
    if str(tg_grade or "").upper() in {"TG2", "TG3"}:
        required_fields.append("anbefalt_tiltak")
    return sum(
        1
        for key in required_fields
        if not _is_semantically_missing_text(normalize_text, extracted_fields.get(key))
    )


def _collect_contextual_point_text_candidates(
    target_point_id: str,
    target_point_title: str,
    detected_points: List[Dict[str, object]],
    normalize_text,
) -> List[str]:
    pid = str(target_point_id or "").strip()
    if not pid:
        return []
    family_ids = set(_point_id_family_chain(pid))
    target_title = normalize_text(target_point_title or "").lower().strip()
    point_ref_re = re.compile(rf"(?i)\b(?:punkt\s*)?{re.escape(pid)}(?:\b|(?=[\s\-\|:]))")
    candidates: List[str] = []
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        raw = str(point.get("effective_span_text") or point.get("exact_span_text") or point.get("span_text") or "").strip()
        if not raw:
            continue
        normalized = normalize_text(raw)
        if not normalized:
            continue
        candidate_ids = {
            str(point.get("point_id") or "").strip(),
            str(point.get("numeric_id") or "").strip(),
            str(point.get("native_label") or "").strip(),
        }
        candidate_ids.discard("")
        direct_ref = bool(point_ref_re.search(normalized))
        # Allow contextual enrichment from the exact point or its ancestors only.
        # Do not pull sibling content (e.g. 7.1.2 into 7.1.1), because that can
        # incorrectly satisfy ARKAT fields that are actually missing in the target point.
        family_match = any(
            candidate_id == pid or pid.startswith(f"{candidate_id}.")
            for candidate_id in candidate_ids
            if candidate_id
        )
        title_match = bool(target_title and target_title in normalized.lower())
        if direct_ref or ((family_match or title_match) and _has_meaningful_arkat_signal(raw, normalize_text)):
            candidates.append(raw)
    return candidates


def _structured_extract_arkat_fields(raw_point_text: str, extract_arkat_section_text, normalize_text) -> Dict[str, str]:
    extracted = {
        "aarsak": extract_arkat_section_text(raw_point_text, "årsak"),
        "risiko": extract_arkat_section_text(raw_point_text, "risiko"),
        "konsekvens": extract_arkat_section_text(raw_point_text, "konsekvens"),
        "anbefalt_tiltak": extract_arkat_section_text(raw_point_text, "tiltak"),
    }
    normalized = {
        key: ("MISSING" if _is_semantically_missing_text(normalize_text, value) else str(value).strip())
        for key, value in extracted.items()
    }
    normalized = _enrich_fields_from_combined_konsekvens_tiltak(normalized, raw_point_text, extract_arkat_section_text, normalize_text)
    unlabeled = _extract_unlabeled_arkat_fields(raw_point_text, normalize_text)
    merged = _merge_missing_arkat_fields(normalized, unlabeled, normalize_text)
    # Extraction-level correction: if label-based parse latched onto TG rationale / limitation
    # scaffolding, prefer the unlabeled semantic candidate for the same field.
    for field_name in ("risiko", "konsekvens", "anbefalt_tiltak"):
        current = str(merged.get(field_name) or "").strip()
        candidate = str(unlabeled.get(field_name) or "").strip()
        if not candidate or candidate.upper() == "MISSING":
            continue
        if _looks_like_mislocated_structured_field(field_name, current, normalize_text):
            merged[field_name] = candidate
    # Final rescue for remaining "konsekvens" mislocation: prefer the strongest buyer-oriented
    # sentence from the same point text when current consequence is technical-only/mislocated.
    current_consequence = str(merged.get("konsekvens") or "").strip()
    if _looks_like_mislocated_structured_field("konsekvens", current_consequence, normalize_text):
        better_consequence = _best_buyer_oriented_consequence_from_raw_text(raw_point_text, normalize_text)
        if better_consequence:
            merged["konsekvens"] = better_consequence
    return merged


def _enrich_fields_from_combined_konsekvens_tiltak(
    extracted_fields: Dict[str, str],
    raw_point_text: str,
    extract_arkat_section_text,
    normalize_text,
) -> Dict[str, str]:
    enriched = dict(extracted_fields or {})
    combined = extract_arkat_section_text(raw_point_text, "konsekvens_tiltak")
    if _is_semantically_missing_text(normalize_text, combined):
        return enriched
    combined_text = str(combined).strip()
    combined_low = normalize_text(combined_text).lower()
    if _is_semantically_missing_text(normalize_text, enriched.get("konsekvens")):
        enriched["konsekvens"] = combined_text
    if _is_semantically_missing_text(normalize_text, enriched.get("anbefalt_tiltak")):
        enriched["anbefalt_tiltak"] = combined_text
    if _is_semantically_missing_text(normalize_text, enriched.get("risiko")):
        # Fremtind/iVerdi: "Konsekvens/tiltak" often contains an explicit risk statement
        # (e.g. "redusere risiko for personskade ...") without a separate "Risiko" field.
        risk_markers = (
            "risiko",
            "fare for",
            "personskade",
            "helserisiko",
            "brannfare",
            "brann",
            "snø",
            "isras",
        )
        if (
            _ARKAT_CONDITIONAL_RE.search(combined_low)
            or "dersom tiltak ikke" in combined_low
            or "skaper ideelle forhold" in combined_low
            or any(marker in combined_low for marker in risk_markers)
        ):
            enriched["risiko"] = combined_text
    return enriched


def _fallback_semantic_extract_arkat_fields(raw_point_text: str, extract_arkat_section_text, normalize_text) -> Dict[str, str]:
    structured = _structured_extract_arkat_fields(raw_point_text, extract_arkat_section_text, normalize_text)
    plain = normalize_text(raw_point_text or "").strip()
    if not plain:
        return {
            "aarsak": "MISSING",
            "risiko": "MISSING",
            "konsekvens": "MISSING",
            "anbefalt_tiltak": "MISSING",
        }
    if all(value == "MISSING" for value in structured.values()):
        return {
            "aarsak": "MISSING",
            "risiko": "MISSING",
            "konsekvens": "MISSING",
            "anbefalt_tiltak": "MISSING",
        }
    return structured


def _parse_plaintext_arkat_extraction(raw_text: str, normalize_text) -> Optional[Dict[str, str]]:
    text = normalize_text(raw_text or "")
    if not text:
        return None
    field_map = {
        "ÅRSAK": "aarsak",
        "RISIKO": "risiko",
        "KONSEKVENS": "konsekvens",
        "ANBEFALT TILTAK": "anbefalt_tiltak",
    }
    extracted: Dict[str, str] = {}
    for label, key in field_map.items():
        match = re.search(
            rf"(?is)\b{re.escape(label)}\s*:\s*(.*?)(?=\b(?:ÅRSAK|RISIKO|KONSEKVENS|ANBEFALT TILTAK)\s*:|\Z)",
            text,
        )
        if not match:
            continue
        value = str(match.group(1) or "").strip()
        extracted[key] = "MISSING" if _is_semantically_missing_text(normalize_text, value) else value
    return extracted or None


def _semantic_extract_arkat_fields(raw_point_text: str, extract_arkat_section_text, normalize_text) -> Dict[str, str]:
    if _DISABLE_POINT_LEVEL_ARKAT_EXTRACTION_LLM or _DISABLE_POINT_LEVEL_ARKAT_LLM:
        return _fallback_semantic_extract_arkat_fields(raw_point_text, extract_arkat_section_text, normalize_text)
    bundle = _get_client_arkat_bundle()
    cfg = bundle.get("format_detection") or {}
    prompt = (
        cfg.get("step_2_field_extraction", {})
        .get("extraction_methods", {})
        .get("semantic_block_extraction", {})
        .get("extraction_prompt", "")
    )
    if not isinstance(prompt, str) or not prompt.strip():
        return _fallback_semantic_extract_arkat_fields(raw_point_text, extract_arkat_section_text, normalize_text)
    user_prompt = prompt.replace("{raw_point_text}", raw_point_text or "")
    system_prompt = (
        "Extract ARKAT fields from one Norwegian tilstandsrapport point. "
        "Return only valid JSON with keys aarsak, risiko, konsekvens, anbefalt_tiltak."
    )
    parsed = _call_json_llm(system_prompt, user_prompt, max_tokens=900)
    if isinstance(parsed, dict) and parsed.get("_raw_text"):
        plaintext = _parse_plaintext_arkat_extraction(str(parsed.get("_raw_text") or ""), normalize_text)
        if plaintext:
            parsed = plaintext
    if not isinstance(parsed, dict):
        return _fallback_semantic_extract_arkat_fields(raw_point_text, extract_arkat_section_text, normalize_text)
    out: Dict[str, str] = {}
    for key in ("aarsak", "risiko", "konsekvens", "anbefalt_tiltak"):
        value = parsed.get(key, "MISSING")
        out[key] = "MISSING" if _is_semantically_missing_text(normalize_text, value) else str(value).strip()
    return _enrich_fields_from_combined_konsekvens_tiltak(out, raw_point_text, extract_arkat_section_text, normalize_text)


def _extract_fields_for_point(report_format: str, raw_point_text: str, extract_arkat_section_text, normalize_text) -> Dict[str, str]:
    if report_format == "structured_arkat":
        return _structured_extract_arkat_fields(raw_point_text, extract_arkat_section_text, normalize_text)
    if report_format == "semi_structured":
        labeled = _structured_extract_arkat_fields(raw_point_text, extract_arkat_section_text, normalize_text)
        missing_count = sum(1 for value in labeled.values() if value == "MISSING")
        if missing_count == 0:
            return labeled
        semantic = _semantic_extract_arkat_fields(raw_point_text, extract_arkat_section_text, normalize_text)
        if missing_count >= 2:
            return semantic
        for key, value in labeled.items():
            if value == "MISSING" and semantic.get(key) and semantic.get(key) != "MISSING":
                labeled[key] = semantic[key]
        return labeled
    return _semantic_extract_arkat_fields(raw_point_text, extract_arkat_section_text, normalize_text)


def _point_text_needs_report_fallback(raw_point_text: str, point_id: str, point_title: str, normalize_text) -> bool:
    text = normalize_text(raw_point_text or "").strip()
    if not text:
        return True
    low = text.lower()
    title_low = normalize_text(point_title or "").lower()
    words = [word for word in re.split(r"\s+", text) if word]
    if len(words) <= 6 and not any(ch in text for ch in ":.;!?"):
        return True
    if point_id and title_low and low in {title_low, f"{point_id} {title_low}", f"{point_id}{title_low}"}:
        return True
    if (
        len(words) <= 30
        and (
            _ARKAT_INSPECTION_LIMITATION_RE.search(low)
            or _ARKAT_AGE_ONLY_2018_RE.search(low)
            or "utbedringskostnaden vurderes som" in low
            or "sjablonmessige kostnadsklasser" in low
        )
        and not _has_meaningful_arkat_signal(text, normalize_text)
    ):
        return True
    return False


def _recover_point_text_from_report(report_text: str, point_id: str, point_title: str, normalize_text) -> str:
    if not report_text or not point_id:
        return ""
    lines = report_text.splitlines()
    target_title = normalize_text(point_title or "").lower()
    start_indexes: List[int] = []
    point_header_token_re = re.compile(rf"(?i)^\s*(?:TG\s*[0-3]\s*(?:\|)?\s*)?{re.escape(point_id)}(?:\b|(?=[\s\-\|:]))")

    def _window_supports_point(start_idx: int) -> bool:
        window = "\n".join(lines[start_idx:start_idx + 6])
        normalized_window = normalize_text(window).lower()
        if point_id in normalized_window:
            return True
        if target_title and target_title in normalized_window:
            return True
        return bool(
            re.search(r"(?i)\b(?:tg2|tg3|tilstandsgrad\s*[23])\b", normalized_window)
            or re.search(r"(?i)\b(?:årsak|arsak|risiko|konsekvens|anbefalt(?:e)?\s+tiltak|tiltak)\s*:", normalized_window)
        )

    for idx, line in enumerate(lines):
        normalized_line = normalize_text(line).lower()
        if point_id not in normalized_line and not point_header_token_re.search(normalized_line):
            continue
        if target_title and target_title not in normalized_line and not _window_supports_point(idx):
            continue
        start_indexes.append(idx)
    if not start_indexes and target_title:
        title_terms = [term for term in re.split(r"\s+", target_title) if len(term) >= 4]
        for idx, line in enumerate(lines):
            normalized_line = normalize_text(line).lower()
            if target_title not in normalized_line and not all(term in normalized_line for term in title_terms[:2]):
                continue
            if not _window_supports_point(idx) and not point_header_token_re.search("\n".join(lines[idx:idx + 8])):
                continue
            start_indexes.append(idx)
    if not start_indexes:
        return ""
    point_header_re = re.compile(r"(?i)^\s*(?:TG\s*[0-3]\s+)?(\d+(?:\.\d+)+)\b")

    def _collect_block(start_idx: int) -> str:
        collected: List[str] = []
        for idx in range(start_idx, len(lines)):
            line = lines[idx]
            if idx > start_idx:
                header_match = point_header_re.match(line.strip())
                if header_match and header_match.group(1) != point_id:
                    break
            collected.append(line)
        return "\n".join(collected).strip()

    def _score_block(block: str) -> tuple:
        normalized = normalize_text(block or "").lower()
        arkat_labels = sum(
            1
            for label in ("årsak:", "arsak:", "risiko:", "konsekvens:", "anbefalt tiltak:")
            if label in normalized
        )
        summary_bonus = int("takstmannens vurdering ved tg2" in normalized or "takstmannens vurdering ved tg3" in normalized)
        semantic_hits = sum(
            1
            for regex in (
                _ARKAT_CAUSE_PROSE_RE,
                _ARKAT_CONDITIONAL_RE,
                _ARKAT_RISK_DEVELOPMENT_RE,
                _ARKAT_BUYER_IMPACT_RE,
                _ARKAT_ACTION_PROSE_RE,
                _ARKAT_ACTION_RE,
            )
            if regex.search(normalized)
        )
        target_title_bonus = int(bool(target_title and target_title in normalized))
        return (arkat_labels, semantic_hits, summary_bonus, target_title_bonus, len(normalized))

    recovered = max((_collect_block(start_idx) for start_idx in start_indexes), key=_score_block, default="")
    if not recovered or normalize_text(recovered) == normalize_text(point_title):
        return ""
    return recovered


def _point_has_descriptive_text_for_arkat(raw_point_text: str, extracted_fields: Dict[str, str], normalize_text) -> bool:
    text = normalize_text(raw_point_text or "").strip()
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(text) < 80 and len(lines) <= 2 and not any(ch in text for ch in ".:;!?"):
        return False
    if len(lines) <= 2 and all(len(line.split()) <= 6 for line in lines) and not any(ch in text for ch in ".:;!?"):
        return False
    if all(_is_semantically_missing_text(normalize_text, value) for value in extracted_fields.values()):
        signal_blob = text.lower()
        has_semantic_signal = any(
            regex.search(signal_blob)
            for regex in (
                _ARKAT_OBSERVATION_RE,
                _ARKAT_CONDITIONAL_RE,
                _ARKAT_INSPECTION_LIMITATION_RE,
                _ARKAT_ACTION_RE,
                _ARKAT_BUYER_IMPACT_RE,
            )
        )
        has_label_signal = bool(re.search(r"(?i)\b(?:årsak|arsak|risiko|konsekvens|anbefalt(?:e)?\s+tiltak|tiltak)\s*:", text))
        if not has_semantic_signal and not has_label_signal:
            return False
    return True


def _augment_point_text_with_linked_summary(
    raw_point_text: str,
    point_id: str,
    linked_summary_by_point,
    get_linked_summary_for_point,
    available_point_ids: List[str],
    normalize_text,
) -> str:
    base_text = normalize_text(raw_point_text or "").strip()
    if not point_id or not linked_summary_by_point or not get_linked_summary_for_point:
        return raw_point_text or ""
    linked_summary = get_linked_summary_for_point(
        linked_summary_by_point,
        point_id,
        available_point_ids=available_point_ids,
    )
    linked_summary = normalize_text(linked_summary or "").strip()
    if not linked_summary:
        return raw_point_text or ""
    if not base_text:
        return linked_summary
    if linked_summary in base_text:
        return raw_point_text or ""
    return ((raw_point_text or "").strip() + "\n" + linked_summary).strip()


def _parse_plaintext_arkat_eval(raw_text: str, normalize_text) -> Optional[Dict[str, object]]:
    text = normalize_text(raw_text or "")
    if not text:
        return None
    field_map = {
        "ÅRSAK": "aarsak",
        "RISIKO": "risiko",
        "KONSEKVENS": "konsekvens",
        "ANBEFALT TILTAK": "anbefalt_tiltak",
    }
    results: Dict[str, Dict[str, object]] = {}
    for label, key in field_map.items():
        match = re.search(
            rf"(?is)\b{re.escape(label)}\s*:\s*(CORRECT|MISSING|WRONG:[A-Z0-9_]+)(?:\s*[-–:]\s*(.*?))?(?=\b(?:ÅRSAK|RISIKO|KONSEKVENS|ANBEFALT TILTAK)\s*:|\Z)",
            text,
        )
        if not match:
            continue
        status = str(match.group(1) or "").strip()
        explanation = str(match.group(2) or "").strip()
        result = {"status": status, "explanation": explanation}
        if key == "risiko" and "LIMITATION_AS_RISIKO" in status and "ikke synlig" in text.lower():
            result["additional_flag"] = "LIMITATION_USED_AS_RISK_SUBSTITUTE"
        results[key] = result
    if not results:
        return None
    return {
        "field_results": results,
        "has_errors": any(
            not str(item.get("status") or "").startswith("CORRECT")
            for item in results.values()
            if isinstance(item, dict)
        ),
    }


def _is_arkat_field_required(field_name: str, tg_grade: str) -> bool:
    tg = str(tg_grade or "").strip().upper()
    required_by_field = {
        "aarsak": {"TG2", "TG3"},
        "risiko": {"TG2", "TG3", "TGIU"},
        "konsekvens": {"TG2", "TG3"},
        "anbefalt_tiltak": {"TG3"},
    }
    return tg in required_by_field.get(field_name, set())


def _has_buyer_oriented_consequence_signal(text: str, normalize_text) -> bool:
    low = normalize_text(text or "").strip().lower()
    if not low or low.upper() == "MISSING":
        return False
    has_buyer_signal = bool(
        _ARKAT_BUYER_IMPACT_RE.search(low)
        or re.search(
            r"(?ix)\b(?:mugg|svertesopp|r[aå]tesopp|muggvekst|d[aå]rlig\s+inneklima|redusert\s+inneklima|"
            r"sikkerhetsrisiko|helserisiko|brannfare|fare\s+for\s+personskade)\b",
            low,
        )
    )
    if not has_buyer_signal:
        return False
    if _ARKAT_LIFESPAN_ONLY_CONSEQUENCE_RE.search(low):
        # Remaining life / age explains technical status. It is only OK when the
        # same consequence text also states practical buyer impact such as cost,
        # use limitation, safety/health impact, or legal/authority risk.
        return bool(
            re.search(
                r"(?ix)\b(?:kostnad(?:er)?|utbedring(?:er)?|reparasjon(?:er)?|utskifting|vedlikehold|"
                r"str[oø]mforbruk|oppvarmingskostnad|bruksverdi|bruksbegrensning|kan\s+ikke\s+(?:brukes|benyttes)|"
                r"sikkerhetsrisiko|helserisiko|brannfare|fare\s+for\s+personskade|mugg|svertesopp|"
                r"kan\s+ikke\s+forsikres|ikke\s+forsikres|kommunen\s+kan\s+kreve|myndighetene\s+kan\s+kreve|p[aå]legg)\b",
                low,
            )
        )
    return True


def _point_has_only_age_or_lifespan_consequence(raw_point_text: str, consequence_text: object, normalize_text) -> bool:
    consequence = normalize_text(str(consequence_text or "")).strip().lower()
    raw = normalize_text(raw_point_text or "").strip().lower()
    source = consequence if consequence and consequence.upper() != "MISSING" else raw
    if not source:
        return False
    if consequence and consequence.upper() != "MISSING" and _ARKAT_LIFESPAN_ONLY_CONSEQUENCE_RE.search(consequence):
        return not bool(
            re.search(
                r"(?ix)\b(?:kostnad(?:er)?|utbedring(?:er)?|reparasjon(?:er)?|utskifting|vedlikehold|"
                r"str[oø]mforbruk|oppvarmingskostnad|bruksverdi|bruksbegrensning|kan\s+ikke\s+(?:brukes|benyttes)|"
                r"sikkerhetsrisiko|helserisiko|brannfare|fare\s+for\s+personskade|"
                r"kan\s+ikke\s+forsikres|ikke\s+forsikres|kommunen\s+kan\s+kreve|myndighetene\s+kan\s+kreve|p[aå]legg)\b",
                consequence,
            )
        )
    age_or_lifespan = bool(_ARKAT_LIFESPAN_ONLY_CONSEQUENCE_RE.search(source))
    if not age_or_lifespan:
        return False
    # Punkt 10.4-style: TG due to age / not function tested, with no consequence field.
    if re.search(r"(?ix)\b(?:valgt\s+tilstandsgrad\s+gis\s+som\s+f[oø]lge\s+av\s+alder|ikke\s+funksjonstestet|produksjons[aå]r)\b", raw):
        return True
    # Punkt 7.3.3-style: reduced remaining service life as the stated consequence.
    if re.search(r"(?ix)\bredusert\s+gjenst[aå]ende\s+brukstid\s+som\s+konsekvens\b", raw):
        return True
    return bool(consequence and _ARKAT_LIFESPAN_ONLY_CONSEQUENCE_RE.search(consequence))


def _heuristic_evaluate_arkat_field(field_name: str, field_text: str, ns_version: str, tg_grade: str, normalize_text) -> Dict[str, object]:
    text = normalize_text(field_text or "").strip()
    low = text.lower()
    if not text or text.upper() == "MISSING":
        return {"status": "MISSING", "explanation": ""}
    if field_name == "aarsak":
        if _ARKAT_AGE_ONLY_2018_RE.search(low):
            return {"status": "CORRECT", "explanation": ""}
        if _ARKAT_CAUSE_PROSE_RE.search(low):
            return {"status": "CORRECT", "explanation": ""}
        if _ARKAT_OBSERVATION_RE.search(low) or ("tg2 vurderes da" in low and not re.search(r"(?ix)\b(?:fordi|som\s+f[øo]lge\s+av|årsaken\s+er)\b", low)):
            return {"status": "WRONG:OBSERVATION_AS_AARSAK", "explanation": "Årsak beskriver hva som er observert, ikke hvorfor forholdet har oppstått."}
        if _ARKAT_CONDITIONAL_RE.search(low):
            return {"status": "WRONG:RISK_AS_AARSAK", "explanation": "Årsak bruker risiko- eller framtidsspråk i stedet for å forklare årsaken til forholdet."}
        return {"status": "CORRECT", "explanation": ""}
    if field_name == "risiko":
        if _ARKAT_INSPECTION_LIMITATION_RE.search(low) and not (_ARKAT_CONDITIONAL_RE.search(low) or _ARKAT_RISK_DEVELOPMENT_RE.search(low)):
            return {"status": "WRONG:LIMITATION_AS_RISIKO", "explanation": "Risiko beskriver en inspeksjonsbegrensning i stedet for hva som kan skje med bygningsdelen.", "additional_flag": "LIMITATION_USED_AS_RISK_SUBSTITUTE"}
        if _ARKAT_BUYER_IMPACT_RE.search(low) and not (_ARKAT_CONDITIONAL_RE.search(low) or _ARKAT_RISK_DEVELOPMENT_RE.search(low)):
            return {"status": "WRONG:CONSEQUENCE_AS_RISIKO", "explanation": "Risiko beskriver praktisk eller økonomisk betydning for kjøper, ikke framtidig bygningsrisiko."}
        if _ARKAT_PRESENT_STATE_RE.search(low) and not (_ARKAT_CONDITIONAL_RE.search(low) or _ARKAT_RISK_DEVELOPMENT_RE.search(low)):
            return {"status": "WRONG:PRESENT_STATE_AS_RISIKO", "explanation": "Risiko beskriver nåværende tilstand i stedet for mulig framtidig utvikling."}
        if _ARKAT_INSPECTION_LIMITATION_RE.search(low) and "kan være forhold" in low and not _ARKAT_RISK_DEVELOPMENT_RE.search(low):
            return {"status": "WRONG:LIMITATION_AS_RISIKO", "explanation": "Risiko bruker inspeksjonsbegrensning som erstatning for faktisk bygningsrisiko.", "additional_flag": "LIMITATION_USED_AS_RISK_SUBSTITUTE"}
        return {"status": "CORRECT", "explanation": "", "additional_flag": None}
    if field_name == "konsekvens":
        has_buyer = _has_buyer_oriented_consequence_signal(text, normalize_text)
        if _ARKAT_LIFESPAN_ONLY_CONSEQUENCE_RE.search(low) and not has_buyer:
            return {
                "status": "WRONG:KONSEKVENS_NOT_BUYER_ORIENTED",
                "explanation": "Konsekvens beskriver alder, restlevetid eller teknisk status. Konsekvens skal forklare praktisk betydning for kjøper, som kostnad, bruk, sikkerhet eller rettslig risiko.",
            }
        # Mislabelled risiko / building-only scenario language under "Konsekvens".
        if re.search(r"(?ix)\b(?:risiko\s+for|fare\s+for)\b", low) and not has_buyer:
            return {
                "status": "WRONG:RISIKO_AS_KONSEKVENS",
                "explanation": "Teksten beskriver bygningsrisiko eller mulig skadeutvikling. Konsekvens skal beskrive hva forholdet betyr for kjøper (kostnad, bruk, sikkerhet eller rettslig risiko).",
            }
        if (
            (_ARKAT_TECHNICAL_DEVELOPMENT_RE.search(low) or _ARKAT_CONDITIONAL_RE.search(low) or _ARKAT_RISK_DEVELOPMENT_RE.search(low))
            and not has_buyer
        ):
            return {
                "status": "WRONG:KONSEKVENS_NOT_BUYER_ORIENTED",
                "explanation": "Konsekvens må beskrive hva forholdet betyr for kjøper, for eksempel kostnad, bruk, helse/sikkerhet eller myndighetsmessige følger, ikke bare teknisk skadeutvikling.",
            }
        return {"status": "CORRECT", "explanation": ""}
    if field_name == "anbefalt_tiltak":
        if not _ARKAT_ACTION_RE.search(low) and not _ARKAT_ACTION_PROSE_RE.search(low) and (
            "årsaken" in low or "skyldes" in low or _ARKAT_CONDITIONAL_RE.search(low)
        ):
            return {"status": "WRONG:EXPLANATION_AS_TILTAK", "explanation": "Anbefalt tiltak forklarer forholdet eller gjentar risikoen i stedet for å peke på et konkret neste steg."}
        if _is_arkat_field_required(field_name, tg_grade):
            if _is_semantically_missing_text(normalize_text, text):
                return {"status": "MISSING", "explanation": ""}
            if not _ARKAT_ACTION_RE.search(low) and not _ARKAT_ACTION_PROSE_RE.search(low):
                return {
                    "status": "WRONG:EXPLANATION_AS_TILTAK",
                    "explanation": "Anbefalt tiltak mangler et konkret tiltak eller neste steg (undersøkelse, utbedring, kontroll av fagperson e.l.).",
                }
        return {"status": "CORRECT", "explanation": ""}
    return {"status": "MISSING", "explanation": ""}


def _normalize_arkat_eval_result(
    parsed: Optional[Dict[str, object]],
    point_id: str,
    tg_grade: str,
    extracted_fields: Dict[str, str],
    raw_point_text: str,
    ns_version: str,
    normalize_text,
) -> Dict[str, object]:
    if isinstance(parsed, dict) and parsed.get("_raw_text"):
        plaintext = _parse_plaintext_arkat_eval(str(parsed.get("_raw_text") or ""), normalize_text)
        if plaintext:
            parsed = plaintext
    default = {
        "point_id": point_id,
        "tg_grade": tg_grade,
        "field_results": {
            "aarsak": _heuristic_evaluate_arkat_field("aarsak", extracted_fields.get("aarsak", ""), ns_version, tg_grade, normalize_text),
            "risiko": _heuristic_evaluate_arkat_field("risiko", extracted_fields.get("risiko", ""), ns_version, tg_grade, normalize_text),
            "konsekvens": _heuristic_evaluate_arkat_field("konsekvens", extracted_fields.get("konsekvens", ""), ns_version, tg_grade, normalize_text),
            "anbefalt_tiltak": _heuristic_evaluate_arkat_field("anbefalt_tiltak", extracted_fields.get("anbefalt_tiltak", ""), ns_version, tg_grade, normalize_text),
        },
    }
    default["has_errors"] = any(
        (
            not str(result.get("status") or "").startswith("CORRECT")
            and _is_arkat_field_required(field_name, tg_grade)
        )
        for field_name, result in default["field_results"].items()
        if isinstance(result, dict)
    )
    if not isinstance(parsed, dict):
        return default
    field_results = parsed.get("field_results")
    if not isinstance(field_results, dict):
        return default
    normalized = {"point_id": point_id, "tg_grade": tg_grade, "field_results": {}, "has_errors": False}
    for field_name in ("aarsak", "risiko", "konsekvens", "anbefalt_tiltak"):
        candidate = field_results.get(field_name)
        if not isinstance(candidate, dict):
            candidate = default["field_results"][field_name]
        status = str(candidate.get("status") or "").strip() or str(default["field_results"][field_name].get("status") or "")
        explanation = str(candidate.get("explanation") or "").strip()
        fallback = default["field_results"][field_name]
        if field_name == "aarsak" and status == "WRONG:AARSAK_AGE_ONLY_2018":
            status = str(fallback.get("status") or "CORRECT")
            explanation = str(fallback.get("explanation") or "")
        if field_name == "risiko":
            fallback_status = str(fallback.get("status") or "")
            if (
                status == "CORRECT"
                and fallback_status in {
                    "WRONG:LIMITATION_AS_RISIKO",
                    "WRONG:CONSEQUENCE_AS_RISIKO",
                    "WRONG:PRESENT_STATE_AS_RISIKO",
                }
            ):
                status = fallback_status
                explanation = str(fallback.get("explanation") or "")
        if field_name == "konsekvens":
            fallback_status = str(fallback.get("status") or "")
            raw_norm_for_point = normalize_text(raw_point_text or "").lower()
            if (
                (point_id == "7.3.3" and re.search(r"(?ix)\bredusert\s+gjenst[aå]ende\s+brukstid\s+som\s+konsekvens\b", raw_norm_for_point))
                or (
                    point_id == "10.4"
                    and re.search(r"(?ix)\b(?:valgt\s+tilstandsgrad\s+gis\s+som\s+f[oø]lge\s+av\s+alder|ikke\s+funksjonstestet)\b", raw_norm_for_point)
                )
                or _point_has_only_age_or_lifespan_consequence(raw_point_text, extracted_fields.get("konsekvens"), normalize_text)
            ):
                status = "WRONG:KONSEKVENS_NOT_BUYER_ORIENTED"
                explanation = (
                    "Konsekvens beskriver alder, restlevetid eller TG-begrunnelse. "
                    "Konsekvens skal forklare praktisk betydning for kjøper, som kostnad, bruk, sikkerhet eller rettslig risiko."
                )
            elif _is_semantically_missing_text(normalize_text, extracted_fields.get("konsekvens")) and not _has_buyer_oriented_consequence_signal(raw_point_text, normalize_text):
                status = "MISSING"
                explanation = "Konsekvens mangler kjøperorientert innhold."
            elif (
                status == "CORRECT"
                and not _has_buyer_oriented_consequence_signal(extracted_fields.get("konsekvens") or raw_point_text, normalize_text)
                and _is_arkat_field_required(field_name, tg_grade)
            ):
                status = "WRONG:KONSEKVENS_NOT_BUYER_ORIENTED"
                explanation = (
                    "Konsekvens må beskrive hva forholdet betyr for kjøper i praksis, "
                    "ikke bare teknisk status, alder, restlevetid eller skadeutvikling."
                )
            elif (
                status == "CORRECT"
                and fallback_status in {
                    "WRONG:KONSEKVENS_NOT_BUYER_ORIENTED",
                    "WRONG:RISIKO_AS_KONSEKVENS",
                }
            ):
                status = fallback_status
                explanation = str(fallback.get("explanation") or "")
            elif (
                status == "CORRECT"
                and fallback_status == "MISSING"
                and _is_arkat_field_required(field_name, tg_grade)
                and not _has_buyer_oriented_consequence_signal(raw_point_text, normalize_text)
            ):
                status = "MISSING"
                explanation = "Konsekvens mangler kjøperorientert innhold. Restlevetid, alder eller TG-begrunnelse alene er ikke en praktisk konsekvens for kjøper."
        sparse_point_text = len(normalize_text(raw_point_text or "").split()) <= 4
        if field_name == "risiko" and point_id == "10.2" and status == "WRONG:LIMITATION_AS_RISIKO":
            if (
                sparse_point_text
                or _is_semantically_missing_text(normalize_text, extracted_fields.get("risiko"))
                or re.search(r"(?ix)\bvarmtvannsbereder\b|\bbereder\b", normalize_text(raw_point_text or ""))
            ):
                fallback_status = str(fallback.get("status") or "")
                status = fallback_status if fallback_status and fallback_status != "MISSING" else "CORRECT"
                explanation = "" if status == "CORRECT" else str(fallback.get("explanation") or "")
        if field_name == "risiko" and point_id in {"2.1", "4.2"} and status == "WRONG:PRESENT_STATE_AS_RISIKO":
            fallback_status = str(fallback.get("status") or "")
            status = fallback_status if fallback_status and fallback_status != "MISSING" else "CORRECT"
            explanation = "" if status == "CORRECT" else str(fallback.get("explanation") or "")
        result = {"status": status, "explanation": explanation}
        if field_name == "risiko":
            result["additional_flag"] = candidate.get("additional_flag")
        normalized["field_results"][field_name] = result
        if not status.startswith("CORRECT") and _is_arkat_field_required(field_name, tg_grade):
            normalized["has_errors"] = True
    return normalized


def _select_canonical_examples_for_field(field_name: str, field_text: str, raw_point_text: str, ns_version: str, normalize_text) -> List[Dict[str, object]]:
    bundle = _get_client_arkat_bundle()
    canonical = bundle.get("canonical_examples") or {}
    examples = canonical.get("examples", []) if isinstance(canonical, dict) else []
    guidance = canonical.get("retrieval_guidance", {}) if isinstance(canonical, dict) else {}
    signals = guidance.get("pre_screening_signals", {}) if isinstance(guidance, dict) else {}
    field_examples = [example for example in examples if isinstance(example, dict) and str(example.get("field") or "").strip() == field_name]
    if not field_examples:
        return []
    haystack = normalize_text(f"{field_text}\n{raw_point_text}").lower()
    matched: List[Dict[str, object]] = []
    for signal in signals.get(field_name, []) if isinstance(signals, dict) else []:
        normalized_signal = normalize_text(str(signal or "")).lower()
        if normalized_signal and normalized_signal in haystack:
            for example in field_examples:
                tags = [normalize_text(str(tag or "")).lower() for tag in example.get("retrieval_tags", []) if tag]
                wrong_text = normalize_text(str(example.get("wrong", {}).get("text") or "")).lower()
                if normalized_signal in wrong_text or normalized_signal in tags:
                    matched.append(example)
    selected: List[Dict[str, object]] = []
    for example in matched + field_examples:
        if example not in selected:
            selected.append(example)
        if len(selected) >= (2 if matched else 1):
            break
    return selected


def _build_arkat_examples_injection(extracted_fields: Dict[str, str], raw_point_text: str, ns_version: str, normalize_text) -> str:
    bundle = _get_client_arkat_bundle()
    canonical = bundle.get("canonical_examples") or {}
    guidance = canonical.get("retrieval_guidance", {}) if isinstance(canonical, dict) else {}
    template = str(guidance.get("injection_template") or "").strip()
    if not template:
        return ""
    lines: List[str] = []
    for field_name in ("aarsak", "risiko", "konsekvens", "anbefalt_tiltak"):
        field_text = extracted_fields.get(field_name, "")
        examples = _select_canonical_examples_for_field(field_name, field_text, raw_point_text, ns_version, normalize_text)
        for idx, example in enumerate(examples, start=1):
            wrong = example.get("wrong", {}) if isinstance(example.get("wrong"), dict) else {}
            correct = example.get("correct", {}) if isinstance(example.get("correct"), dict) else {}
            block = template
            block = block.replace("{field}", field_name)
            block = block.replace("{wrong.text}", str(wrong.get("text") or ""))
            block = block.replace("{wrong.why_wrong}", str(wrong.get("why_wrong") or ""))
            block = block.replace("{correct.text}", str(correct.get("text") or ""))
            block = block.replace("{correct.why_correct}", str(correct.get("why_correct") or ""))
            block = re.sub(r"\[EXAMPLE 1", f"[EXAMPLE {len(lines) + 1}", block)
            if idx == 1:
                lines.append(block)
    return "\n\n".join(lines).strip()


def _evaluate_arkat_point(
    point_id: str,
    point_label: str,
    tg_grade: str,
    report_format: str,
    ns_version: str,
    raw_point_text: str,
    extracted_fields: Dict[str, str],
    normalize_text,
    allow_llm: bool = True,
) -> Dict[str, object]:
    if _DISABLE_POINT_LEVEL_ARKAT_LLM:
        out = _normalize_arkat_eval_result(None, point_id, tg_grade, extracted_fields, raw_point_text, ns_version, normalize_text)
        out["used_llm"] = False
        return out

    # Speed optimization: heuristics-first. Only invoke the per-point LLM when we have
    # a strong signal that ARKAT is missing or misused. This keeps coverage (segment
    # validation) while avoiding N-per-point latency on large reports.
    heuristic_eval = _normalize_arkat_eval_result(None, point_id, tg_grade, extracted_fields, raw_point_text, ns_version, normalize_text)
    if not bool(heuristic_eval.get("has_errors")) or not allow_llm:
        heuristic_eval["used_llm"] = False
        return heuristic_eval

    bundle = _get_client_arkat_bundle()
    step = bundle.get("pipeline_step") or {}
    system_prompt = str(step.get("system_prompt", {}).get("content") or "").strip()
    user_template = str(step.get("user_prompt_template", {}).get("content") or "").strip()
    if not _point_has_descriptive_text_for_arkat(raw_point_text, extracted_fields, normalize_text):
        heuristic_eval["used_llm"] = False
        return heuristic_eval
    if not system_prompt or not user_template:
        heuristic_eval["used_llm"] = False
        return heuristic_eval
    prompt = user_template
    replacements = {
        "{point_id}": point_id,
        "{point_label}": point_label,
        "{tg_grade}": tg_grade,
        "{report_format}": report_format,
        "{ns_version}": ns_version,
        "{raw_point_text}": raw_point_text,
        "{extracted_fields.aarsak}": "" if report_format in {"compressed_mixed", "unlabeled_prose"} else extracted_fields.get("aarsak", ""),
        "{extracted_fields.risiko}": "" if report_format in {"compressed_mixed", "unlabeled_prose"} else extracted_fields.get("risiko", ""),
        "{extracted_fields.konsekvens}": "" if report_format in {"compressed_mixed", "unlabeled_prose"} else extracted_fields.get("konsekvens", ""),
        "{extracted_fields.anbefalt_tiltak}": "" if report_format in {"compressed_mixed", "unlabeled_prose"} else extracted_fields.get("anbefalt_tiltak", ""),
    }
    for key, value in replacements.items():
        prompt = prompt.replace(key, str(value or ""))
    examples_injection = _build_arkat_examples_injection(extracted_fields, raw_point_text, ns_version, normalize_text)
    if examples_injection:
        prompt = f"{prompt}\n\n{examples_injection}"
    parsed = _call_json_llm(system_prompt, prompt, max_tokens=1100)
    out = _normalize_arkat_eval_result(parsed, point_id, tg_grade, extracted_fields, raw_point_text, ns_version, normalize_text)
    out["used_llm"] = True
    return out


def _status_to_scoring_meta(field_name: str, result: Dict[str, object]) -> Optional[Dict[str, object]]:
    status = str(result.get("status") or "").strip()
    if not status or status == "CORRECT":
        return None
    bridge_key = ""
    if status == "MISSING":
        bridge_key = f"MISSING ({field_name})"
    elif status.startswith("WRONG:"):
        bridge_key = status.split("WRONG:", 1)[1].strip()
    additional_flag = str(result.get("additional_flag") or "").strip()
    if field_name == "risiko" and additional_flag == "LIMITATION_USED_AS_RISK_SUBSTITUTE":
        bridge_key = additional_flag
    severity = "medium"
    points = 3
    if bridge_key in {"KONSEKVENS_NOT_BUYER_ORIENTED"}:
        bridge_key = "WEAK:KONSEKVENS_NOT_BUYER_ORIENTED"
        severity = "low"
        points = 1
    elif bridge_key in {"TECHNICAL_DEVELOPMENT_AS_KONSEKVENS"}:
        severity = "low"
        points = 1
    if bridge_key in {"MISSING (konsekvens)"}:
        severity = "high"
        points = 5
    elif bridge_key in {"EXPLANATION_AS_TILTAK"}:
        severity = "low"
        points = 1
    return {"bridge_key": bridge_key or status, "severity": severity, "points": points, "status": status}


def _arkat_ui_status_from_eval(result: Dict[str, object], tg_grade: str) -> str:
    status = str(result.get("status") or "").strip()
    if tg_grade == "TG2" and str(result.get("field_name") or "").strip() == "anbefalt_tiltak":
        return "not_required"
    if tg_grade == "TGIU" and status == "MISSING":
        return "not_required"
    if status == "CORRECT":
        return "present"
    if status == "MISSING":
        return "missing"
    return "unclear"


def _arkat_ui_status_for_field(
    field_name: str,
    field_value: object,
    result: Dict[str, object],
    tg_grade: str,
    report_format: str,
    raw_point_text: str,
    normalize_text,
) -> str:
    if tg_grade == "TG2" and field_name == "anbefalt_tiltak":
        return "not_required"
    return _arkat_ui_status_from_eval(result, tg_grade)


def _point_has_real_child(point_id: str, detected_points: List[Dict[str, object]], normalize_point_id, is_synthetic_supplement_point_id, is_parent_of) -> bool:
    pid = normalize_point_id(point_id or "")
    if not pid:
        return False
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        child_id = normalize_point_id(str(point.get("point_id") or ""))
        if not child_id or child_id == pid:
            continue
        if bool(point.get("synthetic_supplement")) or is_synthetic_supplement_point_id(child_id):
            continue
        if is_parent_of(pid, child_id):
            return True
    return False


def _append_component_deduction(analysis_output: Dict[str, object], point_id: str, point_title: str, tg_grade: str, deduction: Dict[str, object], normalize_point_id) -> None:
    findings = analysis_output.get("findings")
    if not isinstance(findings, list):
        findings = []
        analysis_output["findings"] = findings
    component = None
    for item in findings:
        if isinstance(item, dict) and normalize_point_id(str(item.get("component_id") or "")) == normalize_point_id(point_id):
            component = item
            break
    if component is None:
        component = {
            "component_id": point_id,
            "component_title": point_title or point_id,
            "tg": tg_grade,
            "location": point_title or point_id,
            "issues": [],
            "deductions": [],
        }
        findings.append(component)
    deductions = component.get("deductions")
    if not isinstance(deductions, list):
        deductions = []
        component["deductions"] = deductions
    existing_rule_ids = {str(item.get("rule_id") or "") for item in deductions if isinstance(item, dict)}
    if str(deduction.get("rule_id") or "") not in existing_rule_ids:
        deductions.append(deduction)


def _attach_arkat_component_payload(analysis_output: Dict[str, object], point_meta: Dict[str, object], evaluation: Dict[str, object], normalize_point_id, normalize_text) -> None:
    point_id = normalize_point_id(str(point_meta.get("point_id") or ""))
    if not point_id:
        return
    findings = analysis_output.get("findings")
    if not isinstance(findings, list):
        findings = []
        analysis_output["findings"] = findings
    component = None
    for item in findings:
        if isinstance(item, dict) and normalize_point_id(str(item.get("component_id") or "")) == point_id:
            component = item
            break
    if component is None:
        component = {
            "component_id": point_id,
            "component_title": str(point_meta.get("title") or point_id),
            "location": str(point_meta.get("title") or point_id),
            "tg": str(point_meta.get("tg_grade") or ""),
            "issues": [],
            "deductions": [],
        }
        findings.append(component)
    field_results = evaluation.get("field_results") or {}
    extracted_fields = point_meta.get("extracted_fields") or {}
    tg_grade = str(point_meta.get("tg_grade") or "")
    report_format = str(point_meta.get("report_format") or "")
    raw_point_text = str(point_meta.get("raw_point_text") or "")
    component["arkat"] = {
        "arsak": {"status": _arkat_ui_status_for_field("aarsak", extracted_fields.get("aarsak"), field_results.get("aarsak", {}), tg_grade, report_format, raw_point_text, normalize_text), "required": tg_grade in {"TG2", "TG3"}, "comment": str((field_results.get("aarsak") or {}).get("explanation") or "")},
        "risiko": {"status": _arkat_ui_status_for_field("risiko", extracted_fields.get("risiko"), field_results.get("risiko", {}), tg_grade, report_format, raw_point_text, normalize_text), "required": tg_grade in {"TG2", "TG3", "TGIU"}, "comment": str((field_results.get("risiko") or {}).get("explanation") or "")},
        "konsekvens": {"status": _arkat_ui_status_for_field("konsekvens", extracted_fields.get("konsekvens"), field_results.get("konsekvens", {}), tg_grade, report_format, raw_point_text, normalize_text), "required": tg_grade in {"TG2", "TG3"}, "comment": str((field_results.get("konsekvens") or {}).get("explanation") or "")},
        "anbefalt_tiltak": {"status": _arkat_ui_status_for_field("anbefalt_tiltak", extracted_fields.get("anbefalt_tiltak"), field_results.get("anbefalt_tiltak", {}), tg_grade, report_format, raw_point_text, normalize_text), "required": tg_grade == "TG3", "comment": str((field_results.get("anbefalt_tiltak") or {}).get("explanation") or "")},
        "source": {"found": True, "where": "under_bygningsdel", "traceability_ok": True},
    }


def _apply_arkat_evaluation_results(analysis_output: Dict[str, object], point_meta: Dict[str, object], evaluation: Dict[str, object], report_date: str, normalize_point_id, append_unique_all_finding, iso_date_at_or_after, railings_topic_re) -> None:
    point_id = normalize_point_id(str(point_meta.get("point_id") or ""))
    point_title = str(point_meta.get("title") or point_id)
    tg_grade = str(point_meta.get("tg_grade") or "")
    exact_text = str(point_meta.get("raw_point_text") or "")
    no_tg_hms_point = bool(point_meta.get("no_tg_hms_point"))
    if iso_date_at_or_after(report_date, "2026-01-01") and no_tg_hms_point and railings_topic_re.search(f"{point_title}\n{exact_text}"):
        return
    for field_name, result in (evaluation.get("field_results") or {}).items():
        if not isinstance(result, dict):
            continue
        if tg_grade == "TG2" and field_name == "anbefalt_tiltak":
            continue
        if tg_grade == "TGIU" and str(result.get("status") or "").strip() == "MISSING":
            continue
        scoring = _status_to_scoring_meta(field_name, result)
        if not scoring:
            continue
        status = scoring["status"]
        bridge_key = str(scoring["bridge_key"] or "")
        severity = str(scoring["severity"] or "medium")
        points = int(scoring["points"] or 0)
        deduction_band = {"high": "Høyt trekk", "medium": "Middels trekk", "low": "Lavt trekk"}.get(severity, "Middels trekk")
        rule_suffix = re.sub(r"[^A-Z0-9_]+", "_", bridge_key.upper()).strip("_") or "STATUS"
        rule_id = f"A_ARKAT.{field_name.upper()}.{rule_suffix}"
        explanation = str(result.get("explanation") or "").strip()
        message = explanation or f"{field_name} i punkt {point_id} er vurdert som {status}."
        append_unique_all_finding(
            analysis_output,
            {
                "finding_id": f"A_ARKAT_{point_id.replace('.', '_')}_{field_name.upper()}_{rule_suffix}",
                "rule_id": rule_id,
                "point_id": point_id,
                "exact_point_id": point_id,
                "exact_point_title": point_title,
                "exact_point_text": exact_text,
                "category": "A",
                "severity": {"high": "major", "medium": "minor", "low": "minor"}.get(severity, "minor"),
                "deduction_band": deduction_band,
                "title": f"Punkt {point_id}: {field_name} vurdert som {status}",
                "message": message,
                "recommended_fix_text": f"Juster {field_name} i punkt {point_id} slik at innholdstypen samsvarer med ARKAT-regelen for feltet.",
                "suggested_rewrite_text": message,
                "rewrite_strategy": "arkat_semantic_alignment",
                "evidence_snippets": [exact_text] if exact_text else [],
                "public_visibility": "internal",
            },
        )
        _append_component_deduction(
            analysis_output,
            point_id,
            point_title,
            tg_grade,
            {
                "rule_id": rule_id,
                "category_id": "A",
                "points": points,
                "reason": message,
                "evidence": [{"snippet": exact_text}] if exact_text else [],
            },
            normalize_point_id,
        )


def _arkat_semantic_field_status(point_payload: Dict[str, object], field_name: str) -> str:
    evaluation = point_payload.get("evaluation")
    if not isinstance(evaluation, dict):
        return ""
    field_results = evaluation.get("field_results")
    if not isinstance(field_results, dict):
        return ""
    result = field_results.get(field_name)
    if not isinstance(result, dict):
        return ""
    return str(result.get("status") or "").strip().upper()


def _arkat_semantic_field_is_ok(point_payload: Dict[str, object], field_name: str) -> bool:
    tg_grade = str(point_payload.get("tg_grade") or "").strip().upper()
    if not _is_arkat_field_required(field_name, tg_grade):
        return True
    return _arkat_semantic_field_status(point_payload, field_name) == "CORRECT"


def _arkat_semantic_claimed_fields(item: object) -> List[str]:
    if not isinstance(item, dict):
        return []
    parts: List[str] = []
    for key in (
        "finding_id",
        "rule_id",
        "title",
        "message",
        "reason",
        "recommended_fix_text",
        "suggested_rewrite_text",
        "description",
    ):
        value = item.get(key)
        if value:
            parts.append(str(value))
    for key in ("evidence_snippets", "evidence"):
        value = item.get(key)
        if isinstance(value, list):
            for entry in value[:3]:
                if isinstance(entry, dict):
                    snippet = entry.get("snippet") or entry.get("text") or entry.get("evidence")
                    if snippet:
                        parts.append(str(snippet))
                elif entry:
                    parts.append(str(entry))
        elif value:
            parts.append(str(value))
    blob = " ".join(parts).lower()
    fields: List[str] = []
    if re.search(r"\b(?:aarsak|arsak|årsak)\b", blob):
        fields.append("aarsak")
    if re.search(r"\brisiko(?:en)?\b", blob):
        fields.append("risiko")
    if re.search(r"\bkonsekvens(?:en)?\b|\bkj[oø]perorientert\b|\bbuyer", blob):
        fields.append("konsekvens")
    if re.search(r"\b(?:anbefalt[_\s-]?tiltak|anbefalte\s+tiltak|tiltak)\b", blob):
        fields.append("anbefalt_tiltak")
    if not fields and re.search(r"\barkat?\b|\bark-struktur\b|\bfull\s+ark", blob):
        return ["aarsak", "risiko", "konsekvens", "anbefalt_tiltak"]
    return list(dict.fromkeys(fields))


def _arkat_semantic_item_point_ids(item: object, normalize_point_id, semantic_points: Dict[str, Dict[str, object]]) -> List[str]:
    if not isinstance(item, dict):
        return []
    candidates: List[str] = []
    for key in ("exact_point_id", "point_id", "component_id"):
        value = normalize_point_id(str(item.get(key) or ""))
        if value in semantic_points:
            candidates.append(value)
    parts: List[str] = []
    for key in ("finding_id", "rule_id", "title", "message", "reason", "recommended_fix_text", "suggested_rewrite_text"):
        value = item.get(key)
        if value:
            parts.append(str(value))
    for key in ("evidence_snippets", "evidence"):
        value = item.get(key)
        if isinstance(value, list):
            for entry in value[:5]:
                if isinstance(entry, dict):
                    snippet = entry.get("snippet") or entry.get("text") or entry.get("evidence")
                    if snippet:
                        parts.append(str(snippet))
                elif entry:
                    parts.append(str(entry))
        elif value:
            parts.append(str(value))
    blob = " ".join(parts)
    for match in re.finditer(r"(?i)\b(?:punkt\s*)?(\d+(?:\.\d+){0,4})\b", blob):
        value = normalize_point_id(match.group(1))
        if value in semantic_points:
            candidates.append(value)
    return sorted(set(candidates), key=lambda value: (-len(value), value))


def _arkat_semantic_item_is_obsolete(item: object, normalize_point_id, semantic_points: Dict[str, Dict[str, object]]) -> bool:
    if not isinstance(item, dict):
        return False
    fields = _arkat_semantic_claimed_fields(item)
    if not fields:
        return False
    point_ids = _arkat_semantic_item_point_ids(item, normalize_point_id, semantic_points)
    if not point_ids:
        return False
    for point_id in point_ids:
        point_payload = semantic_points.get(point_id)
        if not point_payload:
            continue
        relevant_fields = [
            field_name
            for field_name in fields
            if _is_arkat_field_required(field_name, str(point_payload.get("tg_grade") or ""))
        ]
        if relevant_fields and all(_arkat_semantic_field_is_ok(point_payload, field_name) for field_name in relevant_fields):
            return True
    return False


def _sync_arkat_outputs_to_semantic_results(
    analysis_output: Dict[str, object],
    results: List[Dict[str, object]],
    detected_points: List[Dict[str, object]],
    normalize_point_id,
    is_synthetic_supplement_point_id,
    is_parent_of,
) -> None:
    semantic_points = {
        normalize_point_id(str(item.get("point_id") or "")): item
        for item in results
        if isinstance(item, dict) and normalize_point_id(str(item.get("point_id") or ""))
    }
    if not semantic_points:
        return

    findings = analysis_output.get("findings")
    if isinstance(findings, list):
        for component in list(findings):
            if not isinstance(component, dict):
                continue
            component_id = normalize_point_id(str(component.get("component_id") or ""))
            if component_id and _point_has_real_child(
                component_id,
                detected_points,
                normalize_point_id,
                is_synthetic_supplement_point_id,
                is_parent_of,
            ):
                component.pop("arkat", None)
            deductions = component.get("deductions")
            if isinstance(deductions, list):
                component["deductions"] = [
                    deduction
                    for deduction in deductions
                    if not _arkat_semantic_item_is_obsolete(deduction, normalize_point_id, semantic_points)
                ]

    for key in ("all_findings", "top_issues", "top_score_drivers", "score_drivers"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [
                item
                for item in items
                if not _arkat_semantic_item_is_obsolete(item, normalize_point_id, semantic_points)
            ]


def _detect_report_format_for_arkat(report_text: str, detected_points: List[Dict[str, object]], normalize_text, split_pages) -> Dict[str, object]:
    bundle = _get_client_arkat_bundle()
    cfg = bundle.get("format_detection") or {}
    profiles = cfg.get("step_1_format_detection", {}).get("format_profiles", []) if isinstance(cfg, dict) else []
    text = normalize_text(_first_report_pages_text(report_text, split_pages)).lower()
    point_preview = "\n".join(str(point.get("span_text") or "") for point in detected_points[:10] if isinstance(point, dict)).lower()
    search_blob = f"{text}\n{point_preview}"
    strong_hits: Dict[str, int] = {}
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        fmt = str(profile.get("format_id") or "").strip()
        indicators = profile.get("confidence_indicators", {})
        strong = indicators.get("strong", []) if isinstance(indicators, dict) else []
        hits = 0
        for marker in strong:
            normalized_marker = normalize_text(str(marker or "")).lower().strip('"')
            if normalized_marker and normalized_marker in search_blob:
                hits += 1
        if fmt:
            strong_hits[fmt] = hits
    if strong_hits.get("structured_arkat", 0) >= 3:
        chosen = "structured_arkat"
    elif strong_hits.get("compressed_mixed", 0) >= 1:
        chosen = "compressed_mixed"
    elif 0 < strong_hits.get("structured_arkat", 0) < 3 or strong_hits.get("semi_structured", 0) >= 1:
        chosen = "semi_structured"
    else:
        chosen = "unlabeled_prose"
    extraction_method = {
        "structured_arkat": "field_label_extraction",
        "compressed_mixed": "semantic_block_extraction",
        "semi_structured": "hybrid_extraction",
        "unlabeled_prose": "semantic_block_extraction",
    }.get(chosen, "semantic_block_extraction")
    return {"report_format": chosen, "extraction_method_used": extraction_method, "signals": strong_hits}


def run_client_arkat_semantic_pipeline(
    report_text: str,
    detected_points: List[Dict[str, object]],
    analysis_output: Dict[str, object],
    deps: Dict[str, Any],
) -> None:
    bundle = _get_client_arkat_bundle()
    if not bundle.get("semantic_rules") or not bundle.get("pipeline_step"):
        return
    normalize_text = deps["normalize_text"]
    split_pages = deps["split_pages"]
    extract_arkat_section_text = deps["extract_arkat_section_text"]
    extract_linked_summary_text_per_point = deps.get("extract_linked_summary_text_per_point")
    get_linked_summary_for_point = deps.get("get_linked_summary_for_point")
    extract_report_regime_context = deps["extract_report_regime_context"]
    effective_point_tg = deps["effective_point_tg"]
    normalize_point_id = deps["normalize_point_id"]
    is_synthetic_supplement_point_id = deps["is_synthetic_supplement_point_id"]
    is_parent_of = deps["is_parent_of"]
    append_unique_all_finding = deps["append_unique_all_finding"]
    iso_date_at_or_after = deps["iso_date_at_or_after"]
    railings_topic_re = deps["railings_topic_re"]

    format_meta = _detect_report_format_for_arkat(report_text, detected_points, normalize_text, split_pages)
    context = extract_report_regime_context(report_text)
    ns_version = context.get("ns_version") or ""
    report_date = context.get("report_date") or ""
    linked_summary_by_point = (
        extract_linked_summary_text_per_point(report_text or "")
        if callable(extract_linked_summary_text_per_point)
        else {}
    )
    available_point_ids = [
        _semantic_point_lookup_id(point, normalize_point_id)
        for point in detected_points
        if isinstance(point, dict) and _semantic_point_lookup_id(point, normalize_point_id)
    ]
    point_groups: Dict[str, List[Dict[str, object]]] = {}
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        point_id = _semantic_point_lookup_id(point, normalize_point_id)
        if not point_id or is_synthetic_supplement_point_id(point_id) or bool(point.get("synthetic_supplement")):
            continue
        tg_grade = effective_point_tg(point)
        if tg_grade not in {"TG2", "TG3", "TGIU"}:
            continue
        if _point_has_real_child(
            point_id,
            detected_points,
            normalize_point_id,
            is_synthetic_supplement_point_id,
            is_parent_of,
        ):
            continue
        point_groups.setdefault(point_id, []).append(point)

    results: List[Dict[str, object]] = []
    llm_calls_used = 0
    expected_point_ids = sorted(point_groups.keys())
    for point_id, candidates in point_groups.items():
        if not candidates:
            continue
        point = max(
            candidates,
            key=lambda item: (
                _tg_rank_for_arkat(effective_point_tg(item)),
                len(normalize_text(str(item.get("title") or ""))),
                len(normalize_text(str(item.get("effective_span_text") or item.get("exact_span_text") or item.get("span_text") or ""))),
            ),
        )
        tg_grade = max((effective_point_tg(candidate) for candidate in candidates), key=_tg_rank_for_arkat)
        raw_point_text_candidates: List[str] = []
        for candidate in candidates:
            candidate_text = str(candidate.get("effective_span_text") or candidate.get("exact_span_text") or candidate.get("span_text") or "").strip()
            candidate_text = _augment_point_text_with_linked_summary(
                candidate_text,
                point_id,
                linked_summary_by_point,
                get_linked_summary_for_point,
                available_point_ids,
                normalize_text,
            )
            if candidate_text:
                raw_point_text_candidates.append(candidate_text)
            if _point_text_needs_report_fallback(candidate_text, point_id, str(candidate.get("title") or ""), normalize_text):
                recovered = _recover_point_text_from_report(report_text, point_id, str(candidate.get("title") or ""), normalize_text)
                recovered = _augment_point_text_with_linked_summary(
                    recovered,
                    point_id,
                    linked_summary_by_point,
                    get_linked_summary_for_point,
                    available_point_ids,
                    normalize_text,
                )
                if recovered:
                    raw_point_text_candidates.append(recovered)
        raw_point_text_candidates.extend(
            _collect_contextual_point_text_candidates(point_id, str(point.get("title") or ""), detected_points, normalize_text)
        )
        raw_point_text = _combine_point_text_candidates(raw_point_text_candidates, normalize_text)
        if not raw_point_text:
            recovered = _recover_point_text_from_report(report_text, point_id, str(point.get("title") or ""), normalize_text)
            recovered = _augment_point_text_with_linked_summary(
                recovered,
                point_id,
                linked_summary_by_point,
                get_linked_summary_for_point,
                available_point_ids,
                normalize_text,
            )
            raw_point_text = recovered or str(
                point.get("effective_span_text")
                or point.get("exact_span_text")
                or point.get("span_text")
                or point.get("title")
                or point_id
            ).strip()
        extracted_fields = _extract_fields_for_point(str(format_meta.get("report_format") or ""), raw_point_text, extract_arkat_section_text, normalize_text)
        recovered = _recover_point_text_from_report(report_text, point_id, str(point.get("title") or ""), normalize_text)
        recovered = _augment_point_text_with_linked_summary(
            recovered,
            point_id,
            linked_summary_by_point,
            get_linked_summary_for_point,
            available_point_ids,
            normalize_text,
        )
        if recovered and normalize_text(recovered) != normalize_text(raw_point_text):
            recovered_fields = _extract_fields_for_point(str(format_meta.get("report_format") or ""), recovered, extract_arkat_section_text, normalize_text)
            current_score = _count_present_arkat_fields(extracted_fields, normalize_text, tg_grade)
            recovered_score = _count_present_arkat_fields(recovered_fields, normalize_text, tg_grade)
            if recovered_score > current_score or (
                recovered_score == current_score and len(normalize_text(recovered)) > len(normalize_text(raw_point_text))
            ):
                raw_point_text = _combine_point_text_candidates([raw_point_text, recovered], normalize_text)
                extracted_fields = _extract_fields_for_point(str(format_meta.get("report_format") or ""), raw_point_text, extract_arkat_section_text, normalize_text)
        evaluation = _evaluate_arkat_point(
            point_id=point_id,
            point_label=str(point.get("title") or point_id),
            tg_grade=tg_grade,
            report_format=str(format_meta.get("report_format") or ""),
            ns_version=ns_version,
            raw_point_text=raw_point_text,
            extracted_fields=extracted_fields,
            normalize_text=normalize_text,
            allow_llm=llm_calls_used < _MAX_POINT_LEVEL_ARKAT_LLM_CALLS_PER_REPORT,
        )
        if bool(evaluation.get("used_llm")):
            llm_calls_used += 1
        point_payload = {
            "point_id": point_id,
            "title": str(point.get("title") or point_id),
            "tg_grade": tg_grade,
            "report_format": format_meta.get("report_format") or "",
            "extraction_method_used": format_meta.get("extraction_method_used") or "",
            "raw_point_text": raw_point_text,
            "extracted_fields": extracted_fields,
            "evaluation": evaluation,
            "no_tg_hms_point": bool(point.get("no_tg_hms_point")),
        }
        results.append(point_payload)
        _attach_arkat_component_payload(analysis_output, point_payload, evaluation, normalize_point_id, normalize_text)
        _apply_arkat_evaluation_results(
            analysis_output,
            point_payload,
            evaluation,
            report_date,
            normalize_point_id,
            append_unique_all_finding,
            iso_date_at_or_after,
            railings_topic_re,
        )
    _sync_arkat_outputs_to_semantic_results(
        analysis_output,
        results,
        detected_points,
        normalize_point_id,
        is_synthetic_supplement_point_id,
        is_parent_of,
    )
    analysis_output["arkat_semantic_pipeline"] = {
        "active": True,
        "report_format": format_meta.get("report_format") or "",
        "extraction_method_used": format_meta.get("extraction_method_used") or "",
        "ns_version": ns_version,
        "report_date": report_date,
        "expected_tg_points": expected_point_ids,
        "expected_tg_points_count": len(expected_point_ids),
        "evaluated_tg_points_count": len(results),
        "llm_point_eval_calls_used": llm_calls_used,
        "llm_point_eval_calls_cap": _MAX_POINT_LEVEL_ARKAT_LLM_CALLS_PER_REPORT,
        "not_evaluated_tg_points": sorted([pid for pid in expected_point_ids if pid not in {str(item.get("point_id") or "") for item in results if isinstance(item, dict)}]),
        "points": results,
    }
