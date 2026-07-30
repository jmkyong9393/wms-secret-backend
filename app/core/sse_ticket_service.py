import json
import secrets
from typing import Any
from uuid import UUID

import redis.asyncio as redis

from app.core.config import settings


SSE_TICKET_KEY_PREFIX = "sse_ticket"


def get_sse_ticket_key(ticket: str) -> str:
    return f"{SSE_TICKET_KEY_PREFIX}:{ticket}"


# JWT 인증을 통과한 사용자에게 1회성 SSE 티켓 발급
async def issue_sse_ticket(
    job_id: UUID,
    user_id: UUID,
    tenant_id: UUID,
) -> tuple[str, int]:
    ticket = secrets.token_urlsafe(32)
    expires_in = settings.SSE_TICKET_EXPIRE_SECONDS

    redis_client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )

    payload: dict[str, Any] = {
        "job_id": str(job_id),
        "user_id": str(user_id),
        "tenant_id": str(tenant_id),
    }

    try:
        await redis_client.set(
            get_sse_ticket_key(ticket),
            json.dumps(payload),
            ex=expires_in,
        )
    finally:
        await redis_client.aclose()

    return ticket, expires_in


# 티켓을 조회함과 동시에 삭제
# 같은 티켓은 두 번 사용할 수 없음
async def consume_sse_ticket(
    ticket: str,
    job_id: UUID,
) -> dict[str, Any] | None:
    redis_client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )

    try:
        raw_payload = await redis_client.getdel(
            get_sse_ticket_key(ticket)
        )
    finally:
        await redis_client.aclose()

    if raw_payload is None:
        return None

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None

    # 티켓이 발급된 검수 작업과 요청 경로의 job_id가 같은지 확인
    if payload.get("job_id") != str(job_id):
        return None

    if payload.get("user_id") is None:
        return None
    
    if payload.get("tenant_id") is None:
        return None

    return payload