from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class ReportCreate(BaseModel):
    report_system: Optional[str] = None
    building_year: Optional[int] = None

class ComponentBase(BaseModel):
    component_type: str
    name: str
    condition: Optional[str] = None
    description: Optional[str] = None
    score: Optional[float] = None

class FindingBase(BaseModel):
    finding_type: str
    severity: str
    title: str
    description: str
    suggestion: Optional[str] = None
    standard_reference: Optional[str] = None

class AnalysisResult(BaseModel):
    overall_score: float = Field(..., ge=0, le=100)
    quality_score: float = Field(..., ge=0, le=100)
    completeness_score: float = Field(..., ge=0, le=100)
    compliance_score: float = Field(..., ge=0, le=100)
    components: List[ComponentBase]
    findings: List[FindingBase]
    summary: str
    recommendations: List[str]

class ReportResponse(BaseModel):
    id: int
    filename: str
    report_system: Optional[str] = None
    building_year: Optional[int] = None
    uploaded_at: datetime
    overall_score: Optional[float] = None
    quality_score: Optional[float] = None
    completeness_score: Optional[float] = None
    compliance_score: Optional[float] = None
    components: List[ComponentBase]
    findings: List[FindingBase]
    ai_analysis: Optional[dict] = None
    extracted_text: Optional[str] = None  # For verification purposes
    
    class Config:
        from_attributes = True

class UserResponse(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    picture: Optional[str] = None
    credits: int
    is_admin: Optional[int] = 0
    created_at: datetime
    
    class Config:
        from_attributes = True

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

class GoogleAuthRequest(BaseModel):
    token: str
