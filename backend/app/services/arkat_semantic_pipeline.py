from functools import lru_cache
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

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
_MAX_POINT_LEVEL_ARKAT_LLM_CALLS_PER_REPORT = 999999

_DOMMER_B_ALLOWED_ERROR_TYPES = {
    "MISSING (konsekvens)",
    "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS",
    "PURE_DUPLICATION",
    "MISSING (aarsak)",
    "OBSERVATION_AS_AARSAK",
    "RISK_AS_AARSAK",
    "MISSING (risiko)",
    "CONSEQUENCE_AS_RISIKO",
    "PRESENT_STATE_AS_RISIKO",
    "LIMITATION_AS_RISIKO",
    "LIMITATION_USED_AS_RISK_SUBSTITUTE",
    "AARSAK_AS_RISIKO",
    "MISSING (anbefalt_tiltak)",
    "EXPLANATION_AS_TILTAK",
    "CONSEQUENCE_AS_TILTAK",
    "TILTAK_IMPERATIVE_FORM",
    "TILTAK_VAGUE_WITHOUT_NECESSITY",
    "TGIU_MISSING_REASON",
    "TGIU_MISSING_FURTHER_INVESTIGATION",
    "TGIU_MISSING_MOISTURE_FLAG",
    "TGIU_CRAWLSPACE_MISSING_RISK_CONSEQUENCE",
}

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
    r"vurderes|planlegges)|kan\s+v[æa]re\s+[aå]\s+(?:legge|montere|installere|utbedre|skifte)|planlegg|bestill|lokal\s+utbedring|utf[oø]res\s+av\s+fagperson)\b"
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
    r"det\s+anbefales(?:\s+[aå])?|anbefaler(?:\s+(?:at|[aå]))?|b[oø]r\s+(?:utf[oø]res|skiftes|utbedres|kontrolleres|unders[oø]kes|vurderes|planlegges)|"
    r"kan\s+v[æa]re\s+[aå]\s+(?:legge|montere|installere|utbedre|skifte))\b"
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
    except Exception as exc:
        logger.warning(
            "Dommer B LLM call failed; falling back to heuristic path (%s: %s)",
            exc.__class__.__name__,
            str(exc),
        )
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
            or (
                not is_tg_rationale
                and re.search(
                    r"(?ix)\b(?:manglende|utilstrekkelig|ufagmessig|sprekker?|riss|råte|motfall|lekkasje|bom\s*\(hulrom\)|saltutslag|fuktskade)\b",
                    low,
                )
            )
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


def _point_id_exact_token_re(point_id: str) -> re.Pattern:
    """
    Match an exact structured point id token without allowing dotted-substring hits.
    Example: point_id=1.1 must NOT match inside 9.1.1.
    """
    pid = str(point_id or "").strip()
    if not pid:
        return re.compile(r"$^")
    return re.compile(rf"(?i)(?<![\d.]){re.escape(pid)}(?![\d.])")


def _trim_text_to_point_window(text: str, point_id: str, normalize_text) -> str:
    """
    Trim candidate text to the target point window when compact tables inline multiple points.
    Keep this conservative: only cut on header-like boundaries (line/pipe/TG context).
    """
    raw = str(text or "").strip()
    pid = str(point_id or "").strip()
    if not raw or not _looks_like_structured_point_id(pid):
        return raw
    target_re = _point_id_exact_token_re(pid)
    target_matches = list(target_re.finditer(raw))
    if not target_matches:
        return raw

    def _is_boundary_like(index: int) -> bool:
        prefix = raw[max(0, index - 12):index]
        return (
            "|" in prefix
            or bool(re.search(r"(?i)tg\s*(?:iu|0|1|2|3)\s*[:|]?\s*$", prefix))
        )

    start_match = next((m for m in target_matches if _is_boundary_like(m.start())), target_matches[0])
    start = start_match.start()
    if start <= 80 and raw.count("|") < 3:
        # Already anchored to the target point near the beginning, and not a
        # compact inline table/listing. Keep full text to avoid over-trimming.
        return raw
    boundary_re = re.compile(r"\b\d{1,2}(?:\.\d{1,2}){1,3}\b")
    end = len(raw)
    for boundary in boundary_re.finditer(raw, start_match.end()):
        candidate_id = str(boundary.group(0) or "").strip()
        if not candidate_id or candidate_id == pid:
            continue
        if not _looks_like_structured_point_id(candidate_id):
            continue
        if not _is_boundary_like(boundary.start()):
            continue
        end = boundary.start()
        break
    trimmed = raw[start:end].strip()
    # Prefer trimmed content when it remains informative and not tiny.
    if trimmed and (
        len(normalize_text(trimmed)) >= min(140, max(60, len(normalize_text(raw)) // 6))
        or (start > 30 and raw.count("|") >= 3)
    ):
        return trimmed
    return raw


def _looks_like_structured_point_id(point_id: str) -> bool:
    pid = str(point_id or "").strip()
    if not pid:
        return False
    parts = pid.split(".")
    if len(parts) < 2 or len(parts) > 4:
        return False
    for part in parts:
        if not part.isdigit():
            return False
        if len(part) > 2:
            return False
    # Building-part point IDs in this domain are section-based (1..12.*).
    # This avoids date-like tokens such as 23.02.
    try:
        first = int(parts[0])
    except ValueError:
        return False
    if first < 1 or first > 12:
        return False
    return True


def _looks_like_canonical_child_point_id(point_id: str) -> bool:
    return bool(re.match(r"^P\d{2}[A-Z]_", str(point_id or "").strip()))


def _is_semantic_point_id_supported(point_id: str) -> bool:
    return _looks_like_structured_point_id(point_id) or _looks_like_canonical_child_point_id(point_id)


def _extract_structured_point_ids_from_report(report_text: str) -> List[str]:
    if not report_text:
        return []
    seen = set()
    ordered: List[str] = []
    for match in re.finditer(r"\b\d{1,2}(?:\.\d{1,2}){1,3}\b", report_text):
        pid = str(match.group(0) or "").strip()
        if not _looks_like_structured_point_id(pid):
            continue
        if pid in seen:
            continue
        seen.add(pid)
        ordered.append(pid)
    return ordered


def _normalize_tg_for_semantic_eval(value: str) -> str:
    token = re.sub(r"[\s\-]+", "", str(value or "").upper())
    return token if token in {"TG2", "TG3", "TGIU"} else ""


def _extract_tg_from_point_text(raw_point_text: str, normalize_text) -> str:
    text = normalize_text(raw_point_text or "")
    if not text:
        return ""
    match = re.search(r"(?i)\bTG(?:\s*[-]?\s*(?:2|3|IU))\b", text)
    if not match:
        return ""
    return _normalize_tg_for_semantic_eval(str(match.group(0) or ""))


def _infer_point_title_from_text(point_id: str, raw_point_text: str, normalize_text) -> str:
    if not raw_point_text:
        return point_id
    lines = [line.strip() for line in str(raw_point_text).splitlines() if line.strip()]
    if not lines:
        return point_id
    header_re = re.compile(
        rf"(?i)^\s*(?:TG\s*(?:IU|0|1|2|3)\s+)?{re.escape(point_id)}(?:\b|(?=[\s\-\|:]))\s*(.*)$"
    )
    for line in lines[:12]:
        match = header_re.match(line)
        if not match:
            continue
        tail = str(match.group(1) or "").strip(" -|:")
        if tail:
            return tail[:180]
    return point_id


def _semantic_point_lookup_id(point: Dict[str, object], normalize_point_id) -> str:
    if not isinstance(point, dict):
        return ""
    for key in ("numeric_id", "native_label", "point_id"):
        candidate = normalize_point_id(str(point.get(key) or ""))
        if candidate:
            return candidate
    return ""


def _candidate_priority_for_point(point: Dict[str, object], effective_point_tg, normalize_text) -> tuple:
    return (
        _tg_rank_for_arkat(effective_point_tg(point)),
        len(normalize_text(str(point.get("title") or ""))),
        len(
            normalize_text(
                str(
                    point.get("effective_span_text")
                    or point.get("exact_span_text")
                    or point.get("span_text")
                    or ""
                )
            )
        ),
    )


def _canonicalize_points_by_id(
    points: List[Dict[str, object]],
    normalize_point_id,
    effective_point_tg,
    normalize_text,
) -> List[Dict[str, object]]:
    """
    Keep one canonical candidate per point_id to avoid duplicate-detection contamination.
    """
    grouped: Dict[str, List[Dict[str, object]]] = {}
    passthrough: List[Dict[str, object]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        point_id = _semantic_point_lookup_id(point, normalize_point_id)
        if not point_id:
            passthrough.append(point)
            continue
        grouped.setdefault(point_id, []).append(point)
    canonical: List[Dict[str, object]] = []
    for point_id in sorted(grouped.keys()):
        candidates = grouped[point_id]
        canonical.append(
            max(
                candidates,
                key=lambda item: _candidate_priority_for_point(item, effective_point_tg, normalize_text),
            )
        )
    return canonical + passthrough


def _bmtf_parent_group_is_child_listing(
    point_id: str,
    candidates: List[Dict[str, object]],
    child_point_ids: List[str],
    normalize_text,
    report_text: str = "",
) -> bool:
    """
    BMTF compact wet-room sections can put a TG marker on the parent row even
    though the row immediately continues into sub-points. Treat that parent as
    a container only when a real child heading appears right after the parent
    heading, before any body-like text.
    """
    pid = str(point_id or "").strip()
    if not (_looks_like_structured_point_id(pid) and pid.count(".") == 1):
        return False
    child_ids = [
        str(child_id or "").strip()
        for child_id in child_point_ids
        if str(child_id or "").strip().startswith(pid + ".")
    ]
    if not child_ids:
        return False
    parent_re = _point_id_exact_token_re(pid)
    child_patterns = [re.escape(child_id) for child_id in sorted(child_ids, key=len, reverse=True)]
    child_patterns.append(rf"{re.escape(pid)}\.\d{{1,2}}")
    child_re = re.compile(r"(?i)(?<![\d.])(?:%s)(?![\d.])" % "|".join(child_patterns))
    body_signal_re = re.compile(
        r"(?i)\b(?:det\s+er|overflater?:|membran:|sluk:|fuktm[åa]ling:|konsekvens|merknader?:|"
        r"risiko|anbefal(?:t|es)|tiltak)\b"
    )
    text_sources: List[str] = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        text_sources.append(str(
            candidate.get("effective_span_text")
            or candidate.get("exact_span_text")
            or candidate.get("span_text")
            or candidate.get("excerpt")
            or ""
        ))
    if report_text:
        text_sources.append(str(report_text))
    for raw in text_sources:
        text = normalize_text(raw or "")
        if not text:
            continue
        parent_match = parent_re.search(text)
        if not parent_match:
            continue
        child_match = child_re.search(text, parent_match.end())
        if not child_match or child_match.start() - parent_match.start() > 180:
            continue
        between = text[parent_match.end():child_match.start()]
        if body_signal_re.search(between):
            continue
        return True
    return False


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


def _extract_local_point_snippet_from_context(
    raw_text: str,
    target_point_id: str,
    normalize_text,
) -> str:
    raw = str(raw_text or "").strip()
    pid = str(target_point_id or "").strip()
    if not raw or not pid:
        return ""
    point_ref_re = _point_id_exact_token_re(pid)
    match = point_ref_re.search(raw)
    if not match:
        return raw
    start = match.start()
    boundary_re = re.compile(r"\b\d{1,2}(?:\.\d{1,2}){1,3}\b")
    end = len(raw)
    for boundary in boundary_re.finditer(raw, match.end()):
        token = str(boundary.group(0) or "").strip()
        if token and token != pid:
            end = boundary.start()
            break
    snippet = raw[start:end].strip()
    if not snippet:
        return ""
    # Keep contextual snippets compact to avoid reintroducing cross-point contamination.
    if len(normalize_text(snippet)) > 1800:
        snippet = snippet[:1800].strip()
    return snippet


def _collect_contextual_point_text_candidates(
    target_point_id: str,
    target_point_title: str,
    detected_points: List[Dict[str, object]],
    normalize_text,
    normalize_point_id,
) -> List[Dict[str, str]]:
    pid = normalize_point_id(str(target_point_id or "").strip())
    if not pid:
        return []
    target_title = normalize_text(target_point_title or "").lower().strip()
    point_ref_re = _point_id_exact_token_re(pid)
    candidates: List[Dict[str, str]] = []
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
        norm_ids = {normalize_point_id(c) for c in candidate_ids}
        spec_id = (
            str(point.get("point_id") or "").strip()
            or str(point.get("numeric_id") or "").strip()
            or str(point.get("native_label") or "").strip()
        )
        if normalize_point_id(spec_id) != pid:
            continue
        direct_ref = bool(point_ref_re.search(normalized))
        # Same point_id only — never pull sibling/parent spans into another point's ARKAT source.
        family_match = any(n == pid or pid.startswith(f"{n}.") for n in norm_ids if n)
        if family_match and _has_meaningful_arkat_signal(raw, normalize_text):
            candidates.append(
                {
                    "text": raw,
                    "source_point_id": spec_id,
                    "match_reason": "family_match",
                }
            )
            continue
        if direct_ref:
            local_snippet = _extract_local_point_snippet_from_context(raw, pid, normalize_text)
            if local_snippet and _has_meaningful_arkat_signal(local_snippet, normalize_text):
                candidates.append(
                    {
                        "text": local_snippet,
                        "source_point_id": spec_id,
                        "match_reason": "direct_ref_local_snippet",
                    }
                )
                continue
        title_match = bool(target_title and target_title in normalized.lower())
        if title_match:
            # Intentionally ignore title-only contextual joins; they are too noisy in
            # unlabeled prose reports and can cross-contaminate neighboring points.
            continue
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


def _strip_embedded_summary_tables_for_arkat_fields(text: str) -> str:
    """
    Eierskifte templates append [TABELLDATA] blocks that list many other punkt rows
    (TG3 summaries). Those rows must not feed årsak/risiko/konsekvens extraction.
    """
    raw = str(text or "").strip()
    if not raw:
        return raw
    low = raw.lower()
    idx = low.find("[tabelldata]")
    if idx < 0:
        return raw
    return raw[:idx].strip()


def _collapse_identical_arkat_field_triplet(fields: Dict[str, str], normalize_text) -> Dict[str, str]:
    """
    If årsak, risiko and konsekvens are identical non-missing strings, keep a single field
    using Norwegian phrasing hints so Dommer B is not triple-fed duplicate content.
    """
    if not isinstance(fields, dict):
        return fields
    a = str(fields.get("aarsak") or "").strip()
    r = str(fields.get("risiko") or "").strip()
    k = str(fields.get("konsekvens") or "").strip()
    if not a or a.upper() == "MISSING" or a != r or r != k:
        return fields
    if len(normalize_text(a)) < 25:
        return fields
    low = normalize_text(a).lower()
    out = dict(fields)
    if "risiko" in low and ("konsekvens" in low[:60] or low.startswith("konsekvens")):
        out["aarsak"] = "MISSING"
        out["konsekvens"] = "MISSING"
        out["risiko"] = a
    elif "konsekvens" in low[:60]:
        out["aarsak"] = "MISSING"
        out["risiko"] = "MISSING"
        out["konsekvens"] = a
    elif "risiko" in low:
        out["aarsak"] = "MISSING"
        out["konsekvens"] = "MISSING"
        out["risiko"] = a
    else:
        out["risiko"] = "MISSING"
        out["konsekvens"] = "MISSING"
        out["aarsak"] = a
    return out


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
    point_token_re = _point_id_exact_token_re(point_id)
    start_indexes: List[int] = []
    point_header_token_re = re.compile(rf"(?i)^\s*(?:TG\s*[0-3]\s*(?:\|)?\s*)?{re.escape(point_id)}(?:\b|(?=[\s\-\|:]))")

    def _window_supports_point(start_idx: int) -> bool:
        window = "\n".join(lines[start_idx:start_idx + 6])
        normalized_window = normalize_text(window).lower()
        if point_token_re.search(normalized_window):
            return True
        if target_title and target_title in normalized_window:
            return True
        return bool(
            re.search(r"(?i)\b(?:tg2|tg3|tilstandsgrad\s*[23])\b", normalized_window)
            or re.search(r"(?i)\b(?:årsak|arsak|risiko|konsekvens|anbefalt(?:e)?\s+tiltak|tiltak)\s*:", normalized_window)
        )

    for idx, line in enumerate(lines):
        normalized_line = normalize_text(line).lower()
        if not point_token_re.search(normalized_line) and not point_header_token_re.search(normalized_line):
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
    point_header_re = re.compile(r"(?i)^\s*(?:TG\s*(?:IU|0|1|2|3)\s+)?(\d+(?:\.\d+)+)\b")
    # Boundary markers that often appear inline in compacted/OCR text, e.g.
    # "... | TG 2 | 9.1.2 Gulvets overflate ..." or "Merknader: TG 2 9.1.2 ...".
    boundary_point_re = re.compile(
        r"(?i)(?:^|\||\bTG\s*(?:IU|0|1|2|3)\s*[|:]?\s*)(\d+(?:\.\d+){1,4})\b"
    )
    target_point_inline_re = _point_id_exact_token_re(point_id)

    def _collect_block(start_idx: int) -> str:
        collected: List[str] = []
        target_inline_start = None
        if 0 <= start_idx < len(lines):
            match = target_point_inline_re.search(lines[start_idx] or "")
            if match:
                target_inline_start = match.start()
        for idx in range(start_idx, len(lines)):
            line = lines[idx]
            if idx > start_idx:
                header_match = point_header_re.match(line.strip())
                if header_match and header_match.group(1) != point_id:
                    break
            boundary_cutoff = None
            for boundary_match in boundary_point_re.finditer(line):
                boundary_id = str(boundary_match.group(1) or "").strip()
                if not boundary_id or boundary_id == point_id:
                    continue
                # On the first line, ignore ancestor markers that appear before the
                # target point token in compact headings (e.g. "9 | 9.1 | ... | 9.1.1").
                if (
                    idx == start_idx
                    and target_inline_start is not None
                    and boundary_match.start() <= target_inline_start
                ):
                    continue
                boundary_cutoff = boundary_match.start()
                break
            if boundary_cutoff is not None:
                prefix = line[:boundary_cutoff].rstrip()
                if prefix:
                    collected.append(prefix)
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
        overlong_penalty = max(len(normalized) - 4500, 0)
        compact_length_bonus = min(len(normalized), 4500)
        return (
            arkat_labels,
            semantic_hits,
            summary_bonus,
            target_title_bonus,
            -overlong_penalty,
            compact_length_bonus,
        )

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


def _normalize_ns_version_value(ns_version: str) -> str:
    low = str(ns_version or "").strip().lower().replace(" ", "")
    if "2025" in low:
        return "NS3600:2025"
    if "2018" in low:
        return "NS3600:2018"
    return "NS3600:2018"


def _detect_ns_version_for_dommer_b(
    report_text: str, report_date: str, context_ns_version: str, normalize_text
) -> Tuple[str, Dict[str, object]]:
    """
    Detect NS 3600 edition used for Dommer B rule selection. Prefer explicit in-report
    statements; tolerate PDF glyph noise between '3600' and the year; only then fall back
    to regime context or report date heuristics.
    """
    meta: Dict[str, object] = {"source": "default", "detail": ""}
    blob = normalize_text((report_text or "")[:70000]).lower()
    if re.search(r"(?i)ns\s*3600\D{0,12}2025\b", blob):
        meta["source"] = "report_text"
        meta["detail"] = "ns3600_2025"
        return "NS3600:2025", meta
    if re.search(r"(?i)ns\s*3600\D{0,12}2018\b", blob) or "overgangsordning" in blob:
        meta["source"] = "report_text"
        meta["detail"] = "ns3600_2018"
        return "NS3600:2018", meta
    gap = re.search(r"(?i)ns.{0,18}3600.{0,26}(2018|2025)\b", blob)
    if gap:
        yr = gap.group(1)
        meta["source"] = "report_text_fuzzy"
        meta["detail"] = f"gap_tolerant_{yr}"
        return (f"NS3600:{yr}", meta)
    compact = re.sub(r"[^a-z0-9]", "", blob)
    compact_match = re.search(r"ns3600(?:standard)?(2018|2025)\b", compact)
    if compact_match:
        yr = compact_match.group(1)
        meta["source"] = "report_text_compact"
        meta["detail"] = f"compact_{yr}"
        return (f"NS3600:{yr}", meta)
    context_normalized = _normalize_ns_version_value(context_ns_version)
    if context_normalized in {"NS3600:2018", "NS3600:2025"} and str(context_ns_version or "").strip():
        meta["source"] = "regime_context"
        meta["detail"] = str(context_ns_version).strip()
        return context_normalized, meta
    if report_date:
        # Transition period through 2026-06 still defaults to NS 3600:2018 unless
        # report text explicitly states the 2025 edition.
        chosen = "NS3600:2025" if report_date >= "2026-07-01" else "NS3600:2018"
        meta["source"] = "report_date_fallback"
        meta["detail"] = str(report_date)
        return chosen, meta
    meta["detail"] = "hard_default_2018"
    return "NS3600:2018", meta


def _is_missing_like_value(value: object) -> bool:
    token = str(value or "").strip().upper()
    return token in {"", "MISSING", "N/A", "NA", "-", "IKKE AKTUELT", "IKKE RELEVANT"}


def _heuristic_tgiu_findings(
    point_label: str,
    raw_point_text: str,
    report_context: Dict[str, object],
    normalize_text,
) -> List[Dict[str, str]]:
    text = normalize_text(raw_point_text or "").lower()
    label = normalize_text(point_label or "").lower()
    context_text = normalize_text(str(report_context.get("relevant_component_context") or "")).lower()
    findings: List[Dict[str, str]] = []

    strong_reason_markers = (
        "fastskrudd", "låst", "snødekt", "ikke mulig å inspisere",
        "ikke tilgjengelig", "uten destruktive inngrep", "manglende inspeksjonsluke",
    )
    weak_reason_only = (
        any(token in text for token in ("lukket", "ikke undersøkt", "ingen tilkomst"))
        and not any(marker in text for marker in strong_reason_markers)
    )
    if weak_reason_only:
        findings.append({
            "error_type": "TGIU_MISSING_REASON",
            "explanation": "Punktet forklarer ikke tydelig hvorfor inspeksjon ikke var mulig.",
        })

    if not re.search(r"(?i)\\b(?:anbefales|bør|ytterligere\\s+unders[oø]kelser|videre\\s+unders[oø]kelser|kontroll\\s+b[oø]r)\\b", text):
        findings.append({
            "error_type": "TGIU_MISSING_FURTHER_INVESTIGATION",
            "explanation": "Ingen anbefaling om ytterligere undersøkelser er gitt.",
        })

    moisture_sensitive = bool(
        re.search(r"(?i)\\b(?:krypkjeller|rom\\s+under\\s+terreng|v[åa]trom|loft|yttertak|undertak)\\b", label + " " + context_text)
    )
    moisture_mentioned = bool(re.search(r"(?i)\\b(?:fukt|fuktrisiko|mugg|svertesopp|kondens)\\b", text))
    if moisture_sensitive and not moisture_mentioned:
        findings.append({
            "error_type": "TGIU_MISSING_MOISTURE_FLAG",
            "explanation": "Fuktrisiko er ikke eksplisitt vurdert for en fuktutsatt bygningsdel.",
        })

    is_crawlspace = bool(re.search(r"(?i)\\b(?:krypkjeller|crawlspace)\\b", label + " " + context_text))
    crawlspace_risk = bool(re.search(r"(?i)\\b(?:skaderisiko|konsekvens|fukt|råte|skadedyr)\\b", text))
    if is_crawlspace and not crawlspace_risk:
        findings.append({
            "error_type": "TGIU_CRAWLSPACE_MISSING_RISK_CONSEQUENCE",
            "explanation": "Krypkjeller med TGIU mangler omtale av skaderisiko og konsekvens.",
        })

    dedup: List[Dict[str, str]] = []
    seen = set()
    for finding in findings:
        et = str(finding.get("error_type") or "")
        if et and et not in seen:
            seen.add(et)
            dedup.append(finding)
    return dedup


def _is_arkat_field_required(field_name: str, tg_grade: str, ns_version: str) -> bool:
    tg = str(tg_grade or "").strip().upper()
    ns = _normalize_ns_version_value(ns_version)
    if tg == "TGIU":
        return False
    if field_name in {"aarsak", "risiko", "konsekvens"}:
        return tg in {"TG2", "TG3"}
    if field_name == "anbefalt_tiltak":
        if tg == "TG3":
            return True
        if tg == "TG2":
            return ns == "NS3600:2025"
        return False
    return False


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
    tg = str(tg_grade or "").strip().upper()
    ns = _normalize_ns_version_value(ns_version)
    if tg == "TGIU":
        return {"status": "NOT_APPLICABLE", "error_type": None, "explanation": ""}

    text = normalize_text(field_text or "").strip()
    low = text.lower()
    if _is_missing_like_value(text):
        if field_name == "anbefalt_tiltak" and tg == "TG2" and ns == "NS3600:2018":
            return {"status": "NOT_APPLICABLE", "error_type": None, "explanation": ""}
        return {"status": "MISSING", "error_type": f"MISSING ({field_name})", "explanation": ""}

    if field_name == "aarsak":
        if _ARKAT_AGE_ONLY_2018_RE.search(low):
            return {"status": "CORRECT", "error_type": None, "explanation": ""}
        if _ARKAT_CAUSE_PROSE_RE.search(low):
            return {"status": "CORRECT", "error_type": None, "explanation": ""}
        if _ARKAT_OBSERVATION_RE.search(low) or ("tg2 vurderes da" in low and not re.search(r"(?ix)\b(?:fordi|som\s+f[øo]lge\s+av|årsaken\s+er)\b", low)):
            return {"status": "WRONG", "error_type": "OBSERVATION_AS_AARSAK", "explanation": "Årsak beskriver hva som er observert, ikke hvorfor forholdet har oppstått."}
        if _ARKAT_CONDITIONAL_RE.search(low):
            return {"status": "WRONG", "error_type": "RISK_AS_AARSAK", "explanation": "Årsak bruker risiko- eller framtidsspråk i stedet for å forklare årsaken til forholdet."}
        return {"status": "CORRECT", "error_type": None, "explanation": ""}

    if field_name == "risiko":
        if _ARKAT_INSPECTION_LIMITATION_RE.search(low) and not (_ARKAT_CONDITIONAL_RE.search(low) or _ARKAT_RISK_DEVELOPMENT_RE.search(low)):
            return {"status": "WRONG", "error_type": "LIMITATION_USED_AS_RISK_SUBSTITUTE", "explanation": "Risiko beskriver en inspeksjonsbegrensning i stedet for hva som kan skje med bygningsdelen."}
        if _ARKAT_BUYER_IMPACT_RE.search(low) and not (_ARKAT_CONDITIONAL_RE.search(low) or _ARKAT_RISK_DEVELOPMENT_RE.search(low)):
            return {"status": "WRONG", "error_type": "CONSEQUENCE_AS_RISIKO", "explanation": "Risiko beskriver praktisk eller økonomisk betydning for kjøper, ikke framtidig bygningsrisiko."}
        if _ARKAT_PRESENT_STATE_RE.search(low) and not (_ARKAT_CONDITIONAL_RE.search(low) or _ARKAT_RISK_DEVELOPMENT_RE.search(low)):
            return {"status": "WRONG", "error_type": "PRESENT_STATE_AS_RISIKO", "explanation": "Risiko beskriver nåværende tilstand i stedet for mulig framtidig utvikling."}
        if _ARKAT_INSPECTION_LIMITATION_RE.search(low) and "kan være forhold" in low and not _ARKAT_RISK_DEVELOPMENT_RE.search(low):
            return {"status": "WRONG", "error_type": "LIMITATION_AS_RISIKO", "explanation": "Risiko bruker inspeksjonsbegrensning som erstatning for faktisk bygningsrisiko."}
        if _ARKAT_CAUSE_PROSE_RE.search(low) and not (_ARKAT_CONDITIONAL_RE.search(low) or _ARKAT_RISK_DEVELOPMENT_RE.search(low)):
            return {"status": "WRONG", "error_type": "AARSAK_AS_RISIKO", "explanation": "Risiko-feltet beskriver årsak i stedet for framtidig risiko."}
        return {"status": "CORRECT", "error_type": None, "explanation": ""}

    if field_name == "konsekvens":
        has_buyer = _has_buyer_oriented_consequence_signal(text, normalize_text)
        if _ARKAT_LIFESPAN_ONLY_CONSEQUENCE_RE.search(low) and not has_buyer:
            return {"status": "WRONG", "error_type": "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS", "explanation": "Konsekvens beskriver teknisk status uten tydelig kjøperrelevans."}
        if (
            (_ARKAT_TECHNICAL_DEVELOPMENT_RE.search(low) or _ARKAT_CONDITIONAL_RE.search(low) or _ARKAT_RISK_DEVELOPMENT_RE.search(low))
            and not has_buyer
        ):
            return {"status": "WRONG", "error_type": "TECHNICAL_DEVELOPMENT_AS_KONSEKVENS", "explanation": "Konsekvens må beskrive hva forholdet betyr for kjøper, ikke bare teknisk skadeutvikling."}
        return {"status": "CORRECT", "error_type": None, "explanation": ""}

    if field_name == "anbefalt_tiltak":
        if tg == "TG2" and ns == "NS3600:2018" and _is_semantically_missing_text(normalize_text, text):
            return {"status": "NOT_APPLICABLE", "error_type": None, "explanation": ""}
        if not _ARKAT_ACTION_RE.search(low) and not _ARKAT_ACTION_PROSE_RE.search(low) and (
            "årsaken" in low or "skyldes" in low or _ARKAT_CONDITIONAL_RE.search(low)
        ):
            return {"status": "WRONG", "error_type": "EXPLANATION_AS_TILTAK", "explanation": "Anbefalt tiltak forklarer forholdet eller gjentar risikoen i stedet for å peke på et konkret neste steg."}
        if _is_arkat_field_required(field_name, tg_grade, ns):
            if _is_semantically_missing_text(normalize_text, text):
                return {"status": "MISSING", "error_type": "MISSING (anbefalt_tiltak)", "explanation": "Anbefalt tiltak mangler."}
            if not _ARKAT_ACTION_RE.search(low) and not _ARKAT_ACTION_PROSE_RE.search(low):
                return {"status": "WRONG", "error_type": "EXPLANATION_AS_TILTAK", "explanation": "Anbefalt tiltak mangler et konkret tiltak eller neste steg."}
            if tg == "TG3" and re.search(r"(?ix)\b(?:kan\s+vurderes|eventuelt\s+kan|kan\s+p[åa]\s+sikt)\b", low):
                return {"status": "WRONG", "error_type": "TILTAK_VAGUE_WITHOUT_NECESSITY", "explanation": "Tiltak er for vagt formulert ved TG3."}
        if re.search(r"(?ix)\b(?:skal\s+|m[aå]\s+utf[oø]res|det\s+kreves\s+at)\b", low):
            return {"status": "WRONG", "error_type": "TILTAK_IMPERATIVE_FORM", "explanation": "Tiltak er formulert som pålegg i stedet for anbefaling."}
        return {"status": "CORRECT", "error_type": None, "explanation": ""}

    return {"status": "MISSING", "error_type": f"MISSING ({field_name})", "explanation": ""}


def _normalize_arkat_eval_result(
    parsed: Optional[Dict[str, object]],
    point_id: str,
    point_label: str,
    tg_grade: str,
    extracted_fields: Dict[str, str],
    raw_point_text: str,
    ns_version: str,
    report_context: Dict[str, object],
    normalize_text,
) -> Dict[str, object]:
    if isinstance(parsed, dict) and parsed.get("_raw_text"):
        plaintext = _parse_plaintext_arkat_eval(str(parsed.get("_raw_text") or ""), normalize_text)
        if plaintext:
            parsed = plaintext
    default_tgiu_findings = []
    if str(tg_grade or "").strip().upper() == "TGIU":
        default_tgiu_findings = _heuristic_tgiu_findings(
            point_label=point_label,
            raw_point_text=raw_point_text,
            report_context=report_context or {},
            normalize_text=normalize_text,
        )
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
    ns = _normalize_ns_version_value(ns_version)
    default["tgiu_findings"] = {"findings": default_tgiu_findings}
    default["has_errors"] = any(
        (
            str(result.get("status") or "").strip() in {"WRONG", "MISSING"}
            and _is_arkat_field_required(field_name, tg_grade, ns)
        )
        for field_name, result in default["field_results"].items()
        if isinstance(result, dict)
    )
    if not isinstance(parsed, dict):
        return default
    field_results = parsed.get("field_results")
    if not isinstance(field_results, dict):
        return default
    normalized = {
        "point_id": point_id,
        "tg_grade": tg_grade,
        "field_results": {},
        "tgiu_findings": {"findings": []},
        "has_errors": False,
    }
    parsed_tgiu = parsed.get("tgiu_findings")
    if isinstance(parsed_tgiu, dict):
        findings = parsed_tgiu.get("findings")
        if isinstance(findings, list):
            validated = []
            seen_tgiu = set()
            for item in findings:
                if not isinstance(item, dict):
                    continue
                et = str(item.get("error_type") or "").strip()
                if et in _DOMMER_B_ALLOWED_ERROR_TYPES and et.startswith("TGIU_") and et not in seen_tgiu:
                    seen_tgiu.add(et)
                    validated.append(
                        {"error_type": et, "explanation": str(item.get("explanation") or "").strip()}
                    )
            # Keep heuristic TGIU checks as safety net so one weak LLM judgement
            # does not suppress a required independent TGIU finding.
            for fallback_item in default_tgiu_findings:
                if not isinstance(fallback_item, dict):
                    continue
                fallback_et = str(fallback_item.get("error_type") or "").strip()
                if fallback_et in _DOMMER_B_ALLOWED_ERROR_TYPES and fallback_et.startswith("TGIU_") and fallback_et not in seen_tgiu:
                    seen_tgiu.add(fallback_et)
                    validated.append(
                        {
                            "error_type": fallback_et,
                            "explanation": str(fallback_item.get("explanation") or "").strip(),
                        }
                    )
            normalized["tgiu_findings"] = {"findings": validated}
    for field_name in ("aarsak", "risiko", "konsekvens", "anbefalt_tiltak"):
        candidate = field_results.get(field_name)
        if not isinstance(candidate, dict):
            candidate = default["field_results"][field_name]
        status = str(candidate.get("status") or "").strip().upper() or str(default["field_results"][field_name].get("status") or "")
        explanation = str(candidate.get("explanation") or "").strip()
        fallback = default["field_results"][field_name]
        error_type = candidate.get("error_type")
        if status.startswith("WRONG:"):
            error_type = status.split("WRONG:", 1)[1].strip()
            status = "WRONG"
        if status not in {"CORRECT", "WRONG", "MISSING", "NOT_APPLICABLE"}:
            status = str(fallback.get("status") or "CORRECT")
            error_type = fallback.get("error_type")
            explanation = str(fallback.get("explanation") or "")
        if status == "MISSING" and not error_type:
            error_type = f"MISSING ({field_name})"
        if status == "WRONG" and (not error_type or error_type not in _DOMMER_B_ALLOWED_ERROR_TYPES):
            fallback_error = str(fallback.get("error_type") or "").strip()
            if fallback_error in _DOMMER_B_ALLOWED_ERROR_TYPES:
                error_type = fallback_error
            else:
                status = "CORRECT"
                error_type = None
                explanation = ""
        field_text = str(extracted_fields.get(field_name) or "")
        has_field_text = not _is_semantically_missing_text(normalize_text, field_text)
        # Enforce input-handling contract: if field content exists (possibly recovered from
        # raw_point_text), final status must not stay MISSING due to LLM under-extraction.
        if status == "MISSING" and has_field_text:
            recovered = _heuristic_evaluate_arkat_field(field_name, field_text, ns_version, tg_grade, normalize_text)
            status = str(recovered.get("status") or status)
            error_type = recovered.get("error_type")
            explanation = str(recovered.get("explanation") or "")
        # TG2/NS3600:2018 allows NOT_APPLICABLE only when tiltak is actually absent.
        if (
            field_name == "anbefalt_tiltak"
            and status == "NOT_APPLICABLE"
            and str(tg_grade or "").strip().upper() == "TG2"
            and _normalize_ns_version_value(ns_version) == "NS3600:2018"
            and has_field_text
        ):
            recovered = _heuristic_evaluate_arkat_field(field_name, field_text, ns_version, tg_grade, normalize_text)
            status = str(recovered.get("status") or status)
            error_type = recovered.get("error_type")
            explanation = str(recovered.get("explanation") or "")
        if status in {"CORRECT", "NOT_APPLICABLE"}:
            error_type = None
            explanation = ""
        result = {"status": status, "error_type": error_type, "explanation": explanation}
        normalized["field_results"][field_name] = result
        if status in {"WRONG", "MISSING"} and _is_arkat_field_required(field_name, tg_grade, ns):
            normalized["has_errors"] = True
    if str(tg_grade or "").strip().upper() == "TGIU" and normalized["tgiu_findings"]["findings"]:
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
    report_context: Dict[str, object],
    normalize_text,
    allow_llm: bool = True,
) -> Dict[str, object]:
    tg_upper = str(tg_grade or "").strip().upper()
    # Prompt contract requires consulting raw_point_text when extracted_fields are empty.
    # Hydrate missing fields from unlabeled prose before any heuristic/LLM decision.
    hydrated_fields = _merge_missing_arkat_fields(
        extracted_fields or {},
        _extract_unlabeled_arkat_fields(raw_point_text, normalize_text),
        normalize_text,
    )

    if _DISABLE_POINT_LEVEL_ARKAT_LLM:
        out = _normalize_arkat_eval_result(None, point_id, point_label, tg_grade, hydrated_fields, raw_point_text, ns_version, report_context, normalize_text)
        out["used_llm"] = False
        return out

    # Heuristic evaluation is used as a fallback when LLM cannot run.
    heuristic_eval = _normalize_arkat_eval_result(None, point_id, point_label, tg_grade, hydrated_fields, raw_point_text, ns_version, report_context, normalize_text)
    # TGIU must be evaluated through Dommer B prompt checks when LLM is available.
    force_llm_for_tgiu = tg_upper == "TGIU"
    if not allow_llm:
        heuristic_eval["used_llm"] = False
        return heuristic_eval

    bundle = _get_client_arkat_bundle()
    step = bundle.get("pipeline_step") or {}
    system_prompt = str(step.get("system_prompt", {}).get("content") or "").strip()
    user_template = str(step.get("user_prompt_template", {}).get("content") or "").strip()
    if not force_llm_for_tgiu and not _point_has_descriptive_text_for_arkat(raw_point_text, hydrated_fields, normalize_text):
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
        # Always pass hydrated fields so Dommer B sees any extraction/fallback signal,
        # while still being free to validate against raw_point_text per prompt contract.
        "{extracted_fields.aarsak}": hydrated_fields.get("aarsak", ""),
        "{extracted_fields.risiko}": hydrated_fields.get("risiko", ""),
        "{extracted_fields.konsekvens}": hydrated_fields.get("konsekvens", ""),
        "{extracted_fields.anbefalt_tiltak}": hydrated_fields.get("anbefalt_tiltak", ""),
        "{report_context.building_year}": report_context.get("building_year", ""),
        "{report_context.dwelling_type}": report_context.get("dwelling_type", ""),
        "{report_context.building_method_summary}": report_context.get("building_method_summary", ""),
        "{report_context.relevant_component_context}": report_context.get("relevant_component_context", ""),
    }
    for key, value in replacements.items():
        prompt = prompt.replace(key, str(value or ""))
    examples_injection = _build_arkat_examples_injection(hydrated_fields, raw_point_text, ns_version, normalize_text)
    if examples_injection:
        prompt = f"{prompt}\n\n{examples_injection}"
    parsed = _call_json_llm(system_prompt, prompt, max_tokens=1100)
    out = _normalize_arkat_eval_result(parsed, point_id, point_label, tg_grade, hydrated_fields, raw_point_text, ns_version, report_context, normalize_text)
    out["used_llm"] = parsed is not None
    return out


def _status_to_scoring_meta(field_name: str, result: Dict[str, object]) -> Optional[Dict[str, object]]:
    status = str(result.get("status") or "").strip().upper()
    if not status or status in {"CORRECT", "NOT_APPLICABLE"}:
        return None
    bridge_key = str(result.get("error_type") or "").strip()
    if status == "MISSING" and not bridge_key:
        bridge_key = f"MISSING ({field_name})"
    if not bridge_key:
        return None
    severity = "medium"
    points = 3
    if bridge_key in {"TECHNICAL_DEVELOPMENT_AS_KONSEKVENS", "EXPLANATION_AS_TILTAK", "CONSEQUENCE_AS_TILTAK", "TILTAK_IMPERATIVE_FORM", "TILTAK_VAGUE_WITHOUT_NECESSITY"}:
        severity = "low"
        points = 1
    if bridge_key in {"MISSING (konsekvens)"}:
        severity = "high"
        points = 5
    return {"bridge_key": bridge_key or status, "severity": severity, "points": points, "status": status}


def _arkat_ui_status_from_eval(result: Dict[str, object], tg_grade: str) -> str:
    status = str(result.get("status") or "").strip()
    if status == "NOT_APPLICABLE":
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
    ns_version = str(point_meta.get("ns_version") or "")
    report_format = str(point_meta.get("report_format") or "")
    raw_point_text = str(point_meta.get("raw_point_text") or "")
    component["arkat"] = {
        "arsak": {"status": _arkat_ui_status_for_field("aarsak", extracted_fields.get("aarsak"), field_results.get("aarsak", {}), tg_grade, report_format, raw_point_text, normalize_text), "required": _is_arkat_field_required("aarsak", tg_grade, ns_version), "comment": str((field_results.get("aarsak") or {}).get("explanation") or "")},
        "risiko": {"status": _arkat_ui_status_for_field("risiko", extracted_fields.get("risiko"), field_results.get("risiko", {}), tg_grade, report_format, raw_point_text, normalize_text), "required": _is_arkat_field_required("risiko", tg_grade, ns_version), "comment": str((field_results.get("risiko") or {}).get("explanation") or "")},
        "konsekvens": {"status": _arkat_ui_status_for_field("konsekvens", extracted_fields.get("konsekvens"), field_results.get("konsekvens", {}), tg_grade, report_format, raw_point_text, normalize_text), "required": _is_arkat_field_required("konsekvens", tg_grade, ns_version), "comment": str((field_results.get("konsekvens") or {}).get("explanation") or "")},
        "anbefalt_tiltak": {"status": _arkat_ui_status_for_field("anbefalt_tiltak", extracted_fields.get("anbefalt_tiltak"), field_results.get("anbefalt_tiltak", {}), tg_grade, report_format, raw_point_text, normalize_text), "required": _is_arkat_field_required("anbefalt_tiltak", tg_grade, ns_version), "comment": str((field_results.get("anbefalt_tiltak") or {}).get("explanation") or "")},
        "source": {"found": True, "where": "under_bygningsdel", "traceability_ok": True},
    }


def _apply_arkat_evaluation_results(analysis_output: Dict[str, object], point_meta: Dict[str, object], evaluation: Dict[str, object], report_date: str, normalize_point_id, append_unique_all_finding, iso_date_at_or_after, railings_topic_re) -> None:
    point_id = normalize_point_id(str(point_meta.get("point_id") or ""))
    point_title = str(point_meta.get("title") or point_id)
    tg_grade = str(point_meta.get("tg_grade") or "")
    exact_text = str(point_meta.get("raw_point_text") or "")
    no_tg_hms_point = bool(point_meta.get("no_tg_hms_point"))
    seen_keys = set()
    if iso_date_at_or_after(report_date, "2026-01-01") and no_tg_hms_point and railings_topic_re.search(f"{point_title}\n{exact_text}"):
        return
    if tg_grade == "TGIU":
        tgiu_findings = (
            evaluation.get("tgiu_findings", {}).get("findings", [])
            if isinstance(evaluation.get("tgiu_findings"), dict)
            else []
        )
        points_by_error = {
            "TGIU_MISSING_REASON": 4,
            "TGIU_MISSING_FURTHER_INVESTIGATION": 4,
            "TGIU_MISSING_MOISTURE_FLAG": 3,
            "TGIU_CRAWLSPACE_MISSING_RISK_CONSEQUENCE": 2,
        }
        for finding in tgiu_findings if isinstance(tgiu_findings, list) else []:
            if not isinstance(finding, dict):
                continue
            error_type = str(finding.get("error_type") or "").strip()
            if not error_type:
                continue
            dedupe_key = (point_id, "tgiu", error_type)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            points = int(points_by_error.get(error_type, 2))
            message = str(finding.get("explanation") or "").strip() or f"TGIU finding: {error_type}"
            rule_suffix = re.sub(r"[^A-Z0-9_]+", "_", error_type.upper()).strip("_")
            rule_id = f"A_ARKAT.TGIU.{rule_suffix}"
            append_unique_all_finding(
                analysis_output,
                {
                    "finding_id": f"A_ARKAT_{point_id.replace('.', '_')}_TGIU_{rule_suffix}",
                    "rule_id": rule_id,
                    "point_id": point_id,
                    "exact_point_id": point_id,
                    "exact_point_title": point_title,
                    "exact_point_text": exact_text,
                    "category": "A",
                    "severity": "minor",
                    "deduction_band": "Middels trekk",
                    "title": f"Punkt {point_id}: {error_type}",
                    "message": message,
                    "recommended_fix_text": "Oppdater TGIU-begrunnelse og anbefaling i punktet.",
                    "suggested_rewrite_text": message,
                    "rewrite_strategy": "arkat_tgiu_alignment",
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
        return
    for field_name, result in (evaluation.get("field_results") or {}).items():
        if not isinstance(result, dict):
            continue
        if tg_grade == "TGIU":
            continue
        scoring = _status_to_scoring_meta(field_name, result)
        if not scoring:
            continue
        status = scoring["status"]
        bridge_key = str(scoring["bridge_key"] or "")
        severity = str(scoring["severity"] or "medium")
        points = int(scoring["points"] or 0)
        dedupe_key = (point_id, field_name, bridge_key)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
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
    ns_version = str(point_payload.get("ns_version") or "")
    if not _is_arkat_field_required(field_name, tg_grade, ns_version):
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
            if _is_arkat_field_required(
                field_name,
                str(point_payload.get("tg_grade") or ""),
                str(point_payload.get("ns_version") or ""),
            )
        ]
        if relevant_fields and all(_arkat_semantic_field_is_ok(point_payload, field_name) for field_name in relevant_fields):
            return True
    return False


def _is_legacy_arkat_item_for_semantic_point(item: object, normalize_point_id, semantic_points: Dict[str, Dict[str, object]]) -> bool:
    if not isinstance(item, dict):
        return False
    point_ids = _arkat_semantic_item_point_ids(item, normalize_point_id, semantic_points)
    if not point_ids:
        return False
    rule_id = str(item.get("rule_id") or "").strip()
    finding_id = str(item.get("finding_id") or "").strip()
    legacy_patterns = (
        "A_ARKAT_KONSEKVENS_NOT_BUYER_ORIENTED",
        "A_ARKAT.konsekvens_missing",
        "A_ARKAT.risiko_missing",
        "A_ARKAT.arsak_missing",
        "A_ARKAT.observasjon_unclear",
    )
    if any(pat in rule_id or pat in finding_id for pat in legacy_patterns):
        return True
    title = str(item.get("title") or "").lower()
    message = str(item.get("message") or "").lower()
    if "konsekvens ikke kjøperorientert" in title or "konsekvens ikke kjøperorientert" in message:
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
                    and not _is_legacy_arkat_item_for_semantic_point(deduction, normalize_point_id, semantic_points)
                ]

    for key in ("all_findings", "top_issues", "top_score_drivers", "score_drivers"):
        items = analysis_output.get(key)
        if isinstance(items, list):
            analysis_output[key] = [
                item
                for item in items
                if not _arkat_semantic_item_is_obsolete(item, normalize_point_id, semantic_points)
                and not _is_legacy_arkat_item_for_semantic_point(item, normalize_point_id, semantic_points)
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


def _build_report_context_for_point(
    report_text: str,
    point_id: str,
    point_title: str,
    raw_point_text: str,
    report_date: str,
    normalize_text,
) -> Dict[str, object]:
    header = normalize_text((report_text or "")[:15000])
    building_year = None
    year_candidates = [int(match.group(1)) for match in re.finditer(r"(?i)\b(?:bygge[aå]r|oppf[oø]rt|oppført)\D{0,20}(19\d{2}|20\d{2})\b", header)]
    if year_candidates:
        building_year = min(year_candidates)
    dwelling_type = ""
    for key in ("enebolig", "rekkehus", "leilighet", "fritidsbolig", "tomannsbolig"):
        if re.search(rf"(?i)\b{re.escape(key)}\b", header):
            dwelling_type = key
            break
    building_method_summary = ""
    method_match = re.search(r"(?is)(om byggemetoden|byggemetode|konstruksjon)\s*[:\-]?\s*(.{0,700})", header)
    if method_match:
        building_method_summary = str(method_match.group(2) or "").strip()
    relevant_context = f"{point_id} {point_title}".strip()
    if raw_point_text:
        relevant_context = f"{relevant_context}. {normalize_text(raw_point_text)[:1200]}".strip()
    if report_date:
        relevant_context = f"{relevant_context} Rapportdato: {report_date}."
    return {
        "building_year": building_year,
        "dwelling_type": dwelling_type,
        "building_method_summary": building_method_summary,
        "relevant_component_context": relevant_context[:1800],
    }


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
    report_date = context.get("report_date") or ""
    ns_version, ns_version_detection = _detect_ns_version_for_dommer_b(
        report_text=report_text,
        report_date=report_date,
        context_ns_version=str(context.get("ns_version") or ""),
        normalize_text=normalize_text,
    )
    linked_summary_by_point = (
        extract_linked_summary_text_per_point(report_text or "")
        if callable(extract_linked_summary_text_per_point)
        else {}
    )
    canonical_detected_points = _canonicalize_points_by_id(
        [point for point in detected_points if isinstance(point, dict)],
        normalize_point_id=normalize_point_id,
        effective_point_tg=effective_point_tg,
        normalize_text=normalize_text,
    )
    available_point_ids = [
        _semantic_point_lookup_id(point, normalize_point_id)
        for point in canonical_detected_points
        if _semantic_point_lookup_id(point, normalize_point_id)
    ]
    point_groups: Dict[str, List[Dict[str, object]]] = {}
    for point in canonical_detected_points:
        point_id = _semantic_point_lookup_id(point, normalize_point_id)
        if not point_id or is_synthetic_supplement_point_id(point_id) or bool(point.get("synthetic_supplement")):
            continue
        tg_grade = effective_point_tg(point)
        if _point_has_real_child(
            point_id,
            canonical_detected_points,
            normalize_point_id,
            is_synthetic_supplement_point_id,
            is_parent_of,
        ):
            continue
        # Point-level ARKAT segmentation supports both numeric structured IDs (e.g. 7.1.1)
        # and canonical child IDs (e.g. P07A_*). Skip unsupported IDs to avoid contamination.
        if not _is_semantic_point_id_supported(point_id):
            continue
        # Strict gate: Dommer B only evaluates TG2/TG3/TGIU points.
        # Do not infer TG from surrounding text for TG0/TG1/empty points.
        if tg_grade not in {"TG2", "TG3", "TGIU"}:
            continue
        point_groups.setdefault(point_id, []).append(point)

    # Backfill missing structured TG2/TG3/TGIU points directly from report text when
    # header segmentation misses inline/compacted point headings (common in summary tables).
    for recovered_point_id in _extract_structured_point_ids_from_report(report_text):
        if recovered_point_id in point_groups:
            continue
        recovered_text = _recover_point_text_from_report(report_text, recovered_point_id, "", normalize_text)
        if not recovered_text:
            continue
        recovered_tg = _extract_tg_from_point_text(recovered_text, normalize_text)
        if recovered_tg not in {"TG2", "TG3", "TGIU"}:
            continue
        point_groups[recovered_point_id] = [
            {
                "point_id": recovered_point_id,
                "title": _infer_point_title_from_text(recovered_point_id, recovered_text, normalize_text),
                "tg": recovered_tg,
                "effective_span_text": recovered_text,
                "exact_span_text": recovered_text,
                "span_text": recovered_text,
            }
        ]
    skipped_container_parent_ids: List[str] = []
    for parent_point_id in list(point_groups.keys()):
        child_ids = []
        for candidate_point in canonical_detected_points:
            if not isinstance(candidate_point, dict):
                continue
            child_id = _semantic_point_lookup_id(candidate_point, normalize_point_id)
            if (
                child_id
                and child_id != parent_point_id
                and not bool(candidate_point.get("synthetic_supplement"))
                and not is_synthetic_supplement_point_id(child_id)
                and is_parent_of(parent_point_id, child_id)
            ):
                child_ids.append(child_id)
        if not child_ids:
            continue
        if _bmtf_parent_group_is_child_listing(
            parent_point_id,
            point_groups.get(parent_point_id, []),
            child_ids,
            normalize_text,
            report_text,
        ):
            point_groups.pop(parent_point_id, None)
            skipped_container_parent_ids.append(parent_point_id)
    results: List[Dict[str, object]] = []
    llm_calls_used = 0
    expected_point_ids = sorted(set(point_groups.keys()).union(skipped_container_parent_ids))
    for point_id, candidates in point_groups.items():
        if not candidates:
            continue
        point = max(
            candidates,
            key=lambda item: _candidate_priority_for_point(item, effective_point_tg, normalize_text),
        )
        point_id = (
            str(point_id or "").strip()
            or _semantic_point_lookup_id(point, normalize_point_id)
            or normalize_point_id(str(point.get("canonical_point_id") or ""))
        )
        if not point_id:
            continue
        tg_grade = max((effective_point_tg(candidate) for candidate in candidates), key=_tg_rank_for_arkat)
        # Final hard gate: only TG2/TG3/TGIU points are evaluated by Dommer B.
        if str(tg_grade or "").strip().upper() not in {"TG2", "TG3", "TGIU"}:
            continue
        is_canonical_child_point = _looks_like_canonical_child_point_id(point_id)
        raw_point_text_candidates: List[str] = []
        primary_field_chunks: List[str] = []
        candidate_debug: List[Dict[str, object]] = []

        def _record_candidate(source: str, text: str, source_point_id: str = "", reason: str = "") -> None:
            normalized = normalize_text(text or "").strip()
            if not normalized:
                return
            candidate_debug.append(
                {
                    "source": source,
                    "source_point_id": source_point_id,
                    "reason": reason,
                    "length_chars": len(text or ""),
                    "length_norm_chars": len(normalized),
                    "preview": (text or "")[:220],
                }
            )

        for candidate in candidates:
            candidate_text = str(candidate.get("effective_span_text") or candidate.get("exact_span_text") or candidate.get("span_text") or "").strip()
            candidate_text = _trim_text_to_point_window(candidate_text, point_id, normalize_text)
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
                primary_field_chunks.append(candidate_text)
                _record_candidate(
                    source="primary_candidate",
                    text=candidate_text,
                    source_point_id=_semantic_point_lookup_id(candidate, normalize_point_id),
                    reason="canonical_point_group_candidate",
                )
            if not is_canonical_child_point and _point_text_needs_report_fallback(candidate_text, point_id, str(candidate.get("title") or ""), normalize_text):
                recovered = _recover_point_text_from_report(report_text, point_id, str(candidate.get("title") or ""), normalize_text)
                recovered = _trim_text_to_point_window(recovered, point_id, normalize_text)
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
                    primary_field_chunks.append(recovered)
                    _record_candidate(
                        source="recovered_candidate",
                        text=recovered,
                        source_point_id=point_id,
                        reason="point_text_needs_report_fallback=true",
                    )
        if not is_canonical_child_point:
            contextual_candidates = _collect_contextual_point_text_candidates(
                point_id,
                str(point.get("title") or ""),
                canonical_detected_points,
                normalize_text,
                normalize_point_id,
            )
            for contextual_entry in contextual_candidates:
                contextual_text = str(contextual_entry.get("text") or "").strip()
                if not contextual_text:
                    continue
                contextual_text = _trim_text_to_point_window(contextual_text, point_id, normalize_text)
                raw_point_text_candidates.append(contextual_text)
                _record_candidate(
                    source="contextual_candidate",
                    text=contextual_text,
                    source_point_id=str(contextual_entry.get("source_point_id") or ""),
                    reason=str(contextual_entry.get("match_reason") or "contextual"),
                )
        raw_point_text = _combine_point_text_candidates(raw_point_text_candidates, normalize_text)
        raw_point_text = _trim_text_to_point_window(raw_point_text, point_id, normalize_text)
        if not raw_point_text:
            recovered = ""
            if not is_canonical_child_point:
                recovered = _recover_point_text_from_report(report_text, point_id, str(point.get("title") or ""), normalize_text)
            recovered = _trim_text_to_point_window(recovered, point_id, normalize_text)
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
            raw_point_text = _trim_text_to_point_window(raw_point_text, point_id, normalize_text)
            if recovered:
                _record_candidate(
                    source="recovered_candidate_force",
                    text=recovered,
                    source_point_id=point_id,
                    reason="no_candidates_after_combine",
                )
        def _extract_and_sanitize_fields(text_source: str) -> Dict[str, str]:
            trimmed = _strip_embedded_summary_tables_for_arkat_fields(text_source)
            extracted = _extract_fields_for_point(
                str(format_meta.get("report_format") or ""),
                trimmed,
                extract_arkat_section_text,
                normalize_text,
            )
            return _collapse_identical_arkat_field_triplet(extracted, normalize_text)

        primary_field_blob = _combine_point_text_candidates(primary_field_chunks, normalize_text)
        primary_field_blob = _trim_text_to_point_window(primary_field_blob, point_id, normalize_text)
        field_extraction_text = primary_field_blob
        if not normalize_text(field_extraction_text).strip():
            field_extraction_text = _trim_text_to_point_window(raw_point_text, point_id, normalize_text)
        extracted_fields = _extract_and_sanitize_fields(field_extraction_text)
        recovered = ""
        if not is_canonical_child_point:
            recovered = _recover_point_text_from_report(report_text, point_id, str(point.get("title") or ""), normalize_text)
        recovered = _trim_text_to_point_window(recovered, point_id, normalize_text)
        recovered = _augment_point_text_with_linked_summary(
            recovered,
            point_id,
            linked_summary_by_point,
            get_linked_summary_for_point,
            available_point_ids,
            normalize_text,
        )
        if recovered and normalize_text(recovered) != normalize_text(raw_point_text):
            recovered_fields = _extract_and_sanitize_fields(recovered)
            current_score = _count_present_arkat_fields(extracted_fields, normalize_text, tg_grade)
            recovered_score = _count_present_arkat_fields(recovered_fields, normalize_text, tg_grade)
            if recovered_score > current_score or (
                recovered_score == current_score and len(normalize_text(recovered)) > len(normalize_text(raw_point_text))
            ):
                raw_point_text = _combine_point_text_candidates([raw_point_text, recovered], normalize_text)
                raw_point_text = _trim_text_to_point_window(raw_point_text, point_id, normalize_text)
                if not normalize_text(primary_field_blob or "").strip():
                    field_extraction_text = _trim_text_to_point_window(raw_point_text, point_id, normalize_text)
                    extracted_fields = _extract_and_sanitize_fields(field_extraction_text)
                _record_candidate(
                    source="recovered_candidate_merge_upgrade",
                    text=recovered,
                    source_point_id=point_id,
                    reason="recovered_score>=current_score",
                )
        report_context = _build_report_context_for_point(
            report_text=report_text,
            point_id=point_id,
            point_title=str(point.get("title") or point_id),
            raw_point_text=raw_point_text,
            report_date=report_date,
            normalize_text=normalize_text,
        )
        evaluation = _evaluate_arkat_point(
            point_id=point_id,
            point_label=str(point.get("title") or point_id),
            tg_grade=tg_grade,
            report_format=str(format_meta.get("report_format") or ""),
            ns_version=ns_version,
            raw_point_text=raw_point_text,
            extracted_fields=extracted_fields,
            report_context=report_context,
            normalize_text=normalize_text,
            # Dommer B is an LLM-based evaluator; always allow call attempts.
            # (TGIU checks in particular must run through the prompt logic.)
            allow_llm=True,
        )
        if bool(evaluation.get("used_llm")):
            llm_calls_used += 1
        point_payload = {
            "point_id": point_id,
            "title": str(point.get("title") or point_id),
            "tg_grade": tg_grade,
            "ns_version": ns_version,
            "report_format": format_meta.get("report_format") or "",
            "extraction_method_used": format_meta.get("extraction_method_used") or "",
            "raw_point_text": raw_point_text,
            "extracted_fields": extracted_fields,
            "report_context": report_context,
            "evaluation": evaluation,
            "no_tg_hms_point": bool(point.get("no_tg_hms_point")),
        }
        selected_normalized = normalize_text(raw_point_text or "").strip().lower()
        for entry in candidate_debug:
            preview_normalized = normalize_text(str(entry.get("preview") or "")).strip().lower()
            entry["selected_or_appended"] = bool(preview_normalized and preview_normalized in selected_normalized)
        point_payload["raw_point_text_candidate_debug"] = {
            "candidate_count": len(candidate_debug),
            "final_raw_point_text_length": len(raw_point_text or ""),
            "candidates": candidate_debug,
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
        "ns_version_detection": ns_version_detection,
        "report_date": report_date,
        "expected_tg_points": expected_point_ids,
        "expected_tg_points_count": len(expected_point_ids),
        "evaluated_tg_points_count": len(results),
        "llm_point_eval_calls_used": llm_calls_used,
        "llm_point_eval_calls_cap": _MAX_POINT_LEVEL_ARKAT_LLM_CALLS_PER_REPORT,
        "not_evaluated_tg_points": sorted([pid for pid in expected_point_ids if pid not in {str(item.get("point_id") or "") for item in results if isinstance(item, dict)}]),
        "points": results,
    }
