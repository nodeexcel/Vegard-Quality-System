from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone
import logging
import io
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from app.database import get_db
from app.models import Report, Component, Finding, User, CreditTransaction
from app.schemas import ReportCreate, ReportResponse, AnalysisResult
from app.services.pdf_extractor import PDFExtractor
from app.services.ai_analyzer import (
    AIAnalyzer,
    build_analysis_result_from_output,
    build_feedback_v11,
    ensure_analysis_evidence,
    get_validated_detected_points_payload,
    IncompleteAnalysisError,
    normalize_scoring_output,
    postprocess_analysis_output,
    sanitize_bmtf_public_point_taxonomy_payload,
    write_run_exports,
)
from app.services.analysis_cache import get_cached_analysis, upsert_analysis_cache
from app.services.validert_files import get_scoring_model_info, get_prompt_context_sha
from app.auth import get_current_user
from app.config import settings

# Import S3 storage if enabled
if settings.USE_S3_STORAGE:
    from app.services.s3_storage import S3Storage
    s3_storage = S3Storage(bucket_name=settings.S3_BUCKET_NAME)

# Import SQS processor if enabled (lazy initialization to avoid startup errors)
sqs_processor = None
if settings.USE_SQS_PROCESSING:
    from app.services.sqs_processor import SQSProcessor

logger = logging.getLogger(__name__)


def _public_bmtf_payload(payload: Optional[dict], extracted_text: str) -> Optional[dict]:
    if not isinstance(payload, dict):
        return payload
    copied = json.loads(json.dumps(payload, ensure_ascii=False))
    return sanitize_bmtf_public_point_taxonomy_payload(copied, extracted_text or "")


router = APIRouter()


def _process_report_in_background(
    *,
    report_id: int,
    user_id: int,
    filename: str,
    report_system: Optional[str],
    building_year: Optional[int],
    extracted_text: str,
    pdf_metadata: dict,
    document_hash: str,
    credits_required: int,
) -> None:
    db = SessionLocal()
    try:
        report = db.query(Report).filter(Report.id == report_id).first()
        user = db.query(User).filter(User.id == user_id).first()
        if not report:
            logger.error("Background processing aborted: report %s not found", report_id)
            return

        ai_analyzer = AIAnalyzer()
        analysis_result, full_analysis, detected_points_payload, scoring_result_payload = ai_analyzer.analyze_report(
            text=extracted_text,
            report_system=report_system,
            building_year=building_year,
            pdf_metadata=pdf_metadata,
            document_title=filename,
            document_id=str(report_id),
            document_hash=document_hash,
        )

        report.overall_score = analysis_result.overall_score
        report.quality_score = analysis_result.quality_score
        report.completeness_score = analysis_result.completeness_score
        report.compliance_score = analysis_result.compliance_score
        report.status = "completed"
        report.ai_analysis = full_analysis
        report.detected_points = detected_points_payload
        report.scoring_result = scoring_result_payload

        scoring_model_info = get_scoring_model_info()
        upsert_analysis_cache(
            db,
            document_hash=document_hash,
            scoring_model_sha=scoring_model_info.get("sha256"),
            pipeline_git_sha=_get_pipeline_cache_sha(),
            detected_points=_public_bmtf_payload(detected_points_payload, extracted_text),
            scoring_result=_public_bmtf_payload(scoring_result_payload, extracted_text),
            ai_analysis=full_analysis,
        )
        write_run_exports(document_hash, full_analysis, detected_points_payload, scoring_result_payload)

        if isinstance(full_analysis, dict):
            score_total = full_analysis.get("score_total")
            if isinstance(score_total, (int, float)) and score_total >= 96.0 and user:
                user.credits += credits_required
                db.add(CreditTransaction(
                    user_id=user.id,
                    amount=credits_required,
                    transaction_type="auto_refund",
                    description=f"Automatic refund: {credits_required} credits for report: {filename}",
                    report_id=report.id
                ))

        db.query(Component).filter(Component.report_id == report.id).delete()
        db.query(Finding).filter(Finding.report_id == report.id).delete()
        for comp_data in analysis_result.components:
            db.add(Component(
                report_id=report.id,
                component_type=comp_data.component_type,
                name=comp_data.name,
                condition=comp_data.condition,
                description=comp_data.description,
                score=comp_data.score
            ))
        for finding_data in analysis_result.findings:
            db.add(Finding(
                report_id=report.id,
                finding_type=finding_data.finding_type,
                severity=finding_data.severity,
                title=finding_data.title,
                description=finding_data.description,
                suggestion=finding_data.suggestion,
                standard_reference=finding_data.standard_reference
            ))

        db.commit()
        logger.info("Background processing completed for report %s", report_id)
    except IncompleteAnalysisError as e:
        db.rollback()
        report = db.query(Report).filter(Report.id == report_id).first()
        if report:
            report.status = "incomplete"
            report.ai_analysis = {
                "meta": {"analysis_status": "INCOMPLETE", "message": e.message, "reasons": e.reasons, "run_meta": e.run_meta}
            }
            report.detected_points = e.detected_points_payload
            report.scoring_result = None
            db.commit()
    except Exception as e:
        db.rollback()
        logger.error("Background processing failed for report %s: %s", report_id, str(e), exc_info=True)
        report = db.query(Report).filter(Report.id == report_id).first()
        if report:
            report.status = "failed"
            db.commit()
    finally:
        db.close()

_E3_P12_TEXT_RE = re.compile(
    r"(?i)(?:v[æa]r|vaer|ver)\s+oppmerksom\s+p(?:[åa]|aa)|tilleggsopplysninger|anbefalte?\s+ytterligere\s+unders"
)
_E3_P11_TEXT_RE = re.compile(
    r"(?i)lovlighet(?:\s+og\s+sikkerhet)?|godkjente\s+tegninger|byggemeldte?\s+tegninger|ferdigattest|brukstillatelse|bruksendring"
)
_VINTERHAGE_SCOPE_GAP_RE = re.compile(
    r"(?is)\bvinterhage\b.{0,220}(?:ikke\s+tilstandsvurdert|ikke\s+vurdert|ikke\s+omfattet|det\s+foreligger\s+ikke\s+tegninger|det\s+er\s+ikke\s+fremlagt\s+tegninger)"
)


def _report_needs_vinterhage_scope_note(extracted_text: str) -> bool:
    text = extracted_text or ""
    if not isinstance(text, str):
        return False
    low = text.lower()
    if "vinterhage" not in low:
        return False
    if re.search(r"(?is)\bvinterhage\b.{0,260}(det\s+foreligger\s+ikke\s+tegninger|det\s+er\s+ikke\s+fremlagt\s+tegninger)", low):
        return True
    return bool(_VINTERHAGE_SCOPE_GAP_RE.search(low) and re.search(r"(?is)\b(?:avhendingslova|avhendingsloven|ns\s*3600|ns3600)\b", low))


def _inject_vinterhage_scope_note(ai_analysis_payload: dict, feedback_v11: Optional[dict], extracted_text: str) -> None:
    if not isinstance(ai_analysis_payload, dict):
        return
    if not _report_needs_vinterhage_scope_note(extracted_text):
        return

    all_findings = ai_analysis_payload.get("all_findings")
    if not isinstance(all_findings, list):
        all_findings = []
        ai_analysis_payload["all_findings"] = all_findings

    if not any(isinstance(item, dict) and str(item.get("rule_id") or "") == "E_METHOD.vinterhage_not_assessed_ns3600" for item in all_findings):
        all_findings.append(
            {
                "finding_id": "api_vinterhage_scope_note",
                "rule_id": "E_METHOD.vinterhage_not_assessed_ns3600",
                "category": "E",
                "severity": "info",
                "deduction_band": "Ikke scoretrekk",
                "title": "Vinterhage er ikke vurdert etter NS 3600",
                "message": "Rapporten opplyser at vinterhagen ikke er omfattet av den ordinære tilstandsvurderingen. Dette er vesentlig informasjon for kjøper og bør fremgå tydelig som en faglig merknad.",
                "recommended_fix_text": "Legg inn en tydelig merknad om at vinterhagen ikke er omfattet av den ordinære tilstandsvurderingen, slik at kjøper forstår avgrensningen i rapporten.",
                "suggested_rewrite_text": "Vinterhagen er ikke omfattet av den ordinære tilstandsvurderingen i rapporten. Kjøper må derfor være oppmerksom på at denne bygningen ikke er vurdert på samme måte som hovedbygningen.",
                "evidence_snippets": ["Vinterhage • Det foreligger ikke tegninger. Det er ikke fremlagt tegninger."],
            }
        )

    if not isinstance(feedback_v11, dict):
        return
    findings = feedback_v11.get("findings")
    if not isinstance(findings, list):
        findings = []
        feedback_v11["findings"] = findings
    if not any(isinstance(item, dict) and str(item.get("rule_id") or "") == "E_METHOD.vinterhage_not_assessed_ns3600" for item in findings):
        findings.append(
            {
                "finding_id": "f-special-E_METHOD_vinterhage_not_assessed_ns3600",
                "rule_id": "E_METHOD.vinterhage_not_assessed_ns3600",
                "rule_family": "METHOD",
                "severity": "info",
                "affects_96_gate": False,
                "point_id": "",
                "arkat_section": "annet",
                "message": "Vinterhagen er ikke omfattet av den ordinære tilstandsvurderingen i rapporten.",
                "what_to_change": "Legg inn en tydelig merknad om at vinterhagen ikke er omfattet av den ordinære tilstandsvurderingen, slik at kjøper forstår avgrensningen i rapporten.",
                "example_fix": {
                    "good_example": "Vinterhagen er ikke omfattet av den ordinære tilstandsvurderingen i rapporten. Kjøper må derfor være oppmerksom på at denne bygningen ikke er vurdert på samme måte som hovedbygningen."
                },
                "evidence": {
                    "page": 1,
                    "snippet": "Vinterhage • Det foreligger ikke tegninger. Det er ikke fremlagt tegninger.",
                    "match": "Forced visible from extracted report text.",
                },
                "deduction": 0,
            }
        )


def _force_e3_parents_found_in_feedback(feedback_v11: dict, extracted_text: str) -> None:
    """
    Last-mile guard for UI consistency:
    if extracted text clearly contains E3 P11/P12 headings, never return NOT_FOUND for those parent cards.
    """
    if not isinstance(feedback_v11, dict):
        return
    points = feedback_v11.get("points_overview")
    if not isinstance(points, list):
        return
    text = extracted_text or ""
    if not isinstance(text, str):
        text = ""
    has_p12 = bool(_E3_P12_TEXT_RE.search(text))
    has_p11 = bool(_E3_P11_TEXT_RE.search(text))
    # E3: legality cues often live under supplementary/attention headings.
    if not has_p11 and has_p12 and re.search(r"(?i)tegninger|byggemeldt|ferdigattest|brukstillatelse|bruksendring", text):
        has_p11 = True
    if not (has_p11 or has_p12):
        return
    for p in points:
        if not isinstance(p, dict):
            continue
        cid = str(p.get("canonical_id") or "").upper()
        if cid == "P11_LAWFULNESS_AND_SAFETY" and has_p11:
            p["status"] = "FOUND"
            p["summary"] = "OK"
            p["tg"] = "N/A"
        if cid == "P12_SUPPLEMENTARY_INFORMATION" and has_p12:
            p["status"] = "FOUND"
            p["summary"] = "OK"
            p["tg"] = "N/A"


def _get_pipeline_cache_sha() -> Optional[str]:
    prompt_sha = get_prompt_context_sha()
    code_sha = _get_pipeline_code_fingerprint()
    if settings.PIPELINE_GIT_SHA:
        return f"{settings.PIPELINE_GIT_SHA}:{prompt_sha}:{code_sha}"
    return f"{prompt_sha}:{code_sha}"


@lru_cache(maxsize=1)
def _get_pipeline_code_fingerprint() -> str:
    """
    Cache-busting fingerprint for Python-side pipeline logic.
    This prevents stale analysis results when heuristics change without
    updating PIPELINE_GIT_SHA.
    """
    base = Path(__file__).resolve().parents[2]  # backend/app
    targets = [
        base / "services" / "arkat_semantic_pipeline.py",
        base / "services" / "ai_analyzer.py",
    ]
    h = hashlib.sha256()
    for path in targets:
        try:
            h.update(path.read_bytes())
        except OSError:
            # Missing file shouldn't break uploads; just include marker.
            h.update(f"MISSING:{path}".encode("utf-8"))
    return h.hexdigest()[:16]


def _build_report_processing_error(e: Exception) -> HTTPException:
    message = str(e)
    lowered = message.lower()

    if isinstance(e, DBAPIError) and (
        "diskfull" in lowered
        or "no space left on device" in lowered
        or "could not extend file" in lowered
    ):
        return HTTPException(
            status_code=507,
            detail=(
                "Serveren har ikke nok lagringsplass til aa fullfore analysen naa. "
                "Prov igjen senere eller kontakt support hvis feilen fortsetter."
            ),
        )

    if "aws bedrock is currently overloaded" in lowered or "throttling" in lowered:
        return HTTPException(
            status_code=503,
            detail=(
                "AI-tjenesten er midlertidig overbelastet. "
                "Prøv igjen om et par minutter."
            ),
        )

    return HTTPException(
        status_code=500,
        detail="Rapporten kunne ikke behandles. Prov igjen. Hvis feilen fortsetter, kontakt support.",
    )

@router.post("/upload", response_model=ReportResponse)
async def upload_report(
    file: UploadFile = File(...),
    report_system: Optional[str] = None,
    building_year: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Upload a PDF condition report and get automated quality analysis
    Requires authentication
    """
    try:
        # Validate file type
        if not file.filename.endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Only PDF files are allowed")
        
        # Read file content
        file_content = await file.read()
        
        # Validate file size (must be at least 100 bytes - very small PDFs are suspicious)
        if len(file_content) < 100:
            raise HTTPException(
                status_code=400, 
                detail=f"PDF file is too small ({len(file_content)} bytes). The file appears to be corrupted or incomplete. Please ensure you're uploading a complete PDF file."
            )
        
        # Check PDF magic bytes
        if not file_content.startswith(b'%PDF'):
            raise HTTPException(
                status_code=400,
                detail="The uploaded file does not appear to be a valid PDF file. PDF files must start with '%PDF' header. Please ensure you're uploading a valid PDF file."
            )
        
        file_stream = io.BytesIO(file_content)
        
        # Extract text from PDF and get metadata
        logger.info(f"Extracting text from PDF: {file.filename} (size: {len(file_content)} bytes)")
        pdf_extractor = PDFExtractor()
        
        # Get PDF metadata first
        file_stream.seek(0)
        pdf_metadata = pdf_extractor.get_pdf_metadata(file_stream)
        
        # Extract text
        file_stream.seek(0)
        extracted_text = pdf_extractor.extract_text(file_stream)
        
        if not extracted_text or len(extracted_text.strip()) < 100:
            raise HTTPException(
                status_code=400, 
                detail="Could not extract sufficient text from PDF. Please ensure the PDF contains readable text."
            )

        document_hash = hashlib.sha256(extracted_text.encode("utf-8")).hexdigest()
        
        # Check if this is a re-check (same filename already exists for this user)
        existing_report = db.query(Report).filter(
            Report.user_id == current_user.id,
            Report.filename == file.filename,
            Report.status == "completed"
        ).order_by(Report.uploaded_at.desc()).first()
        
        is_recheck = existing_report is not None
        credits_required = 2 if is_recheck else 10
        
        # Check if user has enough credits
        db.refresh(current_user)  # Refresh to get latest credit balance
        if current_user.credits < credits_required:
            raise HTTPException(
                status_code=402,  # 402 Payment Required
                detail=f"Insufficient credits. You need {credits_required} credits to {'re-check' if is_recheck else 'analyze'} this report. You currently have {current_user.credits} credits."
            )
        
        # Deduct credits
        current_user.credits -= credits_required
        
        # Create credit transaction record
        credit_transaction = CreditTransaction(
            user_id=current_user.id,
            amount=-credits_required,  # Negative for usage
            transaction_type="usage",
            description=f"{'Re-check' if is_recheck else 'First analysis'} of report: {file.filename}"
        )
        db.add(credit_transaction)
        
        # Create report record
        report = Report(
            user_id=current_user.id,
            filename=file.filename,
            report_system=report_system,
            building_year=building_year,
            extracted_text=extracted_text,
            document_hash=document_hash,
            status="processing"
        )
        db.add(report)
        db.flush()  # Get the ID

        scoring_model_info = get_scoring_model_info()
        cache_entry = get_cached_analysis(
            db,
            document_hash=document_hash,
            scoring_model_sha=scoring_model_info.get("sha256"),
            pipeline_git_sha=_get_pipeline_cache_sha(),
        )
        if cache_entry:
            updated_at = cache_entry.updated_at or cache_entry.created_at
            cache_age_s = None
            if updated_at:
                cache_age_s = int((datetime.now(timezone.utc) - updated_at).total_seconds())
            logger.info(
                "Analysis cache hit document_hash=%s cache_id=%s cache_age_s=%s",
                document_hash,
                cache_entry.id,
                cache_age_s,
            )
        if (
            cache_entry
            and isinstance(cache_entry.ai_analysis, dict)
            and isinstance(cache_entry.detected_points, dict)
            and isinstance(cache_entry.scoring_result, dict)
            and cache_entry.ai_analysis.get("meta", {}).get("analysis_status") != "INCOMPLETE"
        ):
            # Keep cache-hit path fast: do NOT run postprocess_analysis_output(), because it
            # re-runs semantic ARKAT extraction and can trigger expensive Bedrock calls.
            analysis_output = normalize_scoring_output(dict(cache_entry.ai_analysis or {}))
            scoring_result_payload = dict(cache_entry.scoring_result or {})
            scoring_result_payload["analysis_output"] = analysis_output
            # Fast cache-hit path: reuse cached validated segments and already-built feedback_v11
            # to avoid expensive semantic re-evaluation on every duplicate upload.
            detected_points_payload = cache_entry.detected_points
            if not isinstance(detected_points_payload, dict):
                detected_points_payload = get_validated_detected_points_payload(
                    extracted_text,
                    document_hash=document_hash,
                    document_title=file.filename,
                    document_id=str(report.id),
                    pdf_metadata=pdf_metadata,
                )
            # Strict cache-hit fast path:
            # Never rebuild feedback_v11 here, because rebuild may invoke expensive
            # semantic/LLM post-processing and erase cache-hit latency benefits.
            # If legacy cache rows miss feedback_v11, return without it and let
            # non-cache paths populate it on future fresh analyses.
            if isinstance(scoring_result_payload.get("feedback_v11"), dict):
                _force_e3_parents_found_in_feedback(
                    scoring_result_payload.get("feedback_v11"),
                    extracted_text or "",
                )
            analysis_result = build_analysis_result_from_output(analysis_output)

            report.overall_score = analysis_result.overall_score
            report.quality_score = analysis_result.quality_score
            report.completeness_score = analysis_result.completeness_score
            report.compliance_score = analysis_result.compliance_score
            report.status = "completed"
            report.ai_analysis = analysis_output
            report.detected_points = detected_points_payload
            report.scoring_result = scoring_result_payload

            trygghetsscore = None
            score_total = analysis_output.get("score_total") if isinstance(analysis_output, dict) else None
            if isinstance(score_total, (int, float)):
                trygghetsscore = float(score_total)
            if trygghetsscore is None:
                trygghetsscore = analysis_result.overall_score
            if trygghetsscore and trygghetsscore >= 96.0:
                refund_amount = credits_required
                current_user.credits += refund_amount
                refund_transaction = CreditTransaction(
                    user_id=current_user.id,
                    amount=refund_amount,
                    transaction_type="auto_refund",
                    description=(
                        f"Automatic refund: {refund_amount} credits for achieving "
                        f"{trygghetsscore:.1f}% trygghetsscore on report: {file.filename}"
                    ),
                    report_id=report.id
                )
                db.add(refund_transaction)
                logger.info(
                    "Auto-refunded %s credits to user %s for report %s (score: %.1f%%)",
                    refund_amount,
                    current_user.id,
                    report.id,
                    trygghetsscore,
                )

            for comp_data in analysis_result.components:
                component = Component(
                    report_id=report.id,
                    component_type=comp_data.component_type,
                    name=comp_data.name,
                    condition=comp_data.condition,
                    description=comp_data.description,
                    score=comp_data.score
                )
                db.add(component)

            for finding_data in analysis_result.findings:
                finding = Finding(
                    report_id=report.id,
                    finding_type=finding_data.finding_type,
                    severity=finding_data.severity,
                    title=finding_data.title,
                    description=finding_data.description,
                    suggestion=finding_data.suggestion,
                    standard_reference=finding_data.standard_reference
                )
                db.add(finding)

            upsert_analysis_cache(
                db,
                document_hash=document_hash,
                scoring_model_sha=scoring_model_info.get("sha256"),
                pipeline_git_sha=_get_pipeline_cache_sha(),
                detected_points=_public_bmtf_payload(detected_points_payload, extracted_text),
                scoring_result=_public_bmtf_payload(scoring_result_payload, extracted_text),
                ai_analysis=analysis_output,
            )
            write_run_exports(document_hash, analysis_output, detected_points_payload, scoring_result_payload)

            db.commit()
            db.refresh(report)
            report.components = db.query(Component).filter(Component.report_id == report.id).all()
            report.findings = db.query(Finding).filter(Finding.report_id == report.id).all()

            from app.schemas import ComponentBase, FindingBase
            components_data = [ComponentBase(
                component_type=c.component_type,
                name=c.name,
                condition=c.condition,
                description=c.description,
                score=c.score
            ) for c in report.components]
            findings_data = [FindingBase(
                finding_type=f.finding_type,
                severity=f.severity,
                title=f.title,
                description=f.description,
                suggestion=f.suggestion,
                standard_reference=f.standard_reference
            ) for f in report.findings]

            return ReportResponse(
                id=report.id,
                filename=report.filename,
                report_system=report.report_system,
                building_year=report.building_year,
                uploaded_at=report.uploaded_at,
                overall_score=report.overall_score,
                quality_score=report.quality_score,
                completeness_score=report.completeness_score,
                compliance_score=report.compliance_score,
                components=components_data,
                findings=findings_data,
                ai_analysis=report.ai_analysis,
                detected_points=_public_bmtf_payload(report.detected_points, report.extracted_text or ""),
                scoring_result=_public_bmtf_payload(report.scoring_result, report.extracted_text or ""),
                status=report.status,
                message=None,
            )
        elif cache_entry and isinstance(cache_entry.ai_analysis, dict) and cache_entry.ai_analysis.get("meta", {}).get("analysis_status") == "INCOMPLETE":
            logger.info("Cache entry marked INCOMPLETE for document_hash=%s, bypassing cache.", document_hash)
        
        # Upload to S3 if enabled
        if settings.USE_S3_STORAGE:
            try:
                file_stream.seek(0)  # Reset stream
                s3_key = s3_storage.upload_pdf(
                    file=file_stream,
                    filename=file.filename,
                    user_id=current_user.id,
                    report_id=report.id
                )
                report.s3_key = s3_key
                logger.info(f"Uploaded PDF to S3: {s3_key}")
            except Exception as s3_error:
                logger.warning(f"S3 upload failed: {str(s3_error)}, continuing without S3")
        
        # If SQS processing is enabled, send to queue and return immediately
        if settings.USE_SQS_PROCESSING and report.s3_key:
            try:
                logger.info(f"Sending report {report.id} to SQS for async processing")
                # Lazy initialize SQS processor
                global sqs_processor
                if sqs_processor is None:
                    from app.services.sqs_processor import SQSProcessor
                    sqs_processor = SQSProcessor()
                message_id = sqs_processor.send_pdf_processing_job(
                    s3_key=report.s3_key,
                    report_id=report.id,
                    user_id=current_user.id,
                    filename=file.filename,
                    report_system=report_system,
                    building_year=building_year
                )
                report.overall_score = 0.0
                report.quality_score = 0.0
                report.completeness_score = 0.0
                report.compliance_score = 0.0
                db.commit()
                
                return {
                    "id": report.id,
                    "report_id": report.id,
                    "filename": report.filename,
                    "uploaded_at": report.uploaded_at.isoformat() if report.uploaded_at else None,
                    "status": "processing",
                    "message": "Report queued for processing. Results will be available shortly.",
                    "message_id": message_id,
                    "overall_score": 0.0,
                    "quality_score": 0.0,
                    "completeness_score": 0.0,
                    "compliance_score": 0.0,
                    "components": [],
                    "findings": []
                }
            except Exception as sqs_error:
                logger.error(f"SQS processing failed: {str(sqs_error)}, falling back to sync processing")
                # Fall through to synchronous processing
        
        # Synchronous processing (original behavior)
        logger.info(f"Analyzing report {report.id} with AI")
        ai_analyzer = AIAnalyzer()
        try:
            analysis_result, full_analysis, detected_points_payload, scoring_result_payload = ai_analyzer.analyze_report(
                text=extracted_text,
                report_system=report_system,
                building_year=building_year,
                pdf_metadata=pdf_metadata,
                document_title=file.filename,
                document_id=str(report.id),
                document_hash=document_hash,
            )
        except IncompleteAnalysisError as e:
            logger.warning(
                "Analysis incomplete for report %s: %s reasons=%s",
                report.id,
                e.message,
                e.reasons,
            )
            report.overall_score = None
            report.quality_score = None
            report.completeness_score = None
            report.compliance_score = None
            report.status = "incomplete"
            report.ai_analysis = {
                "meta": {
                    "analysis_status": "INCOMPLETE",
                    "message": e.message,
                    "reasons": e.reasons,
                    "run_meta": e.run_meta,
                }
            }
            report.detected_points = e.detected_points_payload
            report.scoring_result = None
            db.commit()
            db.refresh(report)
            return ReportResponse(
                id=report.id,
                filename=report.filename,
                report_system=report.report_system,
                building_year=report.building_year,
                uploaded_at=report.uploaded_at,
                overall_score=None,
                quality_score=None,
                completeness_score=None,
                compliance_score=None,
                components=[],
                findings=[],
                ai_analysis=report.ai_analysis,
                detected_points=_public_bmtf_payload(report.detected_points, report.extracted_text or ""),
                scoring_result=None,
                status=report.status,
                message=e.message,
            )
        
        # Store analysis results
        report.overall_score = analysis_result.overall_score
        report.quality_score = analysis_result.quality_score
        report.completeness_score = analysis_result.completeness_score
        report.compliance_score = analysis_result.compliance_score
        report.status = "completed"
        # Store full analysis JSON for detailed view
        report.ai_analysis = full_analysis
        report.detected_points = detected_points_payload
        report.scoring_result = scoring_result_payload

        upsert_analysis_cache(
            db,
            document_hash=document_hash,
            scoring_model_sha=scoring_model_info.get("sha256"),
            pipeline_git_sha=_get_pipeline_cache_sha(),
            detected_points=_public_bmtf_payload(detected_points_payload, extracted_text),
            scoring_result=_public_bmtf_payload(scoring_result_payload, extracted_text),
            ai_analysis=full_analysis,
        )
        write_run_exports(document_hash, full_analysis, detected_points_payload, scoring_result_payload)
        
        # Check for automatic refund (96%+ trygghetsscore)
        # Extract score_total from full_analysis
        trygghetsscore = None
        if isinstance(full_analysis, dict):
            score_total = full_analysis.get("score_total")
            if isinstance(score_total, (int, float)):
                trygghetsscore = float(score_total)
        
        # If trygghetsscore is not found, use overall_score as fallback
        if trygghetsscore is None:
            trygghetsscore = analysis_result.overall_score
        
        # Auto-refund if score is 96% or higher
        if trygghetsscore and trygghetsscore >= 96.0:
            # Refund the credits that were just used
            refund_amount = credits_required
            current_user.credits += refund_amount
            
            # Create refund transaction
            refund_transaction = CreditTransaction(
                user_id=current_user.id,
                amount=refund_amount,
                transaction_type="auto_refund",
                description=f"Automatic refund: {refund_amount} credits for achieving {trygghetsscore:.1f}% trygghetsscore on report: {file.filename}",
                report_id=report.id
            )
            db.add(refund_transaction)
            logger.info(f"Auto-refunded {refund_amount} credits to user {current_user.id} for report {report.id} (score: {trygghetsscore:.1f}%)")
        
        # Store components
        for comp_data in analysis_result.components:
            component = Component(
                report_id=report.id,
                component_type=comp_data.component_type,
                name=comp_data.name,
                condition=comp_data.condition,
                description=comp_data.description,
                score=comp_data.score
            )
            db.add(component)
        
        # Store findings
        for finding_data in analysis_result.findings:
            finding = Finding(
                report_id=report.id,
                finding_type=finding_data.finding_type,
                severity=finding_data.severity,
                title=finding_data.title,
                description=finding_data.description,
                suggestion=finding_data.suggestion,
                standard_reference=finding_data.standard_reference
            )
            db.add(finding)
        
        db.commit()
        db.refresh(report)
        
        # Load relationships
        db.refresh(report)
        report.components = db.query(Component).filter(Component.report_id == report.id).all()
        report.findings = db.query(Finding).filter(Finding.report_id == report.id).all()
        
        logger.info(f"Successfully processed report {report.id} for user {current_user.id}")
        
        # Convert SQLAlchemy models to dicts for Pydantic validation
        from app.schemas import ComponentBase, FindingBase
        components_data = [ComponentBase(
            component_type=c.component_type,
            name=c.name,
            condition=c.condition,
            description=c.description,
            score=c.score
        ) for c in report.components]
        
        findings_data = [FindingBase(
            finding_type=f.finding_type,
            severity=f.severity,
            title=f.title,
            description=f.description,
            suggestion=f.suggestion,
            standard_reference=f.standard_reference
        ) for f in report.findings]
        
        return ReportResponse(
            id=report.id,
            filename=report.filename,
            report_system=report.report_system,
            building_year=report.building_year,
            uploaded_at=report.uploaded_at,
            overall_score=report.overall_score,
            quality_score=report.quality_score,
            completeness_score=report.completeness_score,
            compliance_score=report.compliance_score,
            components=components_data,
            findings=findings_data,
            ai_analysis=report.ai_analysis,
            detected_points=_public_bmtf_payload(report.detected_points, report.extracted_text or ""),
            scoring_result=_public_bmtf_payload(report.scoring_result, report.extracted_text or ""),
            status=report.status,
            message=None,
        )
        
    except HTTPException:
        raise
    except ValueError as e:
        # Convert ValueError (from PDF validation) to HTTPException with user-friendly message
        logger.error(f"PDF validation error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing report: {str(e)}", exc_info=True)
        db.rollback()
        # Mark report as failed if it exists
        try:
            if 'report' in locals() and report.id:
                report.status = "failed"
                db.commit()
        except:
            pass
        raise _build_report_processing_error(e)

@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a report by ID (only if it belongs to the current user)
    """
    report = db.query(Report).filter(Report.id == report_id, Report.user_id == current_user.id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    report.components = db.query(Component).filter(Component.report_id == report.id).all()
    report.findings = db.query(Finding).filter(Finding.report_id == report.id).all()
    
    # Convert SQLAlchemy models to dicts for Pydantic validation
    from app.schemas import ComponentBase, FindingBase
    components_data = [ComponentBase(
        component_type=c.component_type,
        name=c.name,
        condition=c.condition,
        description=c.description,
        score=c.score
    ) for c in report.components]
    
    findings_data = [FindingBase(
        finding_type=f.finding_type,
        severity=f.severity,
        title=f.title,
        description=f.description,
        suggestion=f.suggestion,
        standard_reference=f.standard_reference
    ) for f in report.findings]

    ai_analysis_payload = dict(report.ai_analysis) if isinstance(report.ai_analysis, dict) else report.ai_analysis
    if isinstance(ai_analysis_payload, dict):
        try:
            # Fast path: prefer persisted analysis payload and only ensure evidence fields.
            ensure_analysis_evidence(ai_analysis_payload, report.extracted_text or "")
        except Exception as e:
            logger.warning("ensure_analysis_evidence on get_report failed for report_id=%s: %s", report.id, str(e))

    scoring_result_out = dict(report.scoring_result) if isinstance(report.scoring_result, dict) else {}
    has_feedback_v11 = isinstance(scoring_result_out.get("feedback_v11"), dict)
    if isinstance(ai_analysis_payload, dict) and not has_feedback_v11:
        try:
            detected_points_payload = report.detected_points if isinstance(report.detected_points, dict) else None
            if not isinstance(detected_points_payload, dict):
                detected_points_payload = get_validated_detected_points_payload(
                    report.extracted_text or "",
                    document_hash=report.document_hash or "",
                    document_title=report.filename,
                    document_id=str(report.id),
                )
            points = detected_points_payload.get("points", []) if isinstance(detected_points_payload, dict) else []
            e3_hints = [
                p for p in points
                if isinstance(p, dict) and str(p.get("e3_parent_hint") or "").upper() in {"P11", "P12"}
            ]
            logger.info(
                "get_report rebuild report_id=%s validated_points=%s e3_parent_hints=%s",
                report.id,
                len(points),
                len(e3_hints),
            )
            scoring_result_out["feedback_v11"] = build_feedback_v11(
                ai_analysis_payload,
                detected_points_payload,
                report_id=str(report.id),
                document_hash=report.document_hash or "",
            )
            _force_e3_parents_found_in_feedback(
                scoring_result_out.get("feedback_v11") if isinstance(scoring_result_out, dict) else None,
                report.extracted_text or "",
            )
        except Exception as e:
            logger.warning("feedback_v11 rebuild failed for report_id=%s: %s", report.id, str(e))

    if isinstance(ai_analysis_payload, dict):
        try:
            _inject_vinterhage_scope_note(
                ai_analysis_payload,
                scoring_result_out.get("feedback_v11") if isinstance(scoring_result_out, dict) else None,
                report.extracted_text or "",
            )
        except Exception as e:
            logger.warning("vinterhage scope note injection failed for report_id=%s: %s", report.id, str(e))
    
    return ReportResponse(
        id=report.id,
        filename=report.filename,
        report_system=report.report_system,
        building_year=report.building_year,
        uploaded_at=report.uploaded_at,
        overall_score=report.overall_score,
        quality_score=report.quality_score,
        completeness_score=report.completeness_score,
        compliance_score=report.compliance_score,
        components=components_data,
        findings=findings_data,
        ai_analysis=ai_analysis_payload,
        detected_points=_public_bmtf_payload(report.detected_points, report.extracted_text or ""),
        scoring_result=_public_bmtf_payload(scoring_result_out, report.extracted_text or ""),
        extracted_text=report.extracted_text,
        status=report.status,
        message=None,
    )

@router.get("/", response_model=list[ReportResponse])
async def list_reports(
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List all reports for the current user
    """
    reports = db.query(Report).filter(Report.user_id == current_user.id).offset(skip).limit(limit).all()
    
    from app.schemas import ComponentBase, FindingBase
    
    result = []
    for report in reports:
        report.components = db.query(Component).filter(Component.report_id == report.id).all()
        report.findings = db.query(Finding).filter(Finding.report_id == report.id).all()
        
        components_data = [ComponentBase(
            component_type=c.component_type,
            name=c.name,
            condition=c.condition,
            description=c.description,
            score=c.score
        ) for c in report.components]
        
        findings_data = [FindingBase(
            finding_type=f.finding_type,
            severity=f.severity,
            title=f.title,
            description=f.description,
            suggestion=f.suggestion,
            standard_reference=f.standard_reference
        ) for f in report.findings]
        
        result.append(ReportResponse(
            id=report.id,
            filename=report.filename,
            report_system=report.report_system,
            building_year=report.building_year,
            uploaded_at=report.uploaded_at,
            overall_score=report.overall_score,
            quality_score=report.quality_score,
            completeness_score=report.completeness_score,
            compliance_score=report.compliance_score,
            components=components_data,
            findings=findings_data,
            ai_analysis=report.ai_analysis,
            detected_points=_public_bmtf_payload(report.detected_points, report.extracted_text or ""),
            scoring_result=_public_bmtf_payload(report.scoring_result, report.extracted_text or ""),
            status=report.status,
            message=None,
        ))
    
    return result

@router.post("/{report_id}/update-analysis")
async def update_report_analysis(
    report_id: int,
    analysis_data: dict,
    db: Session = Depends(get_db)
):
    """
    Update report with analysis results from Lambda
    Internal endpoint for Lambda callbacks
    """
    try:
        report = db.query(Report).filter(Report.id == report_id).first()
        
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        
        # Update scores (prefer explicit, fallback to v1.4 score_total)
        ai_analysis_payload = analysis_data.get("ai_analysis", {}) or {}
        detected_points_payload = analysis_data.get("detected_points")
        scoring_result_payload = analysis_data.get("scoring_result")
        document_hash = None
        if isinstance(detected_points_payload, dict):
            document_hash = detected_points_payload.get("document", {}).get("document_hash")
        # Validated segments for storage (re-validate from extracted_text when available)
        validated_detected_points = detected_points_payload
        if isinstance(ai_analysis_payload, dict) and ai_analysis_payload.get("meta", {}).get("analysis_status") == "INCOMPLETE":
            report.overall_score = None
            report.quality_score = None
            report.completeness_score = None
            report.compliance_score = None
            report.ai_analysis = ai_analysis_payload
            if detected_points_payload is not None:
                report.detected_points = detected_points_payload
            report.scoring_result = None
            report.status = "incomplete"
            db.query(Component).filter(Component.report_id == report_id).delete()
            db.query(Finding).filter(Finding.report_id == report_id).delete()
            db.commit()
            logger.info("Marked report %s as incomplete from Lambda", report_id)
            return {"status": "incomplete", "report_id": report_id}

        if isinstance(ai_analysis_payload, dict):
            if report.extracted_text:
                ai_analysis_payload = postprocess_analysis_output(ai_analysis_payload, report.extracted_text)
            else:
                ai_analysis_payload = normalize_scoring_output(ai_analysis_payload)
            if not isinstance(scoring_result_payload, dict):
                scoring_result_payload = {}
            scoring_result_payload["analysis_output"] = ai_analysis_payload
            # Hard gate: Lambda sends raw segments - we re-validate from extracted_text
            validated_detected_points = get_validated_detected_points_payload(
                report.extracted_text or "",
                document_hash=document_hash or "",
                document_title=report.filename,
                document_id=str(report_id),
            )
            scoring_result_payload["feedback_v11"] = build_feedback_v11(
                ai_analysis_payload,
                validated_detected_points,
                report_id=str(report_id),
                document_hash=document_hash,
            )
            _force_e3_parents_found_in_feedback(
                scoring_result_payload.get("feedback_v11"),
                report.extracted_text or "",
            )
        score_total = ai_analysis_payload.get("score_total")
        report.overall_score = analysis_data.get("overall_score", score_total or 0.0)
        report.quality_score = analysis_data.get("quality_score", 0.0)
        report.completeness_score = analysis_data.get("completeness_score", 0.0)
        report.compliance_score = analysis_data.get("compliance_score", 0.0)
        report.ai_analysis = ai_analysis_payload
        # Store validated segments only (never Lambda's raw payload)
        report.detected_points = validated_detected_points if report.extracted_text else detected_points_payload
        if scoring_result_payload is not None:
            report.scoring_result = scoring_result_payload
        report.status = "completed"

        if not document_hash and report.extracted_text:
            document_hash = hashlib.sha256(report.extracted_text.encode("utf-8")).hexdigest()
        if document_hash:
            report.document_hash = document_hash
            scoring_model_info = get_scoring_model_info()
            upsert_analysis_cache(
                db,
                document_hash=document_hash,
                scoring_model_sha=scoring_model_info.get("sha256"),
                pipeline_git_sha=_get_pipeline_cache_sha(),
                detected_points=_public_bmtf_payload(validated_detected_points, extracted_text),
                scoring_result=_public_bmtf_payload(scoring_result_payload, extracted_text),
                ai_analysis=ai_analysis_payload,
            )
            write_run_exports(document_hash, ai_analysis_payload, validated_detected_points or {}, scoring_result_payload or {})
        
        # Check for automatic refund (96%+ trygghetsscore)
        user = db.query(User).filter(User.id == report.user_id).first()
        if user:
            trygghetsscore = None
            if isinstance(ai_analysis_payload, dict):
                score_total = ai_analysis_payload.get("score_total")
                if isinstance(score_total, (int, float)):
                    trygghetsscore = float(score_total)

            if trygghetsscore is None:
                trygghetsscore = report.overall_score
            
            # Auto-refund if score is 96% or higher
            if trygghetsscore and trygghetsscore >= 96.0:
                # Find the usage transaction for this report
                usage_transaction = db.query(CreditTransaction).filter(
                    CreditTransaction.user_id == user.id,
                    CreditTransaction.report_id == report.id,
                    CreditTransaction.transaction_type == "usage"
                ).order_by(CreditTransaction.created_at.desc()).first()
                
                if usage_transaction:
                    refund_amount = abs(usage_transaction.amount)  # Get positive amount
                    user.credits += refund_amount
                    
                    # Create refund transaction
                    refund_transaction = CreditTransaction(
                        user_id=user.id,
                        amount=refund_amount,
                        transaction_type="auto_refund",
                        description=f"Automatic refund: {refund_amount} credits for achieving {trygghetsscore:.1f}% trygghetsscore on report: {report.filename}",
                        report_id=report.id
                    )
                    db.add(refund_transaction)
                    logger.info(f"Auto-refunded {refund_amount} credits to user {user.id} for report {report.id} (score: {trygghetsscore:.1f}%)")
        
        # Delete existing components and findings
        db.query(Component).filter(Component.report_id == report_id).delete()
        db.query(Finding).filter(Finding.report_id == report_id).delete()
        
        # Store components
        for comp_data in analysis_data.get("components", []):
            component = Component(
                report_id=report.id,
                component_type=comp_data.get("component_type", "Unknown"),
                name=comp_data.get("name", ""),
                condition=comp_data.get("condition"),
                description=comp_data.get("description"),
                score=comp_data.get("score")
            )
            db.add(component)
        
        # Store findings
        for finding_data in analysis_data.get("findings", []):
            finding = Finding(
                report_id=report.id,
                finding_type=finding_data.get("finding_type", "general"),
                severity=finding_data.get("severity", "info"),
                title=finding_data.get("title", ""),
                description=finding_data.get("description", ""),
                suggestion=finding_data.get("suggestion"),
                standard_reference=finding_data.get("standard_reference")
            )
            db.add(finding)
        
        db.commit()
        logger.info(f"Successfully updated report {report_id} from Lambda")
        
        return {"status": "success", "report_id": report_id}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating report {report_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
