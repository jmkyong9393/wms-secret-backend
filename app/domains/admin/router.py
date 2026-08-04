import logging
logger = logging.getLogger(__name__)
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from typing import List, Dict, Any, Optional
from uuid import UUID
from pydantic import BaseModel, Field

from app.db.session import get_db
from app.models.wms import ReturnJob, AdminAuditLog, UserRoleEnum, JobStatusEnum
from app.core.security import get_current_user, RoleChecker
from app.core.exceptions import NotFoundException, BadRequestException

router = APIRouter(prefix="/admin/hitl", tags=["Admin HITL"])

# Admin 전용 권한 체커
admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])

class HitlOverrideRequest(BaseModel):
    ticketId: str = Field(..., description="Job Task ID or ID")
    decision: str = Field(..., description="APPROVE_DOWNGRADE, REJECT_RETURN, REJECT_DISCARD, APPROVE_NORMAL")
    targetGrade: Optional[str] = Field(None, description="A, B, C, S 등")
    primaryReasonCode: str = Field(..., description="DMG_EXT_CRUSH 등 단일 사유")
    reasonComment: Optional[str] = Field(None)
    defectCoordinates: Optional[List[Any]] = Field(default_factory=list)
    reviewDurationMs: Optional[int] = Field(0, description="관리자 체류 시간")

class BulkOverridePayload(BaseModel):
    items: List[HitlOverrideRequest]

class HitlTaskResponse(BaseModel):
    id: UUID
    book_id: UUID
    book_title: Optional[str] = None
    isbn: Optional[str] = None
    cover_image_url: Optional[str] = None
    image_urls: List[str] = Field(default_factory=list)
    status: str
    ubci_score: Optional[int] = None
    agent_logs: Optional[Dict[str, Any]] = None
    created_at: str

@router.get("/pending", response_model=List[HitlTaskResponse])
def get_pending_hitl_tasks(
    session: Session = Depends(get_db),
    current_admin = Depends(admin_only)
):
    """
    수동 검수(HITL) 대기 중인 모든 건 조회 (MASTER/ADMIN 보안 인증 가드 적용)
    """
    from app.models.wms import Book
    statement = (
        select(ReturnJob, Book)
        .where(ReturnJob.status.in_([JobStatusEnum.HITL_REQUIRED, JobStatusEnum.PENDING]))
        .outerjoin(Book, ReturnJob.book_id == Book.id)
    )
    results = session.exec(statement).all()
    
    output = []
    for job, book in results:
        output.append(
            HitlTaskResponse(
                id=job.id,
                book_id=job.book_id,
                book_title=book.title if book else "도서 정보 없음",
                isbn=book.isbn if book else "-",
                cover_image_url=book.cover_image_url if book else None,
                image_urls=job.image_urls or [],
                status=job.status,
                ubci_score=job.ubci_score,
                agent_logs=job.agent_logs,
                created_at=job.created_at.isoformat() if job.created_at else "",
            )
        )
    return output

@router.post("/override")
def submit_hitl_override(
    payload: BulkOverridePayload,
    session: Session = Depends(get_db),
    current_admin = Depends(admin_only)
):
    """
    관리자가 여러 HITL 건을 다중 선택하여 일괄 오버라이드.
    UBCI 감가 등급, 결함 좌표, 리뷰 시간 등을 함께 수집(Audit Log).
    """
    audit_logs = []
    processed_count = 0

    # 이 오버라이드를 실제로 결재한 관리자. InventoryUsedItem.inspected_by에 그대로 기록해
    # 재고 상세/보증서 화면의 "입고 처리 담당자"가 하드코딩 상수가 아니라 실제 결재자를
    # 가리키게 한다 (기존에는 "HITL - WM2608001 (장문경)" 문자열이 라우터에 박혀 있었다).
    admin_employee_id = str(getattr(current_admin, "employee_id", "") or "").strip()
    admin_name = str(getattr(current_admin, "name", "") or "").strip()
    if admin_employee_id and admin_name:
        hitl_inspector = f"{admin_employee_id} ({admin_name})"
    else:
        hitl_inspector = admin_employee_id or admin_name or "HITL 관리자"

    for item in payload.items:
        # Find ReturnJob (using ticketId as UUID string for now)
        try:
            job_uuid = UUID(item.ticketId)
        except ValueError:
            raise BadRequestException(f"Invalid ticketId format: {item.ticketId}")
            
        job = session.get(ReturnJob, job_uuid)
        if not job:
            continue # In a real app, maybe return error
            
        previous_state = job.status
        
        # Determine new status based on decision
        if item.decision.startswith("APPROVE"):
            job.status = JobStatusEnum.APPROVED
            # HITL 최종 결재 승인 시: 창고 보관 랙(Zone B-12-4 등) 위치 할당 및 재고(InventoryUsedItem) 편입
            from app.domains.inventory.service import assign_rack_location_after_inspection
            target_grade = item.targetGrade or (job.agent_logs.get("suggested_grade") if job.agent_logs else "NORMAL")
            lpn = (job.agent_logs.get("lpn_barcode") if job.agent_logs else None) or f"LPN-260728-A002"
            try:
                cert_code = str(job.id)[:6].upper()
                cert_url = f"/certificate/CERT-20260728-{cert_code}"
                assign_rack_location_after_inspection(
                    session,
                    lpn_barcode=lpn,
                    final_grade=target_grade,
                    book_id=job.book_id,
                    ubci_score=job.ubci_score or 85,
                    source_job_id=str(job.id),
                    certificate_url=cert_url,
                    inspection_source="HITL",
                    inspected_by=hitl_inspector,
                )
            except Exception as ex:
                logger.error(f"Failed to assign rack location: {ex}")
        elif item.decision.startswith("REJECT"):
            job.status = JobStatusEnum.REJECTED
            # [수정 이력] 승인(APPROVE) 분기와 달리 반려 분기는 랙 배정 자체를 호출하지 않아,
            # HITL에서 반려된 건은 Zone E(격리/폐기) 배정도 안 되고 InventoryUsedItem row도
            # 안 만들어져 실물 추적이 안 되고 있었다 - 자동 반려 경로(worker/tasks.py)와
            # 동일하게 Zone E 격리 랙 배정을 호출하도록 교정.
            from app.domains.inventory.service import assign_rack_location_after_inspection
            lpn = (job.agent_logs.get("lpn_barcode") if job.agent_logs else None) or f"LPN-260728-A002"
            try:
                assign_rack_location_after_inspection(
                    session,
                    lpn_barcode=lpn,
                    final_grade="REJECT",
                    final_status="REJECTED",
                    book_id=job.book_id,
                    ubci_score=job.ubci_score or 40,
                    source_job_id=str(job.id),
                    inspection_source="HITL",
                    inspected_by=hitl_inspector,
                )
            except Exception as ex:
                logger.error(f"Failed to assign rack location (reject): {ex}")
        elif item.decision in ["RE_CHECK", "AI_REINSPECT"]:
            # [수정 이력] 존재하지 않는 app.domains.returns.service.process_inspection을 import해서
            # 실제로 호출되면 100% ImportError로 죽던 코드였다 (Pipeline A/B 통합 이전의 잔재).
            # 상태만 PENDING으로 되돌리고 아무것도 재큐잉하지 않아 작업이 그대로 멈춰있던 문제도
            # 함께 수정 - /admin/hitl/{job_id}/re-inspect와 동일하게 Celery로 재큐잉한다.
            job.status = JobStatusEnum.PENDING
            job.retry_count += 1
            session.add(job)
            session.commit()

            from app.worker.tasks import process_inspection
            try:
                process_inspection.delay(str(job.id))
            except Exception as e:
                logger.warning(f"Celery 재큐잉 실패, 인프로세스로 폴백: {e}")
                import threading
                threading.Thread(target=process_inspection, args=(str(job.id),), daemon=True).start()
        else:
            raise BadRequestException(f"Unknown decision: {item.decision}")
        
        # Save Agent Logs / Comments
        # [수정 이력] 기존에는 job.agent_logs 딕셔너리를 제자리 변경(in-place mutation)했는데,
        # SQLAlchemy는 JSONB 컬럼의 제자리 변경을 감지하지 못해(MutableDict 미적용) UPDATE문에
        # 아예 포함되지 않았다. 그 결과 관리자의 결정/등급/메모가 DB에 한 번도 저장된 적이 없다.
        # 새 dict를 할당해 변경을 확실히 감지시킨다.
        job.agent_logs = {
            **(job.agent_logs or {}),
            "admin_decision": item.decision,
            "admin_comment": item.reasonComment,
            "primary_reason_code": item.primaryReasonCode,
            "target_grade": item.targetGrade,
        }
        
        session.add(job)
        
        # Admin ID UUID 변환 및 Foreign Key 방어 로직
        from app.models.wms import User
        valid_admin_id = None
        raw_admin_id = str(getattr(current_admin, "id", "") or "")
        try:
            parsed_uuid = UUID(raw_admin_id)
            user_exists = session.get(User, parsed_uuid)
            if user_exists:
                valid_admin_id = parsed_uuid
        except Exception:
            pass

        if not valid_admin_id:
            db_admin = session.exec(select(User).where(User.role.in_([UserRoleEnum.MASTER, UserRoleEnum.ADMIN]))).first()
            if not db_admin:
                db_admin = session.exec(select(User)).first()
            valid_admin_id = db_admin.id if db_admin else UUID("00000000-0000-0000-0000-000000000001")

        # Create Audit Log for compliance & FDS
        audit = AdminAuditLog(
            admin_id=valid_admin_id,
            target_type="RETURN_JOB",
            target_id=str(job.id),
            action=item.decision,
            previous_state=previous_state,
            new_state=job.status,
            target_grade=item.targetGrade,
            primary_reason_code=item.primaryReasonCode,
            defect_coordinates=[coord.dict() if hasattr(coord, 'dict') else coord for coord in (item.defectCoordinates or [])],
            review_duration_ms=item.reviewDurationMs
        )
        session.add(audit)
        audit_logs.append(audit)
        processed_count += 1
        
    session.commit()
    
    return {
        "status": "success",
        "processed_count": processed_count,
        "message": "HITL overrides successfully applied."
    }



@router.post("/{job_id}/re-inspect")
def trigger_ai_reinspection(job_id: str, session: Session = Depends(get_db)):
    """
    [Master AI Re-inspection Engine]
    HITL 관리자가 재검수를 요청하면, 앱 전체가 공유하는 단일 Celery 파이프라인
    (app.worker.tasks.process_inspection - WBF+GPT-4o Vision Agent, Redlock+DLQ)으로
    재큐잉한다.

    [수정 이력] 과거에는 요청을 블로킹하며 이미 폐기된 app.ai.graph.build_wms_graph()를
    동기 실행했고, 존재하지 않는 JobStatusEnum.INSPECTED 값을 대입하는 등 실행 자체가
    불가능한 버그가 있었다 (Pipeline A/B 통합 작업 중 발견). /inbound/retry와 동일한
    비동기 재큐잉 패턴으로 통일한다.
    """
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise BadRequestException(f"Invalid job_id UUID: {job_id}")

    job = session.get(ReturnJob, job_uuid)
    if not job:
        # 재고 상세 화면은 InventoryUsedItem.id로 재검수를 요청하므로 원본 검수 작업으로 환원한다.
        from app.models.wms import InventoryUsedItem
        used_item = session.get(InventoryUsedItem, job_uuid)
        if used_item and used_item.source_job_id:
            job = session.get(ReturnJob, used_item.source_job_id)

    # [수정 이력] 여기서 `job = session.query(ReturnJob).first()`로 폴백하고 있었다.
    # 잘못된 ID로 재검수를 눌러도 404가 아니라 "DB의 아무 검수 작업 한 건"을 다시 큐에
    # 밀어넣어, 전혀 관계없는 도서가 재검수되고 그 등급이 덮어써졌다. 정직하게 404를 낸다.
    if not job:
        raise NotFoundException(f"ReturnJob with ID {job_id} not found")

    if not job.image_urls:
        raise BadRequestException("No images found for this job.")

    job.status = JobStatusEnum.PENDING.value
    job.retry_count = (job.retry_count or 0) + 1
    session.add(job)
    session.commit()

    from app.worker.tasks import process_inspection
    try:
        process_inspection.delay(str(job.id))
    except Exception as e:
        import threading
        threading.Thread(target=process_inspection, args=(str(job.id),), daemon=True).start()

    return {
        "status": "queued",
        "message": "재검수 작업이 Celery 큐에 등록되었습니다. 진행 상황은 SSE로 확인하세요.",
        "job_id": str(job.id),
    }

@router.get("/completed", response_model=List[HitlTaskResponse])
def get_completed_hitl_tasks(
    session: Session = Depends(get_db),
    current_admin = Depends(admin_only)
):
    """
    HITL 검수 및 오버라이드가 완료된(APPROVED, REJECTED) 처리 내역 전체 조회
    """
    from app.models.wms import Book
    statement = (
        select(ReturnJob, Book)
        .where(ReturnJob.status.in_([JobStatusEnum.APPROVED, JobStatusEnum.REJECTED]))
        .outerjoin(Book, ReturnJob.book_id == Book.id)
    )
    results = session.exec(statement).all()
    
    output = []
    for job, book in results:
        output.append(
            HitlTaskResponse(
                id=job.id,
                book_id=job.book_id,
                book_title=book.title if book else "도서 정보 없음",
                isbn=book.isbn if book else "-",
                cover_image_url=book.cover_image_url if book else None,
                image_urls=job.image_urls or [],
                status=job.status,
                ubci_score=job.ubci_score,
                agent_logs=job.agent_logs,
                created_at=job.created_at.isoformat() if job.created_at else "",
            )
        )
    return output
