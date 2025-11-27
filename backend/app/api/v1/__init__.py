from fastapi import APIRouter
from app.api.v1 import reports, auth

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["authentication"])
router.include_router(reports.router, prefix="/reports", tags=["reports"])

