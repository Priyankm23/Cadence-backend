from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.proxy import router as proxy_router
from middleware.auth import AuthMiddleware
from core.config import settings

app = FastAPI(title="API Gateway")

# Production-grade CORS Configuration
# Supporting both local development, custom domains, and Vercel dynamic preview deployments
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",  # Matches Vercel dynamic preview deployments
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)

# Proxy Routes
app.include_router(proxy_router)

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "api-gateway"}


