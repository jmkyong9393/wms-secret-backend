import os
from typing import Any, Dict
import httpx

# 맞게 고쳐야함.
WMS_BASE_URL = os.getenv(
    "WMS_BASE_URL",
    "http://api:8000"
)

# 실제 endpoint는 WMS API 정의 확정 후 수정
# 정상 판정된 도서를 WMS  입고 처리하는 API 호출 wrapper
def call_wms_approve_api(book_id: str) -> Dict[str, Any]:
    response = httpx.post(
        f"{WMS_BASE_URL}/api/inventory/approve", #수정 필요
        json={
            "book_id": book_id,
            "reason": "AI_INSPECTION_PASSED",
        },
        timeout=10,
    )

    response.raise_for_status()
    return response.json()

# 불량 판정된 도서 처리 API 호출 wrapper
def call_wms_reject_api(book_id: str, reason: str) -> Dict[str, Any]:
    response = httpx.post(
        f"{WMS_BASE_URL}/api/inventory/reject", #수정 필요
        json={
            "book_id":book_id,
            "reason": reason,
        },
        timeout=10,
    )

    response.raise_for_status()
    return response.json()
    
    