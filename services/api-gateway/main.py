from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.proxy import router as proxy_router
from middleware.auth import AuthMiddleware

app = FastAPI(title="API Gateway")

# Global Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
