import uuid
import time
from fastapi import Request, HTTPException
from jose import jwt, JWTError
from core.config import settings
from starlette.middleware.base import BaseHTTPMiddleware

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Generate Request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        # Public paths that don't require JWT
        public_paths = [
            "/auth/login",
            "/auth/register",
            "/auth/refresh",
            "/docs",
            "/openapi.json",
            "/",
            "/health"
        ]
        
        # Check if path is public
        is_public = any(request.url.path.startswith(path) for path in public_paths)
        
        user_id = None
        if not is_public:
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return await self._error_response("Missing or invalid authentication token", 401)
            
            token = auth_header.split(" ")[1]
            try:
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
                user_id = payload.get("sub")
                if payload.get("type") != "access" or user_id is None:
                    return await self._error_response("Invalid token type or missing subject", 401)
            except JWTError:
                return await self._error_response("Could not validate credentials", 401)

        # Set headers for downstream services
        request.state.user_id = user_id
        
        # We'll inject these into the headers when proxying in the router
        response = await call_next(request)
        
        # Add Request ID to outgoing response for debugging
        response.headers["X-Request-ID"] = request_id
        return response

    async def _error_response(self, message: str, status_code: int):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status_code,
            content={"detail": message}
        )
