from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    company = Column(String, nullable=True)
    google_id = Column(String, unique=True, index=True, nullable=False)
    picture = Column(String, nullable=True)
    credits = Column(Integer, default=0, nullable=False)
    is_admin = Column(Integer, default=0, nullable=False)  # 0 = regular user, 1 = admin
    status = Column(String, default="active", nullable=False)  # "active" or "disabled"
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    reports = relationship("Report", back_populates="user", cascade="all, delete-orphan")
    credit_transactions = relationship("CreditTransaction", back_populates="user", cascade="all, delete-orphan")

class Report(Base):
    __tablename__ = "reports"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String, nullable=False)
    report_system = Column(String, nullable=True)
    building_year = Column(Integer, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Analysis results
    overall_score = Column(Float, nullable=True)
    quality_score = Column(Float, nullable=True)
    completeness_score = Column(Float, nullable=True)
    compliance_score = Column(Float, nullable=True)
    
    # Extracted text from PDF
    extracted_text = Column(Text, nullable=True)
    
    # AI analysis results (stored as JSON)
    ai_analysis = Column(JSON, nullable=True)
    
    # S3 storage
    s3_key = Column(String, nullable=True)  # S3 path if using S3 storage
    
    # Status tracking
    status = Column(String, default="processing", nullable=False)  # "processing", "completed", "failed"
    
    # Relationships
    user = relationship("User", back_populates="reports")
    components = relationship("Component", back_populates="report", cascade="all, delete-orphan")
    findings = relationship("Finding", back_populates="report", cascade="all, delete-orphan")

class Component(Base):
    __tablename__ = "components"
    
    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("reports.id"), nullable=False)
    component_type = Column(String, nullable=False)  # e.g., "roof", "foundation", "walls"
    name = Column(String, nullable=False)
    condition = Column(String, nullable=True)  # e.g., "good", "fair", "poor"
    description = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    
    report = relationship("Report", back_populates="components")

class Finding(Base):
    __tablename__ = "findings"
    
    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("reports.id"), nullable=False)
    finding_type = Column(String, nullable=False)  # e.g., "missing_info", "non_compliance", "quality_issue"
    severity = Column(String, nullable=False)  # e.g., "low", "medium", "high", "critical"
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    suggestion = Column(Text, nullable=True)
    standard_reference = Column(String, nullable=True)  # e.g., "NS 3600:2018", "Forskrift til avhendingslova"
    
    report = relationship("Report", back_populates="findings")

class CreditTransaction(Base):
    __tablename__ = "credit_transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount = Column(Integer, nullable=False)  # Positive for credits added, negative for credits used
    transaction_type = Column(String, nullable=False)  # "purchase", "usage", "admin_add", "admin_remove", "refund"
    description = Column(Text, nullable=True)
    report_id = Column(Integer, ForeignKey("reports.id"), nullable=True)  # If related to a report
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user = relationship("User", back_populates="credit_transactions")
    report = relationship("Report")

