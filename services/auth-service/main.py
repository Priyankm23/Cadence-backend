from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes import auth
from core.database import engine
import models

# Create tables if they don't exist (useful for initial dev, but we'll use Alembic)
# models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Auth Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)

@app.get("/")
def read_root():
    return {"service": "Auth Service", "status": "running"}
