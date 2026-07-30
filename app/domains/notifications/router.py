import asyncio
import json
import logging
from typing import AsyncGenerator
from fastapi import APIRouter, Request, Depends
from sse_starlette.sse import EventSourceResponse
from app.core.sse_ticket_service import sse_ticket_service

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/stream", summary="실시간 WMS SSE 이벤트 스트리밍")
async def stream_notifications(request: Request, ticket: str = ""):
    """
    실시간 WMS 알림 SSE (Server-Sent Events) 스트리밍 엔드포인트
    인바운드 AI 검수 완료, HITL 수동 이관, 출고 패킹 이벤트를 동적으로 수신
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            queue = await sse_ticket_service.subscribe(ticket)
            while True:
                if await request.is_disconnected():
                    logger.info("SSE Client disconnected")
                    break
                try:
                    event_data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield json.dumps(event_data, ensure_ascii=False)
                except asyncio.TimeoutError:
                    # Keep-alive heartbeat
                    yield json.dumps({"type": "ping", "message": "heartbeat"}, ensure_ascii=False)
        except Exception as e:
            logger.error(f"SSE stream error: {e}")
        finally:
            await sse_ticket_service.unsubscribe(ticket)

    return EventSourceResponse(event_generator())
