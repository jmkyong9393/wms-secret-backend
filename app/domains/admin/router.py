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

class DefectCoordinate(BaseModel):
    type: str = Field(..., description="e.g., BBOX")
    x: int
    y: int
    width: int
    height: int

class HitlOverrideRequest(BaseModel):
    ticketId: str = Field(..., description="Job Task ID or ID")
    decision: str = Field(..., description="APPROVE_DOWNGRADE, REJECT_RETURN, REJECT_DISCARD, APPROVE_NORMAL")
    targetGrade: Optional[str] = Field(None, description="A, B, C, S 등")
    primaryReasonCode: str = Field(..., description="DMG_EXT_CRUSH 등 단일 사유")
    reasonComment: Optional[str] = Field(None)
    defectCoordinates: List[DefectCoordinate] = Field(default_factory=list)
    reviewDurationMs: int = Field(..., description="관리자 체류 시간")

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
    수동 검수(HITL) 대기 중인 모든 건 조회 (도서 정보 조인)
    """
    from app.models.wms import Book
    statement = (
        select(ReturnJob, Book)
        .where(ReturnJob.status == JobStatusEnum.HITL_REQUIRED)
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
        elif item.decision.startswith("REJECT"):
            job.status = JobStatusEnum.REJECTED
        elif item.decision == "RE_CHECK":
            job.status = JobStatusEnum.PENDING
            job.retry_count += 1
        else:
            raise BadRequestException(f"Unknown decision: {item.decision}")
        
        # Save Agent Logs / Comments
        if not job.agent_logs:
            job.agent_logs = {}
        
        job.agent_logs["admin_decision"] = item.decision
        job.agent_logs["admin_comment"] = item.reasonComment
        job.agent_logs["primary_reason_code"] = item.primaryReasonCode
        job.agent_logs["target_grade"] = item.targetGrade
        
        session.add(job)
        
        # Create Audit Log for compliance & FDS
        audit = AdminAuditLog(
            admin_id=current_admin.id,
            target_type="RETURN_JOB",
            target_id=str(job.id),
            action=item.decision,
            previous_state=previous_state,
            new_state=job.status,
            target_grade=item.targetGrade,
            primary_reason_code=item.primaryReasonCode,
            defect_coordinates=[coord.dict() for coord in item.defectCoordinates],
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
