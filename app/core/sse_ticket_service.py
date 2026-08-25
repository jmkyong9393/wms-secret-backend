"""SSE 스트림 접근용 1회성 티켓 (Redis, TTL).

쿠키를 실을 수 없는 클라이언트가 쿼리스트링으로 넘겨 인증하는 경로다.
"""

import json
import secrets
from typing import Any, Optional

import redis.asyncio as redis

from app.core.config import settings

SSE_TICKET_KEY_PREFIX = "sse_ticket"


def get_sse_ticket_key(ticket: str) -> str:
    return f"{SSE_TICKET_KEY_PREFIX}:{ticket}"


def _client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


async def issue_sse_ticket(scope: str, employee_id: str, role: str) -> tuple[str, int]:
    """인증된 사용자에게 특정 scope 전용 1회성 티켓을 발급한다."""
    ticket = secrets.token_urlsafe(32)
    expires_in = settings.SSE_TICKET_EXPIRE_SECONDS
    payload: dict[str, Any] = {"scope": scope, "employee_id": employee_id, "role": role}

    client = _client()
    try:
        await client.set(get_sse_ticket_key(ticket), json.dumps(payload), ex=expires_in)
    finally:
        await client.aclose()
    return ticket, expires_in


async def consume_sse_ticket(ticket: str, scope: str) -> Optional[dict[str, Any]]:
    """티켓을 조회와 동시에 삭제한다. 같은 티켓은 두 번 쓸 수 없다."""
    client = _client()
    try:
        raw = await client.getdel(get_sse_ticket_key(ticket))
    finally:
        await client.aclose()

    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    # 발급 대상 scope와 실제 접속하려는 스트림이 같은지 확인한다.
    if payload.get("scope") != scope:
        return None
    if not payload.get("employee_id"):
        return None
    return payload
