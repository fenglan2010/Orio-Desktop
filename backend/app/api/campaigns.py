from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.services.campaigns import catalog_public

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"], dependencies=[Depends(get_current_user)])


@router.get("")
def list_campaigns():
    return {
        "campaigns": catalog_public(),
        "note": "演示目录来自系统预置的渠道活动，不是爬取结果。本阶段只给出是否参加的建议，不会向 OTA 报名。",
    }
