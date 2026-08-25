import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.db.session import get_db
from app.core.security import get_current_user
from app.models.wms import Notification, now_kst
from app.core.stream_auth import require_stream_access
from app.models.wms import User

logger = logging.getLogger(__name__)

# 라우터 전체에 인증을 건다. 엔드포인트마다 붙이면 새 경로를 추가할 때 또 빠뜨린다 -
# 실제로 재고·피킹지시서·발주제안이 무인증으로 조회되던 것을 전수 점검에서 발견했다.
# 알림은 로그인 필수
router = APIRouter(prefix="/notifications", tags=["Notifications"],
                   dependencies=[Depends(get_current_user)])

# 전역 알림 브로드캐스트 채널 (job 단위가 아닌 대시보드 전역 알림용)
NOTIFICATIONS_CHANNEL = "notifications:global"

# 이벤트가 없을 때만 보내는 생존 신호 주기(초).
# 작업이 흐르는 동안에는 0건이다 - get_message()가 메시지를 받으면 즉시 반환한다.
# 주기를 더 늘리지 못하는 이유는 ALB·nginx의 기본 유휴 타임아웃이 60초이기
# 때문이다. 그보다 길면 프록시가 먼저 연결을 끊어 재연결 비용이 더 크게 든다.
HEARTBEAT_INTERVAL_SEC = 25


@router.get("", summary="알림 목록 조회 (최신순)")
@router.get("/", include_in_schema=False)
def list_notifications(
    limit: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    role: Optional[str] = Query(None, description="조회자 역할. 해당 역할 전용 알림 + 전체 공개 알림만 반환"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    저장된 알림 이력을 최신순으로 반환한다.

    [수정 이력] 종전에는 이 엔드포인트 자체가 없어서, 프론트 Header.tsx가 더미 4건을
    useState 초기값으로 하드코딩하고 SSE로 들어온 것만 메모리에 얹었다. 새로고침하면
    실시간 알림은 사라지고 더미만 남았다.
    """
    from app.domains.notifications.service import to_payload

    stmt = select(Notification)
    if unread_only:
        stmt = stmt.where(Notification.is_read == False)  # noqa: E712
    if role:
        # target_role이 비어 있으면 전체 공개 알림이다.
        stmt = stmt.where(
            (Notification.target_role == None) | (Notification.target_role == role)  # noqa: E711
        )

    rows = db.exec(stmt.order_by(Notification.created_at.desc()).limit(limit)).all()

    unread_stmt = select(Notification).where(Notification.is_read == False)  # noqa: E712
    if role:
        unread_stmt = unread_stmt.where(
            (Notification.target_role == None) | (Notification.target_role == role)  # noqa: E711
        )
    unread_count = len(db.exec(unread_stmt).all())

    return {
        "items": [to_payload(n) for n in rows],
        "unread_count": unread_count,
    }


@router.post("/{notification_id}/read", summary="알림 1건 읽음 처리")
def mark_notification_read(notification_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from uuid import UUID
    from app.core.exceptions import BadRequestException, NotFoundException

    try:
        parsed = UUID(notification_id)
    except ValueError:
        raise BadRequestException(f"잘못된 알림 ID 형식입니다: {notification_id}")

    n = db.get(Notification, parsed)
    if not n:
        raise NotFoundException(f"알림을 찾을 수 없습니다: {notification_id}")

    if not n.is_read:
        n.is_read = True
        n.read_at = now_kst()
        db.add(n)
        db.commit()

    return {"status": "success", "id": str(n.id), "is_read": True}


@router.post("/read-all", summary="전체 알림 읽음 처리")
def mark_all_notifications_read(
    role: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    stmt = select(Notification).where(Notification.is_read == False)  # noqa: E712
    if role:
        stmt = stmt.where(
            (Notification.target_role == None) | (Notification.target_role == role)  # noqa: E711
        )

    rows = db.exec(stmt).all()
    for n in rows:
        n.is_read = True
        n.read_at = now_kst()
        db.add(n)
    db.commit()

    return {"status": "success", "updated": len(rows)}


@router.delete("", summary="알림 전체 삭제 (일괄 정리)")
@router.delete("/", include_in_schema=False)
def clear_all_notifications(
    role: Optional[str] = Query(None, description="조회자 역할. 해당 역할 전용 알림 + 전체 공개 알림만 삭제"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    [2026-08-06 신설] 알림 이력 일괄 삭제. "모두 읽음"은 배지만 끄고 목록은 계속 쌓이므로,
    시연/운영 중 수백 건이 누적되면 정리할 수단이 없었다. 목록 조회(GET)와 동일한 role
    필터를 적용해, 다른 역할 전용 알림까지 지우지 않는다.
    """
    stmt = select(Notification)
    if role:
        stmt = stmt.where(
            (Notification.target_role == None) | (Notification.target_role == role)  # noqa: E711
        )

    rows = db.exec(stmt).all()
    for n in rows:
        db.delete(n)
    db.commit()

    return {"status": "success", "deleted": len(rows)}


@router.get("/stream", summary="실시간 WMS 전역 알림 SSE 스트리밍")
async def stream_notifications(request: Request, _user: User = Depends(require_stream_access)):
    """
    실시간 WMS 알림 SSE (Server-Sent Events) 스트리밍 엔드포인트.
    Redis Pub/Sub(notifications:global 채널)을 구독하여, POST /trigger-fds 등으로
    발행된 이벤트를 그대로 중계한다. (app/domains/inbound/router.py의 SSE 패턴과 동일하게
    구현 - sse_starlette 등 별도 의존성 없이 표준 StreamingResponse만 사용)
    """
    async def event_generator():
        import redis.asyncio as aioredis
        from app.core.redis_pubsub import REDIS_URL

        r = aioredis.Redis.from_url(REDIS_URL, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(NOTIFICATIONS_CHANNEL)

        try:
            yield f"data: {json.dumps({'type': 'CONNECTED', 'message': '실시간 알림 채널에 연결되었습니다.'}, ensure_ascii=False)}\n\n"

            # 이벤트가 없어도 주기적으로 신호를 보낸다. 두 가지를 해결한다.
            # ① 중간 프록시(ingress/ALB/dev 서버)가 백엔드로 가는 연결만 끊어지면
            #   브라우저 쪽 소켓은 열린 채 남아 onerror가 오지 않는다. 클라이언트는
            #   이 신호가 끊기는 것으로 죽은 연결을 판별한다(2026-08-26 실측).
            # ② listen()은 메시지가 올 때까지 블로킹되어 그 사이 is_disconnected()가
            #   실행되지 않았다. 떠난 클라이언트의 pubsub이 계속 남는다.
            while True:
                if await request.is_disconnected():
                    logger.info("SSE Client disconnected")
                    break

                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=HEARTBEAT_INTERVAL_SEC
                )
                if message is None:
                    yield f"data: {json.dumps({'type': 'HEARTBEAT'})}\n\n"
                    continue
                if message["type"] != "message":
                    continue
                yield f"data: {message['data']}\n\n"
        except Exception as e:
            logger.error(f"SSE stream error: {e}")
        finally:
            await pubsub.unsubscribe()
            await r.close()

    # 중간 계층이 스트림을 건드리지 못하게 막는다.
    # no-transform: 프록시의 gzip 재압축을 금지한다. 압축이 걸리면 30바이트짜리
    #   프레임이 버퍼에 갇혀 브라우저까지 흐르지 않는다(2026-08-26 실측 - Next rewrite가
    #   압축해 CONNECTED조차 도달하지 못했다).
    # X-Accel-Buffering: nginx 계열 프록시의 응답 버퍼링을 끔다.
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/trigger-fds", summary="FDS 이상거래 시뮬레이션 (데모용)")
async def trigger_fds_notification():
    """
    데모용 FDS 적발 시뮬레이터. notifications:global 채널 발행과 동시에 fds_reports에도
    실제로 기록한다 (rule_code='SIMULATED'로 구분 - 실탐지는 POST /api/v1/fds/scan 사용).

    기존에는 하드코딩 문구를 채널에 발행만 하고 DB에는 아무것도
    남기지 않아, fds_reports 테이블이 영구히 0건이었다.
    """
    from app.db.session import engine
    from app.models.wms import FdsReport
    from app.domains.notifications.service import emit

    description = "비정상적인 야간 대량 주문 패턴이 감지되었습니다."

    with Session(engine) as db:
        report = FdsReport(
            customer_name="교보문고 B2B 지점 (시뮬레이션)",
            fraud_score=61,
            fraud_reason=description,
            rule_code="SIMULATED",
            target_type="CUSTOMER",
            recommended_action="데모 시뮬레이션 건입니다. 실탐지는 FDS 관제 화면의 [전체 스캔 실행]을 사용하세요.",
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        report_id = str(report.id)

    # [수정 이력] 예전에는 여기서 dict를 직접 만들어 Redis에만 publish했다. 알림이 DB에
    # 남지 않아 새로고침하면 사라졌으므로, 저장+발행을 함께 하는 공용 서비스로 통일한다.
    event = emit(
        type="FDS_ALERT",
        title="FDS 이상거래 적발 (위험점수 61점)",
        description=description,
        ref_type="FDS_REPORT",
        ref_id=report_id,
        target_role="ADMIN",
    )

    return {"status": "SUCCESS", "event": event, "persisted": True}
