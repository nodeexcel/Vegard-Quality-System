from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.models import User
from app.auth import get_current_user

router = APIRouter(prefix="/profile", tags=["profile"])

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None

@router.put("/me")
async def update_profile(
    profile_data: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update user profile (name, phone, company)
    """
    if profile_data.name is not None:
        current_user.name = profile_data.name
    if profile_data.phone is not None:
        current_user.phone = profile_data.phone
    if profile_data.company is not None:
        current_user.company = profile_data.company
    
    db.commit()
    db.refresh(current_user)
    
    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "phone": current_user.phone,
        "company": current_user.company,
        "credits": current_user.credits,
        "is_admin": current_user.is_admin,
        "created_at": current_user.created_at
    }

@router.get("/me")
async def get_profile(
    current_user: User = Depends(get_current_user)
):
    """
    Get current user profile
    """
    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "phone": current_user.phone,
        "company": current_user.company,
        "credits": current_user.credits,
        "is_admin": current_user.is_admin,
        "created_at": current_user.created_at,
        "profile_complete": bool(current_user.name and current_user.phone and current_user.company)
    }

