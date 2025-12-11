from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import Optional, List
from datetime import datetime, timedelta
import logging
from pydantic import BaseModel

from app.database import get_db
from app.models import User, Report, Component, Finding, CreditTransaction
from app.auth import get_current_admin, create_access_token, verify_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.schemas import ReportResponse, ComponentBase, FindingBase
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Admin login schema
class AdminLoginRequest(BaseModel):
    username: str
    password: str

# Admin login endpoint (no auth required)
@router.post("/login")
async def admin_login(request: AdminLoginRequest, db: Session = Depends(get_db)):
    """
    Admin login with username/password
    """
    if request.username != settings.ADMIN_USERNAME or request.password != settings.ADMIN_PASSWORD:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )
    
    # Find an admin user in the database to create token for
    admin_user = db.query(User).filter(User.is_admin == 1).first()
    
    if not admin_user:
        raise HTTPException(
            status_code=500,
            detail="No admin user found in database. Please create an admin user first."
        )
    
    # Create access token for admin user
    access_token = create_access_token(data={"sub": str(admin_user.id)})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": admin_user.id,
            "email": admin_user.email,
            "name": admin_user.name,
            "is_admin": admin_user.is_admin
        }
    }

# Helper to get current admin from admin token
def get_current_admin_from_token(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
    db: Session = Depends(get_db)
) -> User:
    """
    Verify admin token and return admin user
    """
    token = credentials.credentials
    payload = verify_token(token)
    
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token"
        )
    
    user_id = int(payload.get("sub"))
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user or not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Admin access required"
        )
    
    return user

# ============================================================================
# REPORTS & FEEDBACK
# ============================================================================

@router.get("/reports")
async def list_reports_admin(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    score_min: Optional[float] = Query(None, ge=0, le=100),
    score_max: Optional[float] = Query(None, ge=0, le=100),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    user_id: Optional[int] = None,
    company: Optional[str] = None,
    report_system: Optional[str] = None,
    low_score_only: Optional[bool] = Query(None),
    high_risk_only: Optional[bool] = Query(None),
    min_findings: Optional[int] = Query(None, ge=0),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_from_token)
):
    """
    Admin endpoint: List all reports with filters
    """
    query = db.query(Report)
    
    # Apply filters
    if score_min is not None:
        query = query.filter(Report.overall_score >= score_min)
    if score_max is not None:
        query = query.filter(Report.overall_score <= score_max)
    if date_from:
        try:
            date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            query = query.filter(Report.uploaded_at >= date_from_dt)
        except:
            pass
    if date_to:
        try:
            date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            query = query.filter(Report.uploaded_at <= date_to_dt)
        except:
            pass
    if user_id:
        query = query.filter(Report.user_id == user_id)
    if company:
        query = query.join(User).filter(User.company.ilike(f"%{company}%"))
    if report_system:
        query = query.filter(Report.report_system == report_system)
    if low_score_only:
        query = query.filter(Report.overall_score < 70)
    if status:
        query = query.filter(Report.status == status)
    
    # Get reports with user info
    reports = query.order_by(Report.uploaded_at.desc()).offset(skip).limit(limit).all()
    
    result = []
    for report in reports:
        user = db.query(User).filter(User.id == report.user_id).first()
        
        # Count findings
        findings_count = db.query(func.count(Finding.id)).filter(Finding.report_id == report.id).scalar() or 0
        
        # Check for high-risk findings
        high_risk_findings = db.query(func.count(Finding.id)).filter(
            Finding.report_id == report.id,
            Finding.severity.in_(["high", "critical"])
        ).scalar() or 0
        
        # Apply high-risk filter if requested
        if high_risk_only and high_risk_findings == 0:
            continue
        
        # Apply min findings filter
        if min_findings is not None and findings_count < min_findings:
            continue
        
        result.append({
            "id": report.id,
            "filename": report.filename,
            "uploaded_at": report.uploaded_at.isoformat() if report.uploaded_at else None,
            "user": {
                "id": user.id if user else None,
                "name": user.name if user else None,
                "email": user.email if user else None,
                "company": user.company if user else None,
            },
            "report_system": report.report_system,
            "building_year": report.building_year,
            "overall_score": report.overall_score,
            "findings_count": findings_count,
            "high_risk_findings": high_risk_findings,
            "status": report.status,
        })
    
    total = query.count()
    
    return {
        "reports": result,
        "total": total,
        "skip": skip,
        "limit": limit
    }

@router.get("/reports/{report_id}")
async def get_report_admin(
    report_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_from_token)
):
    """
    Admin endpoint: Get detailed report view
    """
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    user = db.query(User).filter(User.id == report.user_id).first()
    
    # Load components and findings
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
    
    # Group findings by type
    tg2_tg3_issues = [f for f in findings_data if "TG2" in f.description or "TG3" in f.description or "TG2" in f.title or "TG3" in f.title]
    ns3600_deviations = [f for f in findings_data if f.standard_reference and ("NS3600" in f.standard_reference or "NS 3600" in f.standard_reference)]
    ns3940_deviations = [f for f in findings_data if f.standard_reference and ("NS3940" in f.standard_reference or "NS 3940" in f.standard_reference)]
    risk_findings = [f for f in findings_data if f.severity in ["high", "critical"]]
    
    return {
        "id": report.id,
        "filename": report.filename,
        "uploaded_at": report.uploaded_at.isoformat() if report.uploaded_at else None,
        "user": {
            "id": user.id if user else None,
            "name": user.name if user else None,
            "email": user.email if user else None,
            "company": user.company if user else None,
        },
        "report_system": report.report_system,
        "building_year": report.building_year,
        "overall_score": report.overall_score,
        "quality_score": report.quality_score,
        "completeness_score": report.completeness_score,
        "compliance_score": report.compliance_score,
        "status": report.status,
        "components": components_data,
        "findings": findings_data,
        "tg2_tg3_issues": tg2_tg3_issues,
        "ns3600_deviations": ns3600_deviations,
        "ns3940_deviations": ns3940_deviations,
        "risk_findings": risk_findings,
        "ai_analysis": report.ai_analysis,
        "s3_key": report.s3_key,  # For PDF download link
    }

# ============================================================================
# USERS
# ============================================================================

@router.get("/users")
async def list_users_admin(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_from_token)
):
    """
    Admin endpoint: List all users
    """
    query = db.query(User)
    
    if search:
        query = query.filter(
            or_(
                User.name.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
                User.company.ilike(f"%{search}%")
            )
        )
    if status:
        query = query.filter(User.status == status)
    
    users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
    
    result = []
    for user in users:
        # Get user statistics
        total_reports = db.query(func.count(Report.id)).filter(Report.user_id == user.id).scalar() or 0
        avg_score = db.query(func.avg(Report.overall_score)).filter(
            Report.user_id == user.id,
            Report.status == "completed"
        ).scalar() or 0.0
        
        result.append({
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "company": user.company,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "credits": user.credits,
            "status": user.status,
            "total_reports": total_reports,
            "average_score": round(float(avg_score), 2) if avg_score else None,
        })
    
    total = query.count()
    
    return {
        "users": result,
        "total": total,
        "skip": skip,
        "limit": limit
    }

@router.get("/users/{user_id}")
async def get_user_admin(
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_from_token)
):
    """
    Admin endpoint: Get detailed user view
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get user reports
    reports = db.query(Report).filter(Report.user_id == user.id).order_by(Report.uploaded_at.desc()).all()
    
    # Get credit transactions
    transactions = db.query(CreditTransaction).filter(
        CreditTransaction.user_id == user.id
    ).order_by(CreditTransaction.created_at.desc()).limit(100).all()
    
    # Calculate statistics
    total_reports = len(reports)
    completed_reports = [r for r in reports if r.status == "completed"]
    avg_score = sum([r.overall_score for r in completed_reports if r.overall_score]) / len(completed_reports) if completed_reports else 0.0
    
    # Score history (last 30 reports)
    score_history = [
        {
            "date": r.uploaded_at.isoformat() if r.uploaded_at else None,
            "score": r.overall_score
        }
        for r in completed_reports[:30] if r.overall_score
    ]
    
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "company": user.company,
        "picture": user.picture,
        "credits": user.credits,
        "status": user.status,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "total_reports": total_reports,
        "average_score": round(float(avg_score), 2) if avg_score else 0.0,
        "score_history": score_history,
        "credit_transactions": [
            {
                "id": t.id,
                "amount": t.amount,
                "transaction_type": t.transaction_type,
                "description": t.description,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in transactions
        ],
        "reports": [
            {
                "id": r.id,
                "filename": r.filename,
                "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
                "overall_score": r.overall_score,
                "status": r.status,
            }
            for r in reports[:50]  # Limit to 50 most recent
        ]
    }

@router.post("/users/{user_id}/credits")
async def manage_user_credits(
    user_id: int,
    amount: int = Query(..., description="Amount to add (positive) or remove (negative)"),
    description: Optional[str] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_from_token)
):
    """
    Admin endpoint: Add or remove credits from a user
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if amount == 0:
        raise HTTPException(status_code=400, detail="Amount cannot be zero")
    
    # Update user credits
    new_balance = user.credits + amount
    if new_balance < 0:
        raise HTTPException(status_code=400, detail="Insufficient credits. Cannot go below 0.")
    
    user.credits = new_balance
    
    # Create transaction record
    transaction = CreditTransaction(
        user_id=user.id,
        amount=amount,
        transaction_type="admin_add" if amount > 0 else "admin_remove",
        description=description or f"Admin adjustment by {current_admin.email}"
    )
    db.add(transaction)
    db.commit()
    db.refresh(user)
    db.refresh(transaction)
    
    return {
        "success": True,
        "user_id": user.id,
        "new_balance": user.credits,
        "transaction": {
            "id": transaction.id,
            "amount": amount,
            "transaction_type": transaction.transaction_type,
            "description": transaction.description,
        }
    }

@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_from_token)
):
    """
    Admin endpoint: Disable a user account
    """
    if user_id == current_admin.id:
        raise HTTPException(status_code=400, detail="Cannot disable your own account")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.status = "disabled"
    db.commit()
    db.refresh(user)
    
    return {
        "success": True,
        "user_id": user.id,
        "status": user.status
    }

@router.post("/users/{user_id}/enable")
async def enable_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_from_token)
):
    """
    Admin endpoint: Enable a user account
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.status = "active"
    db.commit()
    db.refresh(user)
    
    return {
        "success": True,
        "user_id": user.id,
        "status": user.status
    }

# ============================================================================
# CREDITS & BILLING
# ============================================================================

@router.get("/credits")
async def list_credits_admin(
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_from_token)
):
    """
    Admin endpoint: List all users with credit balances
    """
    query = db.query(User)
    
    if search:
        query = query.filter(
            or_(
                User.name.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%")
            )
        )
    
    users = query.order_by(User.credits.desc()).all()
    
    result = []
    for user in users:
        result.append({
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "company": user.company,
            "credits": user.credits,
        })
    
    return {
        "users": result,
        "total_users": len(result),
        "total_credits": sum([u.credits for u in users])
    }

@router.get("/credits/transactions")
async def list_credit_transactions(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    user_id: Optional[int] = None,
    transaction_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_from_token)
):
    """
    Admin endpoint: List credit transactions
    """
    query = db.query(CreditTransaction)
    
    if user_id:
        query = query.filter(CreditTransaction.user_id == user_id)
    if transaction_type:
        query = query.filter(CreditTransaction.transaction_type == transaction_type)
    
    transactions = query.order_by(CreditTransaction.created_at.desc()).offset(skip).limit(limit).all()
    
    result = []
    for t in transactions:
        user = db.query(User).filter(User.id == t.user_id).first()
        result.append({
            "id": t.id,
            "user": {
                "id": user.id if user else None,
                "name": user.name if user else None,
                "email": user.email if user else None,
            },
            "amount": t.amount,
            "transaction_type": t.transaction_type,
            "description": t.description,
            "report_id": t.report_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    
    total = query.count()
    
    return {
        "transactions": result,
        "total": total,
        "skip": skip,
        "limit": limit
    }

# ============================================================================
# SYSTEM & INSIGHTS
# ============================================================================

@router.get("/system/status")
async def get_system_status(
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_from_token)
):
    """
    Admin endpoint: Get system status
    """
    import boto3
    from app.config import settings
    
    status = {
        "timestamp": datetime.utcnow().isoformat(),
        "aws_bedrock": {
            "enabled": settings.USE_AWS_BEDROCK,
            "region": settings.AWS_REGION,
            "status": "unknown"
        },
        "s3_storage": {
            "enabled": settings.USE_S3_STORAGE,
            "bucket": settings.S3_BUCKET_NAME,
            "status": "unknown"
        },
        "sqs_processing": {
            "enabled": settings.USE_SQS_PROCESSING,
            "status": "unknown"
        },
        "database": {
            "status": "unknown"
        }
    }
    
    # Check Bedrock
    if settings.USE_AWS_BEDROCK:
        try:
            bedrock = boto3.client('bedrock', region_name=settings.AWS_REGION)
            bedrock.list_foundation_models()
            status["aws_bedrock"]["status"] = "operational"
        except Exception as e:
            status["aws_bedrock"]["status"] = f"error: {str(e)[:100]}"
    
    # Check S3
    if settings.USE_S3_STORAGE:
        try:
            s3 = boto3.client('s3', region_name=settings.AWS_REGION)
            s3.head_bucket(Bucket=settings.S3_BUCKET_NAME)
            status["s3_storage"]["status"] = "operational"
        except Exception as e:
            status["s3_storage"]["status"] = f"error: {str(e)[:100]}"
    
    # Check database
    try:
        db.execute("SELECT 1")
        status["database"]["status"] = "operational"
    except Exception as e:
        status["database"]["status"] = f"error: {str(e)[:100]}"
    
    # Get failed reports count
    failed_reports = db.query(func.count(Report.id)).filter(Report.status == "failed").scalar() or 0
    status["failed_reports"] = failed_reports
    
    return status

@router.get("/analytics")
async def get_analytics(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_from_token)
):
    """
    Admin endpoint: Get analytics and insights
    """
    date_from = datetime.utcnow() - timedelta(days=days)
    
    # Score distribution
    completed_reports = db.query(Report).filter(
        Report.status == "completed",
        Report.uploaded_at >= date_from
    ).all()
    
    scores = [r.overall_score for r in completed_reports if r.overall_score]
    score_distribution = {
        "0-20": len([s for s in scores if 0 <= s < 20]),
        "20-40": len([s for s in scores if 20 <= s < 40]),
        "40-60": len([s for s in scores if 40 <= s < 60]),
        "60-80": len([s for s in scores if 60 <= s < 80]),
        "80-100": len([s for s in scores if 80 <= s <= 100]),
    }
    
    # Most common findings
    all_findings = db.query(Finding).join(Report).filter(
        Report.status == "completed",
        Report.uploaded_at >= date_from
    ).all()
    
    finding_types = {}
    for f in all_findings:
        key = f.finding_type
        finding_types[key] = finding_types.get(key, 0) + 1
    
    # Most common standard references
    standard_refs = {}
    for f in all_findings:
        if f.standard_reference:
            ref = f.standard_reference.split()[0] if f.standard_reference else "Unknown"
            standard_refs[ref] = standard_refs.get(ref, 0) + 1
    
    # Users with lowest average score
    user_scores = db.query(
        User.id,
        User.name,
        User.email,
        func.avg(Report.overall_score).label('avg_score'),
        func.count(Report.id).label('report_count')
    ).join(Report).filter(
        Report.status == "completed",
        Report.uploaded_at >= date_from
    ).group_by(User.id).order_by('avg_score').limit(10).all()
    
    lowest_score_users = [
        {
            "user_id": u.id,
            "name": u.name,
            "email": u.email,
            "average_score": round(float(u.avg_score), 2) if u.avg_score else 0,
            "report_count": u.report_count
        }
        for u in user_scores
    ]
    
    # Most active users
    active_users = db.query(
        User.id,
        User.name,
        User.email,
        func.count(Report.id).label('report_count')
    ).join(Report).filter(
        Report.uploaded_at >= date_from
    ).group_by(User.id).order_by('report_count').limit(10).all()
    
    most_active_users = [
        {
            "user_id": u.id,
            "name": u.name,
            "email": u.email,
            "report_count": u.report_count
        }
        for u in active_users
    ]
    
    return {
        "period_days": days,
        "total_reports": len(completed_reports),
        "score_distribution": score_distribution,
        "average_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "most_common_findings": dict(sorted(finding_types.items(), key=lambda x: x[1], reverse=True)[:10]),
        "most_common_standards": dict(sorted(standard_refs.items(), key=lambda x: x[1], reverse=True)[:10]),
        "lowest_score_users": lowest_score_users,
        "most_active_users": most_active_users,
    }

