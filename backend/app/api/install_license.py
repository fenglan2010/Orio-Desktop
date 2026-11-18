"""Install-key activation for official packaged desktop builds."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.distribution import activate_install_key, install_status_payload
from app.services.first_run import initial_setup_status

router = APIRouter(prefix="/api/install", tags=["install"])


class InstallActivateBody(BaseModel):
    install_key: str = Field(min_length=8, max_length=64)


@router.get("/status")
def install_status(db: Session = Depends(get_db)):
    return {**install_status_payload(), **initial_setup_status(db)}


@router.post("/activate")
def install_activate(body: InstallActivateBody):
    try:
        return activate_install_key(body.install_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc