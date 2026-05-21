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
    
    # CORS Configuration
    ALLOWED_ORIGINS_STR: str = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:8000,http://127.0.0.1:3000,http://127.0.0.1:5173"
    )

    @property
    def ALLOWED_ORIGINS(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS_STR.split(",") if origin.strip()]

    class Config:
        case_sensitive = True

settings = Settings()

