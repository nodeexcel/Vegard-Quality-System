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

_ARKAT_CONDITIONAL_RE = re.compile(
    r"(?ix)\b(?:kan|dersom|hvis|risiko\s+for|kan\s+føre\s+til|kan\s+medføre|kan\s+trenge\s+inn|kan\s+oppstå)\b"
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
    r"sikkerhetsrisiko|bruksmessig|praktisk\s+betydning|for\s+kj[oø]per)\b"
)
_ARKAT_PRESENT_STATE_RE = re.compile(
    r"(?ix)\b(?:mister\s+evnen\s+til|medf[oø]rer\s+[a-zæøå]+|er\s+ikke\s+vanntett|har\s+redusert\s+tetthet|"
    r"slipper\s+ut\s+varme|gir\s+[a-zæøå]+)\b"
)
_ARKAT_TECHNICAL_DEVELOPMENT_RE = re.compile(
    r"(?ix)\b(?:fukt\s+kan\s+trekke|trekker\s+inn\s+i\s+konstruksjonen|redusert\s+tetthet|"
    r"membran(?:en)?\s+mister|dreneringen\s+svikter|r[aå]tner|lekkasje|vindsperre|b[aæ]rende\s+konstruksjon)\b"
)
_ARKAT_ACTION_RE = re.compile(
    r"(?ix)\b(?:det\s+anbefales(?:\s+[aå])?|b[oø]r\s+(?:utf[oø]res|skiftes|utbedres|kontrolleres|unders[oø]kes|"
    r"vurderes|planlegges)|planlegg|bestill|lokal\s+utbedring|utf[oø]res\s+av\s+fagperson)\b"
)
_ARKAT_AGE_ONLY_2018_RE = re.compile(
    r"(?ix)\b(?:passert\s+halvparten\s+av\s+sin\s+forventede\s+levetid|forventede\s+levetid|"
    r"alder(?:en)?\s+tilsier|er\s+fra\s+\d{4})\b"
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


def _structured_extract_arkat_fields(raw_point_text: str, extract_arkat_section_text, normalize_text) -> Dict[str, str]:
    extracted = {
        "aarsak": extract_arkat_section_text(raw_point_text, "årsak"),
        "risiko": extract_arkat_section_text(raw_point_text, "risiko"),
        "konsekvens": extract_arkat_section_text(raw_point_text, "konsekvens"),
        "anbefalt_tiltak": extract_arkat_section_text(raw_point_text, "tiltak"),
    }
    return {
        key: ("MISSING" if _is_semantically_missing_text(normalize_text, value) else str(value).strip())
        for key, value in extracted.items()
    }


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


def _heuristic_evaluate_arkat_field(field_name: str, field_text: str, ns_version: str, tg_grade: str, normalize_text) -> Dict[str, object]:
    text = normalize_text(field_text or "").strip()
    low = text.lower()
    if not text or text.upper() == "MISSING":
        return {"status": "MISSING", "explanation": ""}
    if field_name == "aarsak":
        if ns_version == "NS 3600:2018" and _ARKAT_AGE_ONLY_2018_RE.search(low) and not re.search(r"(?ix)\b(?:slitasje|svikt|skade|råte|fukt|sprek|utett|avvik)\b", low):
            return {"status": "WRONG:AARSAK_AGE_ONLY_2018", "explanation": "Årsak begrunnes bare med alder eller levetid under NS 3600:2018 uten konkret observert svikt."}
        if _ARKAT_OBSERVATION_RE.search(low) or ("tg2 vurderes da" in low and not re.search(r"(?ix)\b(?:fordi|som\s+f[øo]lge\s+av|årsaken\s+er)\b", low)):
            return {"status": "WRONG:OBSERVATION_AS_AARSAK", "explanation": "Årsak beskriver hva som er observert, ikke hvorfor forholdet har oppstått."}
        if _ARKAT_CONDITIONAL_RE.search(low):
            return {"status": "WRONG:RISK_AS_AARSAK", "explanation": "Årsak bruker risiko- eller framtidsspråk i stedet for å forklare årsaken til forholdet."}
        return {"status": "CORRECT", "explanation": ""}
    if field_name == "risiko":
        if _ARKAT_INSPECTION_LIMITATION_RE.search(low) and not _ARKAT_CONDITIONAL_RE.search(low):
            return {"status": "WRONG:LIMITATION_AS_RISIKO", "explanation": "Risiko beskriver en inspeksjonsbegrensning i stedet for hva som kan skje med bygningsdelen.", "additional_flag": "LIMITATION_USED_AS_RISK_SUBSTITUTE"}
        if _ARKAT_BUYER_IMPACT_RE.search(low) and not _ARKAT_CONDITIONAL_RE.search(low):
            return {"status": "WRONG:CONSEQUENCE_AS_RISIKO", "explanation": "Risiko beskriver praktisk eller økonomisk betydning for kjøper, ikke framtidig bygningsrisiko."}
        if _ARKAT_PRESENT_STATE_RE.search(low) and not _ARKAT_CONDITIONAL_RE.search(low):
            return {"status": "WRONG:PRESENT_STATE_AS_RISIKO", "explanation": "Risiko beskriver nåværende tilstand i stedet for mulig framtidig utvikling."}
        if _ARKAT_INSPECTION_LIMITATION_RE.search(low) and "kan være forhold" in low:
            return {"status": "WRONG:LIMITATION_AS_RISIKO", "explanation": "Risiko bruker inspeksjonsbegrensning som erstatning for faktisk bygningsrisiko.", "additional_flag": "LIMITATION_USED_AS_RISK_SUBSTITUTE"}
        return {"status": "CORRECT", "explanation": "", "additional_flag": None}
    if field_name == "konsekvens":
        if _ARKAT_TECHNICAL_DEVELOPMENT_RE.search(low) and not _ARKAT_BUYER_IMPACT_RE.search(low):
            return {"status": "WRONG:TECHNICAL_DEVELOPMENT_AS_KONSEKVENS", "explanation": "Konsekvens beskriver teknisk skadeutvikling i stedet for hva forholdet betyr for kjøper."}
        return {"status": "CORRECT", "explanation": ""}
    if field_name == "anbefalt_tiltak":
        if not _ARKAT_ACTION_RE.search(low) and ("årsaken" in low or "skyldes" in low or _ARKAT_CONDITIONAL_RE.search(low)):
            return {"status": "WRONG:EXPLANATION_AS_TILTAK", "explanation": "Anbefalt tiltak forklarer forholdet eller gjentar risikoen i stedet for å peke på et konkret neste steg."}
        if tg_grade == "TG2" and _is_semantically_missing_text(normalize_text, text):
            return {"status": "MISSING", "explanation": ""}
        return {"status": "CORRECT", "explanation": ""}
    return {"status": "MISSING", "explanation": ""}


def _normalize_arkat_eval_result(parsed: Optional[Dict[str, object]], point_id: str, tg_grade: str, extracted_fields: Dict[str, str], raw_point_text: str, ns_version: str, normalize_text) -> Dict[str, object]:
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
    default["has_errors"] = any(not str(result.get("status") or "").startswith("CORRECT") for result in default["field_results"].values() if isinstance(result, dict))
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
        if field_name == "konsekvens" and status == "WRONG:RISIKO_AS_KONSEKVENS":
            fallback = default["field_results"][field_name]
            status = str(fallback.get("status") or "CORRECT")
            explanation = str(fallback.get("explanation") or "")
        result = {"status": status, "explanation": explanation}
        if field_name == "risiko":
            result["additional_flag"] = candidate.get("additional_flag")
        normalized["field_results"][field_name] = result
        if not status.startswith("CORRECT"):
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
    if field_name == "aarsak" and "2018" in (ns_version or "") and _ARKAT_AGE_ONLY_2018_RE.search(haystack):
        return [example for example in field_examples if str(example.get("error_type") or "").strip() == "AARSAK_AGE_ONLY_2018"][:2]
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


def _evaluate_arkat_point(point_id: str, point_label: str, tg_grade: str, report_format: str, ns_version: str, raw_point_text: str, extracted_fields: Dict[str, str], normalize_text) -> Dict[str, object]:
    bundle = _get_client_arkat_bundle()
    step = bundle.get("pipeline_step") or {}
    system_prompt = str(step.get("system_prompt", {}).get("content") or "").strip()
    user_template = str(step.get("user_prompt_template", {}).get("content") or "").strip()
    if not _point_has_descriptive_text_for_arkat(raw_point_text, extracted_fields, normalize_text):
        return _normalize_arkat_eval_result(None, point_id, tg_grade, extracted_fields, raw_point_text, ns_version, normalize_text)
    if not system_prompt or not user_template:
        return _normalize_arkat_eval_result(None, point_id, tg_grade, extracted_fields, raw_point_text, ns_version, normalize_text)
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
    parsed = _call_json_llm(system_prompt, prompt, max_tokens=1400)
    return _normalize_arkat_eval_result(parsed, point_id, tg_grade, extracted_fields, raw_point_text, ns_version, normalize_text)


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
    if bridge_key in {"TECHNICAL_DEVELOPMENT_AS_KONSEKVENS", "MISSING (konsekvens)"}:
        severity = "high"
        points = 5
    elif bridge_key in {"EXPLANATION_AS_TILTAK"}:
        severity = "low"
        points = 1
    return {"bridge_key": bridge_key or status, "severity": severity, "points": points, "status": status}


def _arkat_ui_status_from_eval(result: Dict[str, object], tg_grade: str) -> str:
    status = str(result.get("status") or "").strip()
    if tg_grade == "TGIU" and status == "MISSING":
        return "not_required"
    if status == "CORRECT":
        return "present"
    if status == "MISSING":
        return "missing"
    return "unclear"


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


def _attach_arkat_component_payload(analysis_output: Dict[str, object], point_meta: Dict[str, object], evaluation: Dict[str, object], normalize_point_id) -> None:
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
    component["arkat"] = {
        "arsak": {"status": _arkat_ui_status_from_eval(field_results.get("aarsak", {}), str(point_meta.get("tg_grade") or "")), "required": str(point_meta.get("tg_grade") or "") in {"TG2", "TG3"}, "comment": str((field_results.get("aarsak") or {}).get("explanation") or "")},
        "risiko": {"status": _arkat_ui_status_from_eval(field_results.get("risiko", {}), str(point_meta.get("tg_grade") or "")), "required": str(point_meta.get("tg_grade") or "") in {"TG2", "TG3", "TGIU"}, "comment": str((field_results.get("risiko") or {}).get("explanation") or "")},
        "konsekvens": {"status": _arkat_ui_status_from_eval(field_results.get("konsekvens", {}), str(point_meta.get("tg_grade") or "")), "required": str(point_meta.get("tg_grade") or "") in {"TG2", "TG3"}, "comment": str((field_results.get("konsekvens") or {}).get("explanation") or "")},
        "anbefalt_tiltak": {"status": _arkat_ui_status_from_eval(field_results.get("anbefalt_tiltak", {}), str(point_meta.get("tg_grade") or "")), "required": str(point_meta.get("tg_grade") or "") == "TG3", "comment": str((field_results.get("anbefalt_tiltak") or {}).get("explanation") or "")},
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
    results: List[Dict[str, object]] = []
    for point in detected_points:
        if not isinstance(point, dict):
            continue
        point_id = normalize_point_id(str(point.get("point_id") or ""))
        if not point_id or is_synthetic_supplement_point_id(point_id) or bool(point.get("synthetic_supplement")):
            continue
        tg_grade = effective_point_tg(point)
        if tg_grade not in {"TG2", "TG3", "TGIU"}:
            continue
        if _point_has_real_child(point_id, detected_points, normalize_point_id, is_synthetic_supplement_point_id, is_parent_of):
            continue
        raw_point_text = str(point.get("effective_span_text") or point.get("exact_span_text") or point.get("span_text") or "").strip()
        if not raw_point_text:
            continue
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
        )
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
        _attach_arkat_component_payload(analysis_output, point_payload, evaluation, normalize_point_id)
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
    analysis_output["arkat_semantic_pipeline"] = {
        "active": True,
        "report_format": format_meta.get("report_format") or "",
        "extraction_method_used": format_meta.get("extraction_method_used") or "",
        "ns_version": ns_version,
        "report_date": report_date,
        "points": results,
    }
