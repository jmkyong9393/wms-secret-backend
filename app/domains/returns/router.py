from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.db.session import get_db
from app.domains.returns.schemas import InspectionRequest
from app.domains.returns.service import ReturnService
from fastapi.responses import StreamingResponse
import redis.asyncio as redis
from app.core.stream_auth import require_stream_access
from app.core.security import get_current_user
from app.models.wms import User

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
async def stream_inspection_status(job_id: str, _user: User = Depends(require_stream_access)):
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
    scope: Optional[str] = Query(None, description="mine이면 로그인 사용자 본인 검수만"),
    worker_id: Optional[str] = Query(None, description="특정 담당자 사번으로 필터 (ADMIN/MASTER 전용)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    # 조회 대상자는 **서버가 세션에서 정한다.** 종전에는 클라이언트가 보낸 worker_id를
    # 그대로 신뢰해, 남의 사번을 넣으면 그 사람의 검수 이력이 조회됐다.
    #
    # role은 Enum이라 str()이 'UserRoleEnum.MASTER'가 된다. value를 꺼내야 한다.
    _role = getattr(current_user, "role", None)
    role = str(getattr(_role, "value", _role) or "").upper()
    is_manager = role in ("ADMIN", "MASTER")

    if not is_manager or scope == "mine":
        # WORKER는 scope·worker_id와 무관하게 본인 것만 본다 (권한 하향 고정).
        # 관리자도 scope=mine이면 본인 것만 본다.
        worker_id = current_user.employee_id
    # 관리자가 scope 없이 worker_id를 지정하면 그 담당자 것을 그대로 조회한다.
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

    # ------------------------------------------------------------------
    # 신품 Fast-Track 입고분 병합
    # ------------------------------------------------------------------
    # [2026-08-10 신설] 이 화면은 "통합 검수 처리 내역"인데 원장이 return_jobs 하나뿐이라
    # 신품 입고분이 한 건도 뜨지 않았다. 신품은 무검수 입고라 ReturnJob을 아예 만들지 않기
    # 때문이다(설계상 정상). 그래서 조장이 직접 등록한 신품이 "나의 검수 내역"에 나타나지
    # 않았다. 신품의 입고 원장은 InventoryLog(INBOUND/NEW)이므로 그쪽을 함께 읽어 합친다.
    #
    # 등급·점수·결함은 신품에 존재하지 않는 값이라 지어내지 않고 None으로 둔다
    # (화면이 "미표기 (신품 Fast-Track)"으로 표기한다).
    from app.models.wms import InventoryLog

    # 상태 필터는 검수 파이프라인 상태값이라 신품에는 대응 개념이 없다.
    # APPROVED(=입고 확정)만 신품과 의미가 통하므로 그 외 필터가 걸리면 신품은 제외한다.
    include_new = (not status) or status.upper() == "APPROVED"

    if include_new:
        log_stmt = select(InventoryLog).where(
            InventoryLog.transaction_type == "INBOUND",
            InventoryLog.condition_grade == "NEW",
        )
        if worker_id:
            log_stmt = log_stmt.where(InventoryLog.worker_id == worker_id)

        log_rows = db.exec(log_stmt.order_by(InventoryLog.created_at.desc()).limit(limit)).all()
        new_books = {}
        if log_rows:
            book_ids = {lg.book_id for lg in log_rows if lg.book_id}
            if book_ids:
                new_books = {b.id: b for b in db.exec(select(Book).where(Book.id.in_(book_ids))).all()}

        for lg in log_rows:
            b = new_books.get(lg.book_id)
            items.append({
                "job_id": f"NEWLOG-{lg.id}",
                "lpn_barcode": None,  # 신품은 개별 LPN을 발급하지 않는다
                "book": {
                    "title": b.title if b else "도서 정보 없음",
                    "author": b.author if b else "-",
                    "publisher": b.publisher if b else "-",
                    "isbn": b.isbn if b else "-",
                    "base_price": b.base_price if b else 0.0,
                    "cover_image_url": (b.cover_image_url if b else "") or "",
                },
                "image_urls": [],
                "avg_defect_confidence": None,
                "ubci_score": None,
                "grade": "NEW_FASTTRACK",
                "confirmed_grade": None,
                "status": "APPROVED",
                "inspection_source": "NEW_FASTTRACK",
                "inspected_by": None,
                "inspector_label": "신품 Fast-Track (무검수 입고)",
                "inbound_worker_id": lg.worker_id,
                "defect_count": 0,
                "defect_types": [],
                "retry_count": 0,
                "created_at": lg.created_at.strftime("%Y-%m-%d %H:%M:%S") if lg.created_at else None,
                "updated_at": lg.updated_at.strftime("%Y-%m-%d %H:%M:%S") if lg.updated_at else None,
            })
            total += 1

        # 중고/신품을 한 목록에서 최신순으로 읽도록 병합 후 재정렬한다.
        items.sort(key=lambda r: r.get("created_at") or "", reverse=True)

    # "작업자"는 AI/HITL 판정 주체(inspector_label)가 아니라 실제 입고 처리한 사람이어야
    # 한다 - 재고 상세/목록과 같은 표기 정본(format_worker_label)을 여기서도 쓴다.
    # 라벨 인쇄가 이 필드를 그대로 쓰므로, 없으면 화면·인쇄 양쪽에서 AI 이름이 노출된다.
    from app.core.constants import format_worker_label

    emp_ids = {it["inbound_worker_id"] for it in items if it.get("inbound_worker_id")}
    name_by_emp: Dict[str, str] = {}
    if emp_ids:
        for u in db.exec(select(User).where(User.employee_id.in_(emp_ids))).all():
            name_by_emp[u.employee_id] = u.name
    for it in items:
        it["worker_label"] = format_worker_label(
            it.get("inbound_worker_id"), name_by_emp.get(it.get("inbound_worker_id") or "")
        )

    # 작업자 필터는 agent_logs(JSONB) 안에 있어 SQL로 걸지 못하고 파이썬에서 거른다.
    # 그래서 count 쿼리가 필터를 반영하지 못해 total이 전체 건수로 나왔다(내 검수 1건인데
    # 2,714건으로 표기). 필터가 걸린 조회에서는 실제 반환 건수를 그대로 쓴다.
    if worker_id:
        total = len(items)

    return {"items": items, "total": total, "limit": limit, "offset": offset}
