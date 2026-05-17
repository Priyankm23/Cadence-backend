import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    
    # Service URLs
    AUTH_SERVICE_URL: str = os.getenv("AUTH_SERVICE_URL", "http://localhost:8001")
    MEETING_SERVICE_URL: str = os.getenv("MEETING_SERVICE_URL", "http://localhost:8002")
    AI_SERVICE_URL: str = os.getenv("AI_SERVICE_URL", "http://ai-service:8004")
    
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")

    class Config:
        case_sensitive = True

settings = Settings()
