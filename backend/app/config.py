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
    
    # Admin credentials (simple username/password for internal use)
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin@12345"
    
    # Pinecone RAG Configuration
    PINECONE_API_KEY: str = ""
    PINECONE_ENVIRONMENT: str = "us-east-1"  # AWS region for Pinecone
    PINECONE_INDEX_NAME: str = "validert-standards"
    
    @property
    def ACTIVE_PINECONE_INDEX(self) -> str:
        """Return the active index based on which AI service is being used"""
        if self.USE_AWS_BEDROCK:
            return "validert-standards-bedrock"  # 1024 dimensions for Bedrock Titan
        return self.PINECONE_INDEX_NAME  # 1536 dimensions for OpenAI
    
    # AWS Configuration
    USE_AWS_BEDROCK: bool = True  # Set to True to use Bedrock instead of OpenAI
    USE_S3_STORAGE: bool = True  # Set to True to use S3 for PDF storage
    USE_SQS_PROCESSING: bool = False  # Set to True to use SQS + Lambda for async processing
    AWS_REGION: str = "eu-north-1"  # Bedrock region (Stockholm)
    S3_BUCKET_NAME: str = "validert-reports"
    SQS_QUEUE_URL: str = ""  # SQS queue URL for async PDF processing
    
    # Stripe Configuration
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_CURRENCY: str = "nok"  # Norwegian Krone
    
    @property
    def CORS_ORIGINS(self) -> List[str]:
        origins = [
            self.FRONTEND_URL,
            "http://localhost:3000",
            "http://13.53.164.13:3000",
            "https://www.verifisert.no",
            "https://verifisert.no",
            "http://www.verifisert.no",
            "http://verifisert.no",
            "https://admin.verifisert.no",
            "http://admin.verifisert.no"
        ]
        return origins
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()

