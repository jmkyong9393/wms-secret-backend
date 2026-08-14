"""SSE 스트림 접근 인가.

EventSource는 커스텀 헤더를 못 붙이므로 쿠키(같은 오리진 기본) 또는 1회성 티켓(`?ticket=`) 두 경로로 인가한다.
"""
from typing import Optional

import jwt
from fastapi import Depends, Query, Request
from sqlmodel import Session

from app.core.config import settings
from app.core.exceptions import UnauthorizedException
from app.core.sse_ticket_service import consume_sse_ticket
from app.db.session import get_db
from app.domains.users.service import user_service
from app.models.wms import User


def stream_scope(request: Request) -> str:
    """티켓 scope 대조에 쓰는 스트림 식별자."""
    return request.url.path


async def require_stream_access(
    request: Request,
    ticket: Optional[str] = Query(default=None, description="쿠키를 쓸 수 없는 경우의 1회성 접근 티켓"),
    session: Session = Depends(get_db),
) -> User:
    """쿠키 또는 1회성 티켓으로 스트림 접근을 인가하고 사용자를 반환한다."""
    employee_id: Optional[str] = None

    token = request.cookies.get("token")
    if token:
        try:
            employee_id = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]).get("sub")
        except jwt.PyJWTError:
            employee_id = None

    if employee_id is None and ticket:
        payload = await consume_sse_ticket(ticket, scope=stream_scope(request))
        if payload:
            employee_id = payload.get("employee_id")

    if employee_id is None:
        raise UnauthorizedException("스트림 접근 권한이 없습니다.")

    user = user_service.get_user_by_employee_id(session, employee_id)
    if user is None:
        raise UnauthorizedException("스트림 접근 권한이 없습니다.")
    return user
