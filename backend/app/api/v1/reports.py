from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
import logging
import io

from app.database import get_db
from app.models import Report, Component, Finding, User
from app.schemas import ReportCreate, ReportResponse, AnalysisResult
from app.services.pdf_extractor import PDFExtractor
from app.services.ai_analyzer import AIAnalyzer
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
        file_stream = io.BytesIO(file_content)
        
        # Extract text from PDF and get metadata
        logger.info(f"Extracting text from PDF: {file.filename}")
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
        
        # Create report record
        report = Report(
            user_id=current_user.id,
            filename=file.filename,
            report_system=report_system,
            building_year=building_year,
            extracted_text=extracted_text,
            status="processing"
        )
        db.add(report)
        db.flush()  # Get the ID
        
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
        analysis_result, full_analysis = ai_analyzer.analyze_report(
            text=extracted_text,
            report_system=report_system,
            building_year=building_year,
            pdf_metadata=pdf_metadata
        )
        
        # Store analysis results
        report.overall_score = analysis_result.overall_score
        report.quality_score = analysis_result.quality_score
        report.completeness_score = analysis_result.completeness_score
        report.compliance_score = analysis_result.compliance_score
        report.status = "completed"
        # Store full analysis JSON for detailed view
        report.ai_analysis = full_analysis
        
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
            ai_analysis=report.ai_analysis
        )
        
    except HTTPException:
        raise
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
            ai_analysis=report.ai_analysis
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
        
        # Update scores
        report.overall_score = analysis_data.get("overall_score", 0.0)
        report.quality_score = analysis_data.get("quality_score", 0.0)
        report.completeness_score = analysis_data.get("completeness_score", 0.0)
        report.compliance_score = analysis_data.get("compliance_score", 0.0)
        report.ai_analysis = analysis_data.get("ai_analysis", {})
        report.status = "completed"
        
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

