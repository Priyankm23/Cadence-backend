import httpx
from fastapi import APIRouter, Request, Response
from core.config import settings

router = APIRouter()

async def proxy_request(request: Request, target_url: str):
    # Extract path after the service prefix
    # e.g., /auth/login -> /auth/login (if target is auth-service base)
    path = request.url.path
    query = request.url.query
    url = f"{target_url}{path}"
    if query:
        url += f"?{query}"

    headers = dict(request.headers)
    # Inject our internal headers
    headers["X-Request-ID"] = getattr(request.state, "request_id", "unknown")
    if getattr(request.state, "user_id", None):
        headers["X-User-ID"] = str(request.state.user_id)
    
    # Hop-by-hop headers to remove
    excluded_headers = ["host", "content-length", "transfer-encoding", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"]
    for header in excluded_headers:
        if header in headers:
            del headers[header]

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method=request.method,
                url=url,
                content=await request.body(),
                headers=headers,
                params=request.query_params,
                timeout=30.0
            )
            
            # Create a response with the content from downstream
            response = Response(
                content=resp.content,
                status_code=resp.status_code,
            )
            
            # Copy headers from downstream back to client, excluding hop-by-hop
            for name, value in resp.headers.items():
                if name.lower() not in excluded_headers:
                    response.headers.append(name, value)
                    
            return response
        except httpx.RequestError as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=502,
                content={"detail": f"Error connecting to downstream service: {str(exc)}"}
            )

@router.api_route("/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_auth(request: Request, path: str):
    return await proxy_request(request, settings.AUTH_SERVICE_URL)

@router.api_route("/meetings/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_meetings(request: Request, path: str):
    return await proxy_request(request, settings.MEETING_SERVICE_URL)

@router.api_route("/ai/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_ai(request: Request, path: str):
    return await proxy_request(request, settings.AI_SERVICE_URL)
