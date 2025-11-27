from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
from app.config import settings
from app.api.v1 import router as api_router
from app.database import engine, Base

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Note: Database tables are created via Alembic migrations
# Run: alembic upgrade head
# Or use: python init_db.py (for development)

app = FastAPI(
    title="KvalitetTakst API",
    description="API for automated quality evaluation of building condition reports",
    version="1.0.0"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router, prefix="/api/v1")

@app.get("/")
async def root():
    return {"message": "KvalitetTakst API", "version": "1.0.0"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
