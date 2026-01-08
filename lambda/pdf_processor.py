"""
Lambda function to process PDF analysis jobs from SQS
"""
import json
import os
import boto3
import logging
import io
import requests
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime
import re
import hashlib
import uuid

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
s3_client = boto3.client('s3')
bedrock_runtime = boto3.client('bedrock-runtime', region_name='eu-north-1')

# Environment variables
S3_BUCKET = os.environ.get('S3_BUCKET_NAME', 'validert-tilstandsrapporter')
API_ENDPOINT = os.environ.get('API_ENDPOINT', 'https://www.verifisert.no/api')
PIPELINE_GIT_SHA = os.environ.get('PIPELINE_GIT_SHA', '')

FILES_DIR = Path(__file__).resolve().parents[1] / "files"


def _read_file(filename: str) -> str:
    return (FILES_DIR / filename).read_text(encoding="utf-8").strip()


SYSTEM_PROMPT_V14 = _read_file("system_prompt_validert_v1.6.txt")
RAG_LEGAL = _read_file("rag_legal_framework_validert_v1.4.txt")
RAG_RULES = _read_file("rag_validert_rules_v1.4.txt")
RAG_LANGUAGE = _read_file("rag_language_rules_v1.6.txt")
SCORING_MODEL = _read_file("rag_scoring_model_validert_v1.6.json")
OUTPUT_SCHEMA = _read_file("output_schema_validert_v1.5.json")
OUTPUT_OVERLAY = _read_file("scoring_policy.validert_output_overlay.v1.1.json")

PROMPT_CONTEXT = "\n\n".join(
    [
        "===== RAG – JURIDISK RAMMEVERK =====\n" + RAG_LEGAL,
        "===== RAG – VALIDERT SYSTEMREGLER =====\n" + RAG_RULES,
        "===== RAG – SPRÅK- OG STRUKTURREGLER =====\n" + RAG_LANGUAGE,
        "===== SCORING MODEL =====\n" + SCORING_MODEL,
        "===== OUTPUT SCHEMA =====\n" + OUTPUT_SCHEMA,
        "===== OUTPUT OVERLAY POLICY =====\n" + OUTPUT_OVERLAY,
    ]
).strip()

PAGE_MARKER_RE = re.compile(r"\[SIDE (\d+)\]\n", re.IGNORECASE)
SUMMARY_MARKERS = ["oppsummering", "takstmannens vurdering", "summary"]
POINT_HEADER_RE = re.compile(r"^\s*(\d+(?:\.\d+){1,4})\s+(.*\S)?$")
TG_RE = re.compile(r"\bTG(?:0|1|2|3|IU)\b")


def _load_scoring_model() -> Dict[str, object]:
    try:
        payload = json.loads(SCORING_MODEL)
    except json.JSONDecodeError:
        payload = {}
    categories = payload.get("categories", [])
    category_order = [c.get("id") for c in categories if c.get("id")]
    if not category_order:
        category_order = ["A", "B", "C", "D", "E"]
    category_names = {c.get("id"): c.get("name", "") for c in categories if c.get("id")}
    mechanics = payload.get("scoring_mechanics", {}) if isinstance(payload.get("scoring_mechanics"), dict) else {}
    category_caps = mechanics.get("category_caps") or {c.get("id"): c.get("max_deduction", 0) for c in categories if c.get("id")}
    deduct_per_occurrence = mechanics.get("deduct_per_occurrence", True)
    aggregate_level = mechanics.get("aggregate_level", "bygningsdel")
    score_start = mechanics.get("score_start", payload.get("score_start", 100))
    score_floor = mechanics.get("score_floor", 0)
    score_ceiling = mechanics.get("score_ceiling", 100)
    return {
        "category_order": category_order,
        "category_names": category_names,
        "category_caps": category_caps,
        "deduct_per_occurrence": deduct_per_occurrence,
        "aggregate_level": aggregate_level,
        "score_start": score_start,
        "score_floor": score_floor,
        "score_ceiling": score_ceiling,
    }


def _infer_category_from_rule_id(rule_id: str) -> str:
    if not rule_id or "_" not in rule_id:
        return ""
    prefix = rule_id.split("_", 1)[0].upper()
    return prefix if prefix in {"A", "B", "C", "D", "E"} else ""


def _hash_evidence_span(evidence: object) -> str:
    if not evidence:
        return ""
    candidate = None
    if isinstance(evidence, list) and evidence:
        candidate = evidence[0]
    elif isinstance(evidence, dict):
        candidate = evidence
    if not isinstance(candidate, dict):
        return ""
    for key in ("snippet", "text", "span_excerpt"):
        value = candidate.get(key)
        if value:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()
    return ""


def _normalize_scoring_output(analysis_output: Dict[str, object]) -> Dict[str, object]:
    scoring_model = _load_scoring_model()
    category_caps = scoring_model["category_caps"]
    category_names = scoring_model["category_names"]
    category_order = scoring_model["category_order"]
    deduct_per_occurrence = scoring_model["deduct_per_occurrence"]
    aggregate_level = str(scoring_model.get("aggregate_level", "bygningsdel")).lower()

    seen_keys = set()
    category_totals: Dict[str, int] = {cat: 0 for cat in category_order}
    has_deductions = False

    for component in analysis_output.get("findings", []):
        component_id = component.get("component_id", "")
        deductions = component.get("deductions", [])
        if not isinstance(deductions, list):
            deductions = []
        deduped = []
        for deduction in deductions:
            if not isinstance(deduction, dict):
                continue
            rule_id = deduction.get("rule_id", "")
            if rule_id:
                if not deduct_per_occurrence:
                    unique_key = f"report::{rule_id}"
                elif aggregate_level in {"report", "global", "document", "rule"}:
                    unique_key = f"report::{rule_id}"
                elif aggregate_level in {"issue", "evidence"}:
                    evidence_hash = _hash_evidence_span(deduction.get("evidence"))
                    unique_key = f"issue::{rule_id}::{evidence_hash or component_id}"
                else:
                    unique_key = f"component::{component_id or 'unknown'}::{rule_id}"
                if unique_key in seen_keys:
                    continue
                seen_keys.add(unique_key)
            category_id = deduction.get("category_id") or _infer_category_from_rule_id(rule_id)
            if category_id:
                deduction["category_id"] = category_id
            deduped.append(deduction)
        component["deductions"] = deduped
        for deduction in deduped:
            category_id = deduction.get("category_id") or _infer_category_from_rule_id(deduction.get("rule_id", ""))
            if not category_id:
                continue
            has_deductions = True
            points = deduction.get("points", 0)
            try:
                points_value = int(points)
            except (TypeError, ValueError):
                points_value = 0
            category_totals[category_id] = category_totals.get(category_id, 0) + points_value

    if not has_deductions:
        return analysis_output

    capped_totals: Dict[str, int] = {}
    for category_id, total in category_totals.items():
        cap = category_caps.get(category_id)
        if isinstance(cap, (int, float)):
            capped_totals[category_id] = min(int(total), int(cap))
        else:
            capped_totals[category_id] = int(total)

    score_by_category = []
    for category_id in category_order:
        max_deduction = category_caps.get(category_id, 0)
        score_by_category.append(
            {
                "category_id": category_id,
                "category_name": category_names.get(category_id, ""),
                "deduction": int(capped_totals.get(category_id, 0)),
                "max_deduction": int(max_deduction) if isinstance(max_deduction, (int, float)) else 0,
            }
        )
    analysis_output["score_by_category"] = score_by_category

    total_deduction = sum(capped_totals.values())
    score_start = scoring_model["score_start"]
    score_floor = scoring_model["score_floor"]
    score_ceiling = scoring_model["score_ceiling"]
    score_total = int(max(score_floor, min(score_ceiling, score_start - total_deduction)))
    analysis_output["score_total"] = score_total
    return analysis_output


def _split_pages(report_text: str) -> List[Dict[str, str]]:
    parts = PAGE_MARKER_RE.split(report_text)
    pages = []
    for i in range(1, len(parts), 2):
        try:
            page_num = int(parts[i])
        except ValueError:
            continue
        page_text = parts[i + 1].strip()
        pages.append({"page": page_num, "text": page_text})
    return pages


def _extract_snippet(text: str, index: int, window: int = 220) -> str:
    start = max(index - window // 2, 0)
    end = min(index + window // 2, len(text))
    return text[start:end].strip()


def _get_scoring_model_info() -> Dict[str, str]:
    try:
        payload = json.loads(SCORING_MODEL)
    except json.JSONDecodeError:
        payload = {}
    return {
        "model_id": str(payload.get("model", "")),
        "version": str(payload.get("version", "")),
        "updated_at": str(payload.get("updated_at", "")),
        "sha256": hashlib.sha256(SCORING_MODEL.encode("utf-8")).hexdigest(),
    }


def _extract_detected_points(report_text: str) -> List[Dict[str, Any]]:
    pages = _split_pages(report_text)
    line_index: List[Dict[str, Any]] = []
    for page in pages:
        for line in page["text"].splitlines():
            line_index.append({"page": page["page"], "text": line})

    headings: List[Dict[str, Any]] = []
    for idx, line in enumerate(line_index):
        match = POINT_HEADER_RE.match(line["text"])
        if match:
            headings.append(
                {
                    "idx": idx,
                    "point_id": match.group(1),
                    "section_title": (match.group(2) or "").strip(),
                }
            )

    detected: List[Dict[str, Any]] = []
    for i, heading in enumerate(headings):
        start_idx = heading["idx"]
        end_idx = headings[i + 1]["idx"] if i + 1 < len(headings) else len(line_index)
        span_lines = line_index[start_idx:end_idx]
        span_text = "\n".join(item["text"] for item in span_lines).strip()
        page_start = span_lines[0]["page"] if span_lines else line_index[start_idx]["page"]
        page_end = span_lines[-1]["page"] if span_lines else page_start
        tg_match = TG_RE.search(span_text)
        section_title = heading["section_title"] or ""
        excerpt = section_title or (span_text[:200].strip() if span_text else "")
        if not excerpt:
            excerpt = f"Punkt {heading['point_id']}"
        detected.append(
            {
                "point_id": heading["point_id"],
                "title": section_title or "Ukjent",
                "page_start": page_start,
                "page_end": page_end,
                "span_hash": hashlib.sha256(span_text.encode("utf-8")).hexdigest() if span_text else "",
                "excerpt": excerpt,
                "tg": tg_match.group(0) if tg_match else "",
            }
        )
    return detected


def _build_evidence_for_component(component_id: str, component_title: str, tg: str, pages: List[Dict[str, str]]) -> Dict[str, Any]:
    search_terms = [term for term in [component_id, component_title] if term]
    for page in pages:
        page_text = page["text"]
        lower_text = page_text.lower()
        for term in search_terms:
            idx = lower_text.find(term.lower())
            if idx != -1:
                snippet = _extract_snippet(page_text, idx)
                source = "SUMMARY" if any(marker in lower_text for marker in SUMMARY_MARKERS) else "LOCAL"
                return {
                    "point_id": component_id or "",
                    "tg": tg or "",
                    "page": page["page"],
                    "heading": component_title or "",
                    "source": source,
                    "snippet": snippet,
                    "match_explain": f"Matched '{term}' on page {page['page']}.",
                }

    fallback_page = pages[0]["page"] if pages else 1
    fallback_text = pages[0]["text"] if pages else ""
    return {
        "point_id": component_id or "",
        "tg": tg or "",
        "page": fallback_page,
        "heading": component_title or "",
        "source": "LOCAL",
        "snippet": _extract_snippet(fallback_text, 0),
        "match_explain": "Fallback: no direct match for component_id/title in page text.",
    }


def _ensure_issue_evidence(analysis_output: Dict[str, Any], report_text: str) -> None:
    pages = _split_pages(report_text)
    required_keys = {"point_id", "tg", "page", "heading", "source", "snippet", "match_explain"}
    for component in analysis_output.get("findings", []):
        component_id = component.get("component_id", "")
        component_title = component.get("component_title", "")
        tg = component.get("tg", "")
        evidence_seed = _build_evidence_for_component(component_id, component_title, tg, pages)
        for issue in component.get("issues", []):
            evidence = issue.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                issue["evidence"] = [evidence_seed]
                continue
            normalized = _normalize_evidence_items(evidence)
            if normalized:
                issue["evidence"] = normalized
            else:
                issue["evidence"] = [evidence_seed]


def _ensure_driver_evidence(analysis_output: Dict[str, Any]) -> None:
    required_keys = {"point_id", "tg", "page", "heading", "source", "snippet", "match_explain"}
    issue_evidence_by_rule: Dict[str, List[Dict[str, Any]]] = {}
    for component in analysis_output.get("findings", []):
        for issue in component.get("issues", []):
            for rule_id in issue.get("rule_refs", []):
                issue_evidence_by_rule.setdefault(rule_id, []).extend(issue.get("evidence", []))

    for driver in analysis_output.get("top_score_drivers", []):
        evidence = driver.get("evidence")
        if isinstance(evidence, list) and evidence:
            normalized = _normalize_evidence_items(evidence)
            if normalized:
                driver["evidence"] = normalized
                continue
        for rule_id in driver.get("rule_refs", []):
            candidate = issue_evidence_by_rule.get(rule_id)
            if candidate:
                normalized = _normalize_evidence_items(candidate)
                driver["evidence"] = normalized or [candidate[0]]
                break
        if not driver.get("evidence"):
            for candidates in issue_evidence_by_rule.values():
                if candidates:
                    normalized = _normalize_evidence_items(candidates)
                    driver["evidence"] = normalized or [candidates[0]]
                    break
        if not driver.get("evidence"):
            driver["evidence"] = [
                {
                    "point_id": "",
                    "tg": "",
                    "page": 1,
                    "heading": "",
                    "source": "LOCAL",
                    "snippet": "",
                    "match_explain": "Fallback: no evidence available from issues.",
                }
            ]


def _normalize_evidence_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    required_keys = {"point_id", "tg", "page", "heading", "source", "snippet", "match_explain"}
    normalized: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = dict(item)
        if not candidate.get("snippet") and candidate.get("text"):
            candidate["snippet"] = candidate.get("text")
        candidate.setdefault("point_id", "")
        candidate.setdefault("tg", "")
        candidate.setdefault("heading", "")
        candidate.setdefault("source", "LOCAL")
        candidate.setdefault("match_explain", "Derived from evidence text.")
        candidate.setdefault("page", 1)
        if required_keys.issubset(candidate.keys()) and candidate.get("snippet") and candidate.get("page"):
            normalized.append(candidate)
    return normalized


def _ensure_required_arrays(analysis_output: Dict[str, Any]) -> None:
    analysis_output.setdefault("score_total", 0)
    analysis_output.setdefault("score_band", "")
    analysis_output.setdefault("score_by_category", [])
    analysis_output.setdefault("top_score_drivers", [])
    analysis_output.setdefault("findings", [])
    analysis_output.setdefault("improvements", [])
    analysis_output.setdefault("disclaimers", [])


def _ensure_meta_fields(analysis_output: Dict[str, Any]) -> None:
    meta = analysis_output.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        analysis_output["meta"] = meta
    meta.setdefault("schema_version", "1.4")
    meta.setdefault("analysis_timestamp_utc", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    meta.setdefault("document_title", "")


def extract_text_from_pdf(pdf_content: bytes) -> str:
    """
    Extract text from PDF using PyPDF2
    Note: PyPDF2 needs to be included in Lambda layer
    """
    try:
        import PyPDF2
        pdf_file = io.BytesIO(pdf_content)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        
        text_parts = []
        for page_num, page in enumerate(pdf_reader.pages, 1):
            page_text = page.extract_text() or ""
            if page_text:
                text_parts.append(f"[SIDE {page_num}]\n{page_text}")
        text = "\n\n".join(text_parts)
        
        logger.info(f"Extracted {len(text)} characters from PDF")
        return text
    except Exception as e:
        logger.error(f"PDF extraction error: {str(e)}")
        raise


def analyze_with_bedrock(text: str) -> Dict:
    """
    Analyze report text using Bedrock Claude
    """
    try:
        user_message = f"""
{PROMPT_CONTEXT}

===== TILSTANDSRAPPORT SOM SKAL ANALYSERES =====

Analyser følgende tilstandsrapport.
VIKTIG: Du må analysere HELE dokumentet. Alle sider, vedlegg og bilder må vurderes.

Rapporttekst:
{text[:30000]}

Produser KUN gyldig JSON i henhold til OUTPUT SCHEMA. Ingen tekst utenfor JSON.
"""
        
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "temperature": 0.0,
            "top_p": 1.0,
            "system": SYSTEM_PROMPT_V14,
            "messages": [
                {
                    "role": "user",
                    "content": user_message
                }
            ]
        })
        
        logger.info("Invoking Bedrock Claude...")
        response = bedrock_runtime.invoke_model(
            modelId='eu.anthropic.claude-sonnet-4-20250514-v1:0',
            body=body,
            contentType='application/json',
            accept='application/json'
        )
        
        response_body = json.loads(response['body'].read())
        content = response_body.get('content', [])
        
        if content and len(content) > 0:
            response_text = content[0].get('text', '')
            
            # Extract JSON
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1
            
            if json_start != -1 and json_end > json_start:
                json_text = response_text[json_start:json_end]
                analysis_data = json.loads(json_text)
                logger.info("Successfully analyzed report with Bedrock")
                return analysis_data
            else:
                raise ValueError("Could not find JSON in response")
        else:
            raise ValueError("No content in Bedrock response")
            
    except Exception as e:
        logger.error(f"Bedrock analysis error: {str(e)}")
        raise


def _build_components_from_v14(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    components = []
    for component in findings:
        components.append({
            "component_type": component.get("component_id", "ukjent"),
            "name": component.get("component_title", "Ukjent"),
            "condition": component.get("tg"),
            "description": component.get("location"),
            "score": None
        })
    return components


def _build_findings_from_v14(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flattened = []
    for component in findings:
        for issue in component.get("issues", []):
            flattened.append({
                "finding_type": issue.get("issue_id", "issue"),
                "severity": issue.get("severity", "medium"),
                "title": issue.get("summary", "Avvik"),
                "description": issue.get("details", ""),
                "suggestion": None,
                "standard_reference": ", ".join(issue.get("rule_refs", [])) if issue.get("rule_refs") else None
            })
    return flattened


def update_report_via_api(
    report_id: int,
    analysis_data: Dict,
    detected_points_payload: Dict,
    scoring_result_payload: Dict,
) -> bool:
    """
    Update report in database via API callback
    """
    try:
        url = f"{API_ENDPOINT}/v1/reports/{report_id}/update-analysis"
        
        score_total = analysis_data.get("score_total", 0.0)
        findings_v14 = analysis_data.get("findings", [])
        payload = {
            "overall_score": score_total,
            "quality_score": 0.0,
            "completeness_score": 0.0,
            "compliance_score": 0.0,
            "components": _build_components_from_v14(findings_v14),
            "findings": _build_findings_from_v14(findings_v14),
            "ai_analysis": analysis_data,
            "detected_points": detected_points_payload,
            "scoring_result": scoring_result_payload,
        }
        
        response = requests.post(url, json=payload, timeout=30)
        
        if response.status_code == 200:
            logger.info(f"Successfully updated report {report_id}")
            return True
        else:
            logger.error(f"API update failed: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"API callback error: {str(e)}")
        return False


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process SQS messages containing PDF analysis jobs
    
    Expected message format:
    {
        "report_id": "uuid",
        "s3_key": "path/to/pdf",
        "user_email": "user@example.com"
    }
    """
    logger.info(f"Received event with {len(event.get('Records', []))} messages")
    
    processed = 0
    failed = 0
    
    for record in event.get('Records', []):
        try:
            # Parse SQS message
            message_body = json.loads(record['body'])
            report_id = message_body['report_id']
            s3_key = message_body['s3_key']
            user_email = message_body.get('user_email', 'unknown')
            
            logger.info(f"Processing report {report_id} for user {user_email}")
            
            # Step 1: Download PDF from S3
            logger.info(f"Downloading PDF from S3: {s3_key}")
            pdf_response = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
            pdf_content = pdf_response['Body'].read()
            logger.info(f"Downloaded PDF: {len(pdf_content)} bytes")
            
            # Step 2: Extract text from PDF
            logger.info("Extracting text from PDF...")
            text = extract_text_from_pdf(pdf_content)

            if len(text.strip()) < 100:
                raise ValueError("Insufficient text extracted from PDF")

            run_id = str(uuid.uuid4())
            text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            scoring_model_info = _get_scoring_model_info()
            detected_points = _extract_detected_points(text)
            run_meta = {
                "run_id": run_id,
                "analysis_timestamp_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "model_name": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
                "temperature": 0.0,
                "top_p": 1.0,
                "seed": None,
                "text_sha256": text_sha256,
                "scoring_model": scoring_model_info,
                "pipeline_git_sha": PIPELINE_GIT_SHA,
            }
            detected_points_payload = {
                "version": "v1.0",
                "document": {
                    "document_hash": text_sha256,
                    "source_filename": f"report_{report_id}.pdf",
                    "page_count": 1,
                    "extraction": {
                        "engine": "validert-point-detector",
                        "engine_version": "1.0.0",
                        "notes": "Point headers detected via regex on extracted PDF text.",
                    },
                },
                "points": detected_points,
            }
            
            # Step 3: Analyze with Bedrock
            logger.info("Analyzing with Bedrock Claude...")
            analysis_data = analyze_with_bedrock(text)
            _ensure_meta_fields(analysis_data)
            _ensure_required_arrays(analysis_data)
            _ensure_issue_evidence(analysis_data, text)
            _ensure_driver_evidence(analysis_data)
            _normalize_scoring_output(analysis_data)
            meta = analysis_data.get("meta", {})
            if isinstance(meta, dict):
                meta.setdefault("scoring_model_id", scoring_model_info.get("model_id", ""))
                meta.setdefault("scoring_model_version", scoring_model_info.get("version", ""))
                meta.setdefault("scoring_model_updated_at", scoring_model_info.get("updated_at", ""))
                analysis_data["meta"] = meta

            scoring_result_payload = {
                "run_meta": run_meta,
                "analysis_output": analysis_data,
            }
            
            # Step 4: Update database via API
            logger.info("Updating report in database...")
            success = update_report_via_api(report_id, analysis_data, detected_points_payload, scoring_result_payload)
            
            if success:
                logger.info(f"✅ Successfully processed report {report_id}")
                processed += 1
            else:
                raise Exception("Failed to update database")
            
        except Exception as e:
            logger.error(f"❌ Failed to process record: {str(e)}")
            failed += 1
            # Continue processing other messages
            continue
    
    return {
        'statusCode': 200 if failed == 0 else 207,
        'body': json.dumps({
            'processed': processed,
            'failed': failed,
            'total': len(event.get('Records', []))
        })
    }
