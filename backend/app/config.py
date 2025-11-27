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
    
    @property
    def CORS_ORIGINS(self) -> List[str]:
        return [self.FRONTEND_URL, "http://localhost:3000", "http://localhost:3022"]
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()

