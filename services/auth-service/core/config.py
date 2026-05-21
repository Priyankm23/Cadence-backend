import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 7))
    DATABASE_URL: str = os.getenv("DATABASE_URL")

    # CORS Configuration
    ALLOWED_ORIGINS_STR: str = os.getenv(
        "ALLOWED_ORIGINS",
        "https://cadence-backend-fbqh.onrender.com,http://localhost:3000,http://localhost:5173,http://localhost:8000,http://localhost:8001,http://127.0.0.1:3000,http://127.0.0.1:5173"
    )

    @property
    def ALLOWED_ORIGINS(self) -> list[str]:
        return [origin.strip() for origin in self.ALLOWED_ORIGINS_STR.split(",") if origin.strip()]

    class Config:
        case_sensitive = True

settings = Settings()
