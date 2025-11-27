from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
import logging

from app.database import get_db
from app.models import Report, Component, Finding, User
from app.schemas import ReportCreate, ReportResponse, AnalysisResult
from app.services.pdf_extractor import PDFExtractor
from app.services.ai_analyzer import AIAnalyzer
from app.auth import get_current_user

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
    Requires authentication and credits
    """
    try:
        # Check if user has credits
        if current_user.credits <= 0:
            raise HTTPException(
                status_code=402,
                detail="Insufficient credits. Please purchase credits to analyze reports."
            )
        
        # Validate file type
        if not file.filename.endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Only PDF files are allowed")
        
        # Extract text from PDF
        logger.info(f"Extracting text from PDF: {file.filename}")
        pdf_extractor = PDFExtractor()
        extracted_text = pdf_extractor.extract_text(file.file)
        
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
            extracted_text=extracted_text
        )
        db.add(report)
        db.flush()  # Get the ID
        
        # Analyze with AI
        logger.info(f"Analyzing report {report.id} with AI")
        ai_analyzer = AIAnalyzer()
        analysis_result, full_analysis = ai_analyzer.analyze_report(
            text=extracted_text,
            report_system=report_system,
            building_year=building_year
        )
        
        # Store analysis results
        report.overall_score = analysis_result.overall_score
        report.quality_score = analysis_result.quality_score
        report.completeness_score = analysis_result.completeness_score
        report.compliance_score = analysis_result.compliance_score
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
        
        # Deduct credit after successful analysis
        current_user.credits -= 1
        db.commit()
        db.refresh(report)
        db.refresh(current_user)
        
        # Load relationships
        db.refresh(report)
        report.components = db.query(Component).filter(Component.report_id == report.id).all()
        report.findings = db.query(Finding).filter(Finding.report_id == report.id).all()
        
        logger.info(f"Successfully processed report {report.id} for user {current_user.id}. Credits remaining: {current_user.credits}")
        
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

