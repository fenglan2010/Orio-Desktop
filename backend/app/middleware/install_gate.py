"""Block packaged builds until install key is activated."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.services.distribution import install_activated, is_packaged_distribution

ALLOWED_PREFIXES = (
    "/api/install",
    "/api/health",
    "/docs",
    "/openapi.json",
    "/redoc",
)


class InstallGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not is_packaged_distribution() or install_activated():
            return await call_next(request)
        path = request.url.path or ""
        if any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "正式安装包需先输入有效的应用安装密钥。源码运行模式无需密钥。",
                    "install_required": True,
                },
            )
        return await call_next(request)