from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
import logging
import io
import hashlib

from app.database import get_db
from app.models import Report, Component, Finding, User, CreditTransaction
from app.schemas import ReportCreate, ReportResponse, AnalysisResult
from app.services.pdf_extractor import PDFExtractor
from app.services.ai_analyzer import (
    AIAnalyzer,
    build_analysis_result_from_output,
    build_feedback_v11,
    ensure_analysis_evidence,
    normalize_scoring_output,
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

router = APIRouter()


def _get_pipeline_cache_sha() -> Optional[str]:
    prompt_sha = get_prompt_context_sha()
    if settings.PIPELINE_GIT_SHA:
        return f"{settings.PIPELINE_GIT_SHA}:{prompt_sha}"
    return prompt_sha

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
        if (
            cache_entry
            and isinstance(cache_entry.ai_analysis, dict)
            and isinstance(cache_entry.detected_points, dict)
            and isinstance(cache_entry.scoring_result, dict)
        ):
            analysis_output = normalize_scoring_output(cache_entry.ai_analysis)
            ensure_analysis_evidence(analysis_output, extracted_text)
            scoring_result_payload = cache_entry.scoring_result
            scoring_result_payload["analysis_output"] = analysis_output
            detected_points_payload = cache_entry.detected_points
            if isinstance(scoring_result_payload, dict):
                if not isinstance(scoring_result_payload.get("feedback_v11"), dict):
                    scoring_result_payload["feedback_v11"] = build_feedback_v11(
                        analysis_output,
                        detected_points_payload or {},
                        report_id=str(report.id),
                        document_hash=document_hash,
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
                detected_points=detected_points_payload,
                scoring_result=scoring_result_payload,
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
                detected_points=report.detected_points,
                scoring_result=report.scoring_result
            )
        
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
        analysis_result, full_analysis, detected_points_payload, scoring_result_payload = ai_analyzer.analyze_report(
            text=extracted_text,
            report_system=report_system,
            building_year=building_year,
            pdf_metadata=pdf_metadata,
            document_title=file.filename,
            document_id=str(report.id),
            document_hash=document_hash,
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
            detected_points=detected_points_payload,
            scoring_result=scoring_result_payload,
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
            detected_points=report.detected_points,
            scoring_result=report.scoring_result
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
        raise HTTPException(status_code=500, detail=f"Error processing report: {str(e)}")

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

    ai_analysis_payload = report.ai_analysis
    if isinstance(ai_analysis_payload, dict):
        ensure_analysis_evidence(ai_analysis_payload, report.extracted_text or "")
    
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
        detected_points=report.detected_points,
        scoring_result=report.scoring_result,
        extracted_text=report.extracted_text
    )

@router.get("/", response_model=list[ReportResponse])
async def list_reports(
    skip: int = 0,
    limit: int = 100,
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
            detected_points=report.detected_points,
            scoring_result=report.scoring_result
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
        if isinstance(ai_analysis_payload, dict):
            ai_analysis_payload = normalize_scoring_output(ai_analysis_payload)
            if not isinstance(scoring_result_payload, dict):
                scoring_result_payload = {}
            scoring_result_payload["analysis_output"] = ai_analysis_payload
            scoring_result_payload["feedback_v11"] = build_feedback_v11(
                ai_analysis_payload,
                detected_points_payload or {},
                report_id=str(report_id),
                document_hash=document_hash,
            )
        score_total = ai_analysis_payload.get("score_total")
        report.overall_score = analysis_data.get("overall_score", score_total or 0.0)
        report.quality_score = analysis_data.get("quality_score", 0.0)
        report.completeness_score = analysis_data.get("completeness_score", 0.0)
        report.compliance_score = analysis_data.get("compliance_score", 0.0)
        report.ai_analysis = ai_analysis_payload
        if detected_points_payload is not None:
            report.detected_points = detected_points_payload
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
                detected_points=detected_points_payload,
                scoring_result=scoring_result_payload,
                ai_analysis=ai_analysis_payload,
            )
            write_run_exports(document_hash, ai_analysis_payload, detected_points_payload or {}, scoring_result_payload or {})
        
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
