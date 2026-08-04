import json
import logging
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.models.wms import now_kst

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["Notifications"])

# 전역 알림 브로드캐스트 채널 (job 단위가 아닌 대시보드 전역 알림용)
NOTIFICATIONS_CHANNEL = "notifications:global"


@router.get("/stream", summary="실시간 WMS 전역 알림 SSE 스트리밍")
async def stream_notifications(request: Request):
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

            async for message in pubsub.listen():
                if await request.is_disconnected():
                    logger.info("SSE Client disconnected")
                    break
                if message["type"] != "message":
                    continue
                yield f"data: {message['data']}\n\n"
        except Exception as e:
            logger.error(f"SSE stream error: {e}")
        finally:
            await pubsub.unsubscribe()
            await r.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/trigger-fds", summary="FDS 이상거래 시뮬레이션 (데모용)")
async def trigger_fds_notification():
    """
    데모용 FDS 적발 시뮬레이터. notifications:global 채널 발행과 동시에 fds_reports에도
    실제로 기록한다 (rule_code='SIMULATED'로 구분 - 실탐지는 POST /api/v1/fds/scan 사용).

    [수정 이력 2026-08-04] 기존에는 하드코딩 문구를 채널에 발행만 하고 DB에는 아무것도
    남기지 않아, fds_reports 테이블이 영구히 0건이었다.
    """
    import redis as sync_redis
    from fastapi import Depends
    from app.core.redis_pubsub import REDIS_URL
    from app.db.session import engine
    from app.models.wms import FdsReport
    from sqlmodel import Session

    fds_event = {
        "type": "FDS_ALERT",
        "category": "FDS 이상거래",
        "title": "FDS 이상거래 적발 (위험점수 61점)",
        "description": "비정상적인 야간 대량 주문 패턴이 감지되었습니다.",
        "time_ago": "방금 전",
        "timestamp": now_kst().isoformat(),
    }

    with Session(engine) as db:
        db.add(FdsReport(
            customer_name="교보문고 B2B 지점 (시뮬레이션)",
            fraud_score=61,
            fraud_reason=fds_event["description"],
            rule_code="SIMULATED",
            target_type="CUSTOMER",
            recommended_action="데모 시뮬레이션 건입니다. 실탐지는 FDS 관제 화면의 [전체 스캔 실행]을 사용하세요.",
        ))
        db.commit()

    client = sync_redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.publish(NOTIFICATIONS_CHANNEL, json.dumps(fds_event, ensure_ascii=False))
    finally:
        client.close()

    return {"status": "SUCCESS", "event": fds_event, "persisted": True}
