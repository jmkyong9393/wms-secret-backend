from fastapi import APIRouter, Depends, status
from app.domains.returns.schemas import InspectionRequest
from app.domains.returns.service import ReturnService
from fastapi.responses import StreamingResponse
import redis.asyncio as redis
import asyncio
from typing import List

router = APIRouter(prefix="/returns", tags=["Returns & AI Inspections"])

@router.post("/inspections", status_code=202)
def create_inspection(
    request: InspectionRequest, 
    return_service: ReturnService = Depends()
):
    """
    Spring Boot의 Controller 역할을 수행하는 라우터.
    오직 HTTP 파라미터 맵핑 및 Service 계층 호출 역할만 수행합니다. (비즈니스 로직은 Service에 위임)
    """
    job_id = return_service.trigger_inspection(
        book_id=request.book_id,
        location_id=request.location_id,
        image_urls=request.image_urls
    )
    
    return {"job_id": job_id, "message": "검수 파이프라인 가동 시작"}

@router.get("/inspections/{job_id}/stream")
async def stream_inspection_status(job_id: str):
    """
    SSE 푸시 알림 API (Redis Pub/Sub 연동)
    DB 폴링 없이, Celery 워커가 작업 완료 시 Redis에 발행하는 이벤트를 구독하여
    프론트엔드로 즉시 단방향 푸시(Server-Sent Events)를 전송합니다.
    """
    async def event_generator():
        # 비동기 Redis 클라이언트 생성
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(f"job_status:{job_id}")
        
        try:
            # 1. 연결 성공 알림 푸시
            yield f"data: {{\"job_id\": \"{job_id}\", \"status\": \"CONNECTED\"}}\\n\\n"
            
            # 2. 이벤트 구독 대기 루프 (Non-blocking)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    # 프론트엔드로 메시지 푸시
                    yield f"data: {data}\\n\\n"
                    
                    # 최종 상태 도달 시 커넥션 정상 종료
                    if "APPROVED" in data or "REVIEW" in data or "REJECTED" in data or "error" in data:
                        break
        finally:
            await pubsub.unsubscribe()
            await r.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
