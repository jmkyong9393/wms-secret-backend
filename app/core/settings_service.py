"""
서버 전역 설정(system_settings 키-값 테이블) 읽기/쓰기 헬퍼.

캐싱하지 않는다 - 설정 변경(예: 안전재고)이 다음 스캔 시점에 바로 반영돼야 하고, 이 값을 읽는 경로(저재고 스캔, 발주 제안 생성)는 트래픽이 큰 요청 경로가 아니라 매번 조회해도 성능에 영향이 없다.
"""

from typing import Optional

from sqlmodel import Session

from app.models.wms import SystemSetting, now_kst

SAFETY_STOCK_SETTING_KEY = "safety_stock_threshold"
DEFAULT_SAFETY_STOCK_THRESHOLD = 3


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.get(SystemSetting, key)
    return row.value if row else default


def get_int_setting(db: Session, key: str, default: int) -> int:
    raw = get_setting(db, key, None)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def set_setting(db: Session, key: str, value: str) -> SystemSetting:
    row = db.get(SystemSetting, key)
    if row:
        row.value = value
        row.updated_at = now_kst()
    else:
        row = SystemSetting(key=key, value=value)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
