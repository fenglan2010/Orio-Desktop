"""OIRO FastAPI application."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import campaigns, alerts, audit, auth, channels, chat, dashboard, diagnosis, evolution, jobs, memories, ops, ops_workbench, pms, reviews, settings, strategies, users, writes
from app.config import clear_settings_cache, validate_runtime_settings
from app.middleware.install_gate import InstallGateMiddleware
from app.api import install_license, local_config
from app.services.local_secrets import apply_local_model_keys_to_environ
from app.database import init_db, is_postgres_url
from app.schemas import HealthResponse
from app.seed import ensure_seeded
from app.jobs.runtime import job_queue_mode
from app.services.workflow import engine_info

log = logging.getLogger("oiro")

app = FastAPI(title="OIRO", description="OTA 智能收益优化官", version=__version__)

app.add_middleware(InstallGateMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
        "app://.",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(dashboard.router)
app.include_router(alerts.router)
app.include_router(diagnosis.router)
app.include_router(strategies.router)
app.include_router(campaigns.router)
app.include_router(reviews.router)
app.include_router(chat.router)
app.include_router(audit.router)
app.include_router(settings.router)
app.include_router(channels.router)
app.include_router(pms.router)
app.include_router(ops.router)
app.include_router(ops_workbench.router)
app.include_router(ops_workbench.hotel_esign_router)
app.include_router(writes.router)
app.include_router(jobs.router)
app.include_router(memories.router)
app.include_router(evolution.router)
app.include_router(install_license.router)
app.include_router(local_config.router)


@app.on_event("startup")
def _startup() -> None:
    apply_local_model_keys_to_environ()
    clear_settings_cache()
    settings = validate_runtime_settings()
    backend = "postgresql" if is_postgres_url(settings.database_url) else "sqlite"
    log.info(
        "OIRO starting env=%s database=%s (connection strings are not logged)",
        (settings.oiro_env or "development").strip() or "development",
        backend,
    )
    init_db()
    ensure_seeded()
    engine_info()  # initialize LangGraph if present


@app.get("/api/health", response_model=HealthResponse)
def health():
    info = engine_info()
    return HealthResponse(
        status="ok",
        app="OIRO",
        workflow_engine=info["engine"],
        time=datetime.now(timezone.utc),
        job_queue=job_queue_mode(),
    )


def _mount_static_frontend() -> None:
    import os
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    root = (os.environ.get("OIRO_STATIC_ROOT") or "").strip()
    if not root:
        return
    directory = Path(root)
    if directory.is_dir():
        app.mount("/", StaticFiles(directory=str(directory), html=True), name="frontend")


_mount_static_frontend()
