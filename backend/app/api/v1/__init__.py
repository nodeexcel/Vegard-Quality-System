from fastapi import APIRouter
from app.api.v1 import reports, auth, admin, profile

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["authentication"])
router.include_router(reports.router, prefix="/reports", tags=["reports"])
router.include_router(admin.router, tags=["admin"])  # Admin router already has /admin prefix
router.include_router(profile.router, tags=["profile"])  # Profile router already has /profile prefix

