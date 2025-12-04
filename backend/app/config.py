from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    DATABASE_URL: str
    OPENAI_API_KEY: str
    SECRET_KEY: str
    ENVIRONMENT: str = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    FRONTEND_URL: str = "http://localhost:3000"
    
    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    
    # Pinecone RAG Configuration
    PINECONE_API_KEY: str = ""
    PINECONE_ENVIRONMENT: str = "us-east-1"  # AWS region for Pinecone
    PINECONE_INDEX_NAME: str = "validert-standards"
    
    @property
    def CORS_ORIGINS(self) -> List[str]:
        origins = [
            self.FRONTEND_URL,
            "http://localhost:3000",
            "http://13.53.164.13:3000",
            "https://www.validert.no",
            "https://validert.no",
            "http://www.validert.no",
            "http://validert.no"
        ]
        return origins
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()

