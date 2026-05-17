import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
    
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    # Dev SMTP
    SMTP_HOST = os.getenv("SMTP_HOST", "sandbox.smtp.mailtrap.io")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 2525))
    SMTP_USER = os.getenv("SMTP_USER", "")
    SMTP_PASS = os.getenv("SMTP_PASS", "")
    
    # Prod Resend
    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
    FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@yourdomain.com")

settings = Settings()
