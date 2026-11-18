"""Block packaged APIs until install key and first-run hotel setup are done."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.database import SessionLocal
from app.services.distribution import install_activated, is_packaged_distribution
from app.services.first_run import initial_setup_required

ALLOWED_PREFIXES = (
    "/api/install",
    "/api/first-run",
    "/api/auth/login",
    "/api/health",
    "/docs",
    "/openapi.json",
    "/redoc",
)


class SetupGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not is_packaged_distribution() or not install_activated():
            return await call_next(request)
        db = SessionLocal()
        try:
            blocked = initial_setup_required(db)
        finally:
            db.close()
        if not blocked:
            return await call_next(request)
        path = request.url.path or ""
        if any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "请先完成本机酒店开通（酒店信息、管理员账号与房型）。",
                    "initial_setup_required": True,
                },
            )
        return await call_next(request)