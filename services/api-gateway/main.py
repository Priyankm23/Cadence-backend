from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.proxy import router as proxy_router
from middleware.auth import AuthMiddleware
from core.config import settings
import httpx
import asyncio

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

@app.get("/")
async def read_root():
    async def ping_service(url: str):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=5.0)
                if resp.status_code == 200:
                    return {"status": "online", "details": resp.json()}
                return {"status": f"offline/status_{resp.status_code}"}
        except Exception as e:
            return {"status": "offline/cold-starting", "error": str(e)}

    auth_task = ping_service(settings.AUTH_SERVICE_URL)
    meeting_task = ping_service(settings.MEETING_SERVICE_URL)
    
    auth_status, meeting_status = await asyncio.gather(auth_task, meeting_task)
    
    return {
        "service": "API Gateway",
        "status": "running",
        "warmup": {
            "auth_service": auth_status,
            "meeting_service": meeting_status
        }
    }


