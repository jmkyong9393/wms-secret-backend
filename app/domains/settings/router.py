from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.core.security import RoleChecker
from app.core.settings_service import (
    DEFAULT_SAFETY_STOCK_THRESHOLD,
    SAFETY_STOCK_SETTING_KEY,
    get_int_setting,
    set_setting,
)
from app.db.session import get_db
from app.models.wms import User, UserRoleEnum

router = APIRouter(prefix="/admin/settings", tags=["System Settings"])

admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])


class SystemSettingsUpdate(BaseModel):
    safety_stock_threshold: Optional[int] = Field(
        default=None,
        ge=0,
        description="저재고 스캔 대상 선정 기준이자 발주 제안 수량의 안전재고 바닥값(권)",
    )


@router.get("", summary="서버 설정 조회")
@router.get("/", include_in_schema=False)
def get_settings(
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_only),
) -> Dict[str, Any]:
    return {
        "safety_stock_threshold": get_int_setting(
            db, SAFETY_STOCK_SETTING_KEY, DEFAULT_SAFETY_STOCK_THRESHOLD
        ),
    }


@router.put("", summary="서버 설정 변경")
@router.put("/", include_in_schema=False)
def update_settings(
    payload: SystemSettingsUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(admin_only),
) -> Dict[str, Any]:
    if payload.safety_stock_threshold is not None:
        set_setting(db, SAFETY_STOCK_SETTING_KEY, str(payload.safety_stock_threshold))

    return get_settings(db=db, current_admin=current_admin)
