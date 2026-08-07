from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.db.session import get_db
from app.domains.returns.schemas import InspectionRequest
from app.domains.returns.service import ReturnService
from fastapi.responses import StreamingResponse
import redis.asyncio as redis

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
        import os
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        r = redis.Redis.from_url(redis_url, decode_responses=True)
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


# ==========================================
# 검수 처리 이력 조회 (/inspections 화면 전용)
# ==========================================
#
# 검수 이력의 사실 원장은 `return_jobs`다. 이 엔드포인트는 그 테이블만 읽고,
# 없는 값은 채우지 않고 null로 내려보낸다 — 화면이 기본값으로 지어낼 여지를 없앤다.
@router.get("/inspections", summary="검수 처리 이력 조회 (return_jobs 원장 기반)")
def list_inspections(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="APPROVED / REJECTED / HITL_REQUIRED / PENDING / PROCESSING / FAILED"),
    worker_id: Optional[str] = Query(None, description="입고 촬영 담당자 사번으로 필터 (내 검수만 보기)"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    from app.domains.inventory.router import resolve_inspector
    from app.models.wms import Book, InventoryUsedItem, ReturnJob, ubci_grade_from_score

    stmt = select(ReturnJob)
    if status:
        stmt = stmt.where(ReturnJob.status == status.upper())

    rows = db.exec(stmt.order_by(ReturnJob.created_at.desc()).limit(limit).offset(offset)).all()

    # 총 건수(페이징 표시용)
    count_stmt = select(ReturnJob)
    if status:
        count_stmt = count_stmt.where(ReturnJob.status == status.upper())
    total = len(db.exec(count_stmt).all())

    items: List[Dict[str, Any]] = []
    for job in rows:
        logs = job.agent_logs or {}

        # 입고 촬영 담당자 필터는 agent_logs(JSONB) 안에 있어 SQL로 걸기 번거로우므로 여기서 거른다.
        inbound_worker = logs.get("inbound_worker_id")
        if worker_id and inbound_worker != worker_id:
            continue

        book = db.get(Book, job.book_id) if job.book_id else None
        lpn = logs.get("lpn_barcode")

        # 등급 확정 주체는 재고 행에만 있다(AI 자동 / HITL 결재자). 없으면 아직 미확정이다.
        item = (
            db.exec(select(InventoryUsedItem).where(InventoryUsedItem.lpn_barcode == lpn)).first()
            if lpn else None
        )

        defects = logs.get("defects") or []

        # 판독 신뢰도는 결함별로만 기록된다. 항목 단위 지표는 그 평균으로 계산하며,
        # 결함이 없으면 근거가 없으므로 null이다(임의의 기본값을 넣지 않는다).
        confidences = [
            float(d["confidence"])
            for d in defects
            if isinstance(d, dict) and isinstance(d.get("confidence"), (int, float))
        ]
        avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else None

        items.append({
            "job_id": str(job.id),
            "inventory_item_id": str(item.id) if item else None,
            "lpn_barcode": lpn,
            "book": {
                "title": book.title if book else None,
                "author": book.author if book else None,
                "publisher": book.publisher if book else None,
                "isbn": book.isbn if book else None,
                "base_price": book.base_price if book else None,
                "cover_image_url": book.cover_image_url if book else None,
            },
            # 아래 값들은 없으면 null이다. 기본값으로 채우지 않는다.
            "avg_defect_confidence": avg_confidence,
            "ubci_score": job.ubci_score,
            "grade": ubci_grade_from_score(job.ubci_score) if job.ubci_score is not None else None,
            "confirmed_grade": item.condition_grade if item else None,
            "status": job.status,
            "inspection_source": getattr(item, "inspection_source", None) if item else None,
            "inspected_by": getattr(item, "inspected_by", None) if item else None,
            # 재고 상세와 같은 표기를 쓰도록 라벨 생성은 한 곳에서만 한다.
            "inspector_label": resolve_inspector(item, job)["label"] if item else None,
            "inbound_worker_id": inbound_worker,
            "defect_count": len(defects),
            "defect_types": sorted({str(d.get("type")) for d in defects if isinstance(d, dict) and d.get("type")}),
            "retry_count": job.retry_count or 0,
            "created_at": job.created_at.strftime("%Y-%m-%d %H:%M:%S") if job.created_at else None,
            "updated_at": job.updated_at.strftime("%Y-%m-%d %H:%M:%S") if job.updated_at else None,
        })

    return {"items": items, "total": total, "limit": limit, "offset": offset}
