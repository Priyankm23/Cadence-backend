from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings

engine = engine = create_engine(
    settings.DATABASE_URL,
    # Test connection before handing it from pool to your code
    pool_pre_ping=True,
    # Max connections kept in pool
    pool_size=10,
    # Extra connections allowed under burst load
    max_overflow=20,
    # Recycle connections older than 30 min (before Postgres kills them)
    pool_recycle=1800,
    # Wait max 30s for a free connection before raising error
    pool_timeout=30,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
