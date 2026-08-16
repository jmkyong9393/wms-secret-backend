import logging
logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from typing import List, Dict, Any, Optional
from uuid import UUID
from pydantic import BaseModel, Field

from app.db.session import get_db
from app.models.wms import ReturnJob, AdminAuditLog, UserRoleEnum, JobStatusEnum
from app.core.security import get_current_user, RoleChecker
from app.core.constants import format_worker_label
from app.core.exceptions import NotFoundException, BadRequestException

# Admin 전용 권한 체커
admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])

# HITL 결재는 전부 관리자 전용이다. 엔드포인트별로 붙이면 새 경로에서 또 빠뜨린다 —
# 실제로 재검수 트리거(POST)에 인가가 없어 무인증으로 Celery 재큐잉이 가능했다.
router = APIRouter(prefix="/admin/hitl", tags=["Admin HITL"],
                   dependencies=[Depends(admin_only)])


def _is_hitl_required(status) -> bool:
    """status는 평문 str 컬럼이지만 같은 요청 안에서 Enum 멤버가 대입돼 있을 수 있다.
    str(Enum멤버)는 "JobStatusEnum.HITL_REQUIRED"라 값 비교가 조용히 어긋난다 - 둘 다 받는다."""
    return getattr(status, "value", status) == JobStatusEnum.HITL_REQUIRED.value


def _resolve_admin_audit_id(current_admin, session: Session) -> UUID:
    """AdminAuditLog.admin_id는 users.id를 참조하는 UUID FK다. current_admin.id를
    신뢰하되 DB에 실제로 존재하는지 확인하고, 없으면 관리자 계정으로 폴백한다."""
    from app.models.wms import User
    raw_admin_id = str(getattr(current_admin, "id", "") or "")
    try:
        parsed_uuid = UUID(raw_admin_id)
        if session.get(User, parsed_uuid):
            return parsed_uuid
    except Exception:
        pass

    db_admin = session.exec(select(User).where(User.role.in_([UserRoleEnum.MASTER, UserRoleEnum.ADMIN]))).first()
    if not db_admin:
        db_admin = session.exec(select(User)).first()
    return db_admin.id if db_admin else UUID("00000000-0000-0000-0000-000000000001")

class HitlOverrideRequest(BaseModel):
    ticketId: str = Field(..., description="Job Task ID or ID")
    decision: str = Field(..., description="APPROVE_DOWNGRADE, REJECT_RETURN, REJECT_DISCARD, APPROVE_NORMAL")
    targetGrade: Optional[str] = Field(None, description="A, B, C, S 등")
    primaryReasonCode: str = Field(..., description="DMG_EXT_CRUSH 등 단일 사유")
    reasonComment: Optional[str] = Field(None)
    # [2026-08-08] 더 이상 AdminAuditLog에 그대로 쓰이지 않는다 - 서버가 exclude/adopt/
    # editedBboxes 반영 후 job.agent_logs.defects로부터 감사 로그용 좌표를 직접 재조립한다
    # (프론트가 보내는 이 값은 모달을 연 시점의 스냅샷이라 편집분을 반영하지 못했다).
    # 구버전 프론트와의 호환을 위해 필드는 유지하되 무시한다.
    defectCoordinates: Optional[List[Any]] = Field(default_factory=list)
    reviewDurationMs: Optional[int] = Field(0, description="관리자 체류 시간")
    # --- BBox 채택/제외 (2026-08-07) ---
    # 검수자가 화면에서 결함을 눌러 판정을 고친 결과. 인덱스는 agent_logs.defects /
    # agent_logs.yolo_candidates 배열 기준이다.
    excludedDefectIndexes: Optional[List[int]] = Field(
        default_factory=list, description="오탐으로 판단해 감점에서 제외할 결함 인덱스")
    adoptedCandidateIndexes: Optional[List[int]] = Field(
        default_factory=list, description="AI가 놓쳤으나 실제 결함으로 채택할 YOLO 후보 인덱스")
    # --- BBox 좌표 직접 수정 (2026-08-08) ---
    # 검수자가 화면에서 확정 결함의 박스를 드래그해 위치/크기를 고친 결과. index는
    # agent_logs.defects 배열 기준(excludedDefectIndexes와 동일 인덱스 공간)이며,
    # xmin/ymin/xmax/ymax는 Vision Agent와 동일한 0~1000 상대좌표다.
    editedBboxes: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list,
        description="검수자가 직접 고친 결함 좌표. [{index, xmin, ymin, xmax, ymax}]")
    # --- BBox 신규 추가 (2026-08-09) ---
    # AI가 아예 놓친 결함을 검수자가 직접 그려 넣은 것. defects 배열에 새 항목으로 추가된다.
    addedBboxes: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list,
        description="검수자가 직접 그린 신규 결함. [{type, xmin, ymin, xmax, ymax, imageIndex}]")

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
    # 처리(상태 변경) 시각. 목록 정렬 기준이자 "언제 회수/이관됐는가" 표시용.
    updated_at: Optional[str] = None
    # 관리자가 남긴 최신 메모. 프론트가 이 필드를 읽는데 종전에는 서버가 채우지 않아
    # 회수 사유·결재 코멘트가 DB에 있어도 화면에는 고정 문구만 떴다 (2026-08-13 실사고).
    human_issue_notes: Optional[str] = None


def _latest_admin_memo(agent_logs: Optional[Dict[str, Any]], *, prefer_recall: bool) -> Optional[str]:
    """agent_logs에서 관리자가 남긴 최신 메모를 꺼낸다.
    메모는 두 곳에 저장된다: 결재 코멘트(human_feedback.admin_comment)와 회수 사유(recall_history[-1].reason).
    결재 대기 목록은 "왜 다시 대기로 왔는가"가 관심사라 회수 사유를 우선하고, 완료 목록은 결재 코멘트를 우선한다.
    """
    logs = agent_logs or {}
    comment = ((logs.get("human_feedback") or {}).get("admin_comment") or "").strip() or None
    recalls = logs.get("recall_history") or []
    recall_reason = ((recalls[-1].get("reason") or "").strip() or None) if recalls else None
    return (recall_reason or comment) if prefer_recall else (comment or recall_reason)

def apply_bbox_edits(defects: List[Dict[str, Any]], candidates: List[Dict[str, Any]], item) -> List[Dict[str, Any]]:
    """검수자의 BBox 채택/제외/좌표수정/신규추가를 결함 배열에 반영한 새 배열을 돌려준다.

    제외는 삭제가 아니라 `hitl_excluded` 표식만 남긴다 - 감사 추적을 유지하고 재학습 라벨로 재사용할 수 있게 하기 위함이다. 
    결재 확정(/override)과 점수 미리보기(/score-preview)가 같은 규칙을 쓰도록 이 함수 하나로 모았다.
    """
    out = list(defects)
    cands = list(candidates or [])

    for idx in (item.excludedDefectIndexes or []):
        if 0 <= idx < len(out) and isinstance(out[idx], dict):
            out[idx] = {**out[idx], "hitl_excluded": True}

    for idx in (item.adoptedCandidateIndexes or []):
        if not (0 <= idx < len(cands)) or not isinstance(cands[idx], dict):
            continue
        c = cands[idx]
        # 후보에는 감점 산정에 필요한 필드가 없다. ratio는 좌표에서 유도되므로
        # (policy의 _effective_ratio) 여기서 지어내지 않고 비워 둔다.
        out.append({
            "type": c.get("defect_type") or c.get("type") or "UNKNOWN",
            "ratio": 0,
            "confidence": c.get("confidence"),
            "bbox": c.get("bbox"),
            "image_index": c.get("image_index"),
            "hitl_adopted": True,
            "description": "관리자가 YOLO 후보에서 직접 채택",
        })

    # 드래그로 고친 좌표. 판정(type/confidence)은 AI 산출을 유지하고 좌표만 덮어쓴다.
    for edit in (item.editedBboxes or []):
        if not isinstance(edit, dict):
            continue
        idx = edit.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(out)) or not isinstance(out[idx], dict):
            continue
        try:
            new_bbox = {k: int(edit[k]) for k in ("xmin", "ymin", "xmax", "ymax")}
        except (KeyError, TypeError, ValueError):
            continue
        out[idx] = {**out[idx], "bbox": new_bbox, "hitl_bbox_edited": True}

    # AI가 놓친 것을 검수자가 직접 그린 신규 결함.
    for added in (item.addedBboxes or []):
        if not isinstance(added, dict):
            continue
        try:
            new_bbox = {k: int(added[k]) for k in ("xmin", "ymin", "xmax", "ymax")}
        except (KeyError, TypeError, ValueError):
            continue
        out.append({
            "type": added.get("type") or "UNKNOWN",
            "ratio": 0,
            "bbox": new_bbox,
            "image_index": added.get("imageIndex", 0),
            "hitl_added": True,
            "description": "관리자가 직접 추가",
        })

    return out


class ScorePreviewRequest(BaseModel):
    """BBox 편집분으로 점수만 미리 계산한다 (저장하지 않음)."""
    excludedDefectIndexes: Optional[List[int]] = Field(default=None)
    adoptedCandidateIndexes: Optional[List[int]] = Field(default=None)
    editedBboxes: Optional[List[Dict[str, Any]]] = Field(default=None)
    addedBboxes: Optional[List[Dict[str, Any]]] = Field(default=None)


@router.post("/{job_id}/score-preview")
def preview_score_with_edits(
    job_id: str,
    req: ScorePreviewRequest,
    session: Session = Depends(get_db),
    current_admin = Depends(admin_only),
):
    """편집한 BBox 기준의 UBCI 점수/등급을 결재 **전에** 돌려준다.

    policy_agent는 LLM을 쓰지 않는 결정론적 산식이므로 이 미리보기는 API 호출 0회다.
    보증서 문안(report_agent)은 결재가 확정된 뒤에만 생성한다 - 확정되지 않은 판정으로 고객 문서를 만들지 않기 위함이다.

    아무것도 저장하지 않는다. 실제 반영은 /override 가 담당한다.
    """
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise BadRequestException(f"Invalid job_id UUID: {job_id}")

    job = session.get(ReturnJob, job_uuid)
    if not job:
        raise NotFoundException(f"ReturnJob with ID {job_id} not found")

    logs = dict(job.agent_logs or {})
    defects = apply_bbox_edits(
        list(logs.get("defects") or []), list(logs.get("yolo_candidates") or []), req
    )
    scored = [d for d in defects if not d.get("hitl_excluded")]

    from app.ai.agents import policy_agent, critic_stage_a_integrity_check

    result = policy_agent({"defects": scored, "book_title": logs.get("book_title") or ""})
    score = result.get("ubci_score")
    grade = _grade_from_score(score)

    issues = []
    try:
        issues = critic_stage_a_integrity_check(scored, len(job.image_urls or []), score)
    except Exception as e:
        logger.warning(f"미리보기 Critic Stage A 실패 (점수는 유효): {e}")

    return {
        "ubci_score": score,
        "grade": grade,
        "policy_text": result.get("policy_text"),
        "score_unverified": bool(result.get("score_unverified")),
        "defect_count": len(scored),
        "excluded_count": len(defects) - len(scored),
        "integrity_issues": issues,
        "current_score": job.ubci_score,
        "delta": (score - job.ubci_score) if (score is not None and job.ubci_score is not None) else None,
    }


def _grade_from_score(score: Optional[int]) -> Optional[str]:
    """UBCI_Specification_v2.0.0.0 경계값. 점수가 없으면 등급도 없다(지어내지 않는다)."""
    if score is None:
        return None
    return "MINT" if score >= 95 else "GOOD" if score >= 85 else "NORMAL" if score >= 65 else "REJECT"


class RecallToHitlRequest(BaseModel):
    reason: Optional[str] = Field(default=None, description="회수 사유 (감사 추적에 남는다)")


@router.post("/recall/{item_id}")
def recall_inventory_to_hitl(
    item_id: str,
    req: RecallToHitlRequest,
    session: Session = Depends(get_db),
    current_admin = Depends(admin_only),
):
    """적재 완료된 재고를 관리자 판단으로 HITL 재검수 대기로 되돌린다.
    Vision Agent의 판독 편차가 커서 자동 확정된 등급이 실제와 어긋나는 경우가 있다.
    이미 재고에 들어간 뒤에도 사람이 되돌려 정답지를 만들 수 있어야 하며, 그렇게 쌓인 라벨이 탐지기 재학습의 입력이 된다.
    되돌리는 것은 판정 상태뿐이다. 점수·결함·이미지는 그대로 두고 재검수 대기로만 표시한다 - 여기서 점수를 지우면 "무엇을 고쳤는지" 비교할 기준이 사라진다.
    """
    from app.models.wms import InventoryUsedItem, now_kst

    try:
        parsed = UUID(item_id)
    except ValueError:
        raise BadRequestException(f"Invalid item_id UUID: {item_id}")

    used_item = session.get(InventoryUsedItem, parsed)
    job = None
    if used_item:
        job = session.get(ReturnJob, used_item.source_job_id) if used_item.source_job_id else None
    else:
        # 재고 id가 아니라 검수 작업 id로 부르는 화면도 있으므로 함께 받아 준다.
        job = session.get(ReturnJob, parsed)
        if job:
            used_item = session.exec(
                select(InventoryUsedItem).where(InventoryUsedItem.source_job_id == job.id)
            ).first()

    if not job:
        raise NotFoundException(f"검수 작업을 찾을 수 없습니다: {item_id}")
    if not job.image_urls:
        raise BadRequestException("검수 이미지가 없어 재검수할 수 없습니다.")

    actor = getattr(current_admin, "employee_id", None) or str(getattr(current_admin, "id", "UNKNOWN"))
    previous_status = str(job.status)

    logs = dict(job.agent_logs or {})
    history = list(logs.get("recall_history") or [])
    history.append({
        "recalled_by": actor,
        "recalled_at": now_kst().isoformat(),
        "reason": req.reason or "관리자 임의 회수",
        "previous_status": previous_status,
        "previous_score": job.ubci_score,
        "previous_grade": used_item.condition_grade if used_item else None,
    })
    logs["recall_history"] = history
    job.agent_logs = logs
    # 상태를 HITL_REQUIRED로 되돌려 두면, 이후 "AI 재검수"를 눌러도 트리거 시점 상태를 읽어 was_hitl=True가 넘어가므로 AI 단독 확정이 막힌다 (배경: 01-freeze-zones.md).
    job.status = JobStatusEnum.HITL_REQUIRED.value
    session.add(job)

    if used_item:
        # 판매 가능 재고에서 빠진다. 등급·점수는 비교 기준으로 남긴다.
        used_item.item_status = "HITL_PENDING"
        used_item.inspection_source = "PENDING_HITL"
        session.add(used_item)

    session.add(AdminAuditLog(
        # admin_id는 users.id를 참조하는 UUID FK다. employee_id 문자열을 넣으면 500이 난다.
        admin_id=_resolve_admin_audit_id(current_admin, session),
        action="RECALL_TO_HITL",
        target_type="RETURN_JOB",
        target_id=str(job.id),
        previous_state=previous_status,
        new_state=JobStatusEnum.HITL_REQUIRED.value,
        primary_reason_code="ADMIN_RECALL",
        target_grade=used_item.condition_grade if used_item else None,
    ))
    session.commit()

    return {
        "status": "recalled",
        "job_id": str(job.id),
        "item_id": str(used_item.id) if used_item else None,
        "previous_status": previous_status,
        "previous_score": job.ubci_score,
        "message": "HITL 재검수 대기로 되돌렸습니다. 관리자 결재 전에는 재고에 편입되지 않습니다.",
    }


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
        # 이관/회수 시각 역순 - 방금 올라온 건이 맨 위. created_at을 보조 키로 둬서 같은 시각에 일괄 생성된 건들도 순서가 흔들리지 않게 한다(동률이면 DB 임의 순서가 된다).
        .order_by(ReturnJob.updated_at.desc(), ReturnJob.created_at.desc())
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
                updated_at=job.updated_at.isoformat() if job.updated_at else None,
                # 결재 대기 화면이므로 회수 사유 우선 ("왜 다시 대기로 왔는가")
                human_issue_notes=_latest_admin_memo(job.agent_logs, prefer_recall=True),
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
    rejected_job_ids = []  # 커밋 후 Restock 판정 그래프(자동 발주 제안)를 태울 반려 확정 건
    # 커밋 후 보증서 생성(GPT-4o-mini)을 워커로 태울 승인 확정 건.
    # 종전에는 이 루프 안에서 build_certificate_document()를 건별로 동기 호출했는데,
    # 합성 시드 데이터 다건을 한 번에 승인하는 요청에서 순차 LLM 호출이 쌓여 wms-secret-api 컨테이너가 OOM(exit 137)으로 죽었다. 반려 확정 건의 Restock 제안 큐잉과 동일한 원리로, 등급 확정·랙 배정·재고 편입은 이 요청 안에서 동기로 끝내고 보증서 문서화만 워커로 분리한다 (app/worker/tasks.py generate_hitl_certificate 참조).
    approved_job_ids_for_cert = []

    # 이 오버라이드를 실제로 결재한 관리자. InventoryUsedItem.inspected_by에 그대로 기록해 재고 상세/보증서 화면의 "입고 처리 담당자"가 하드코딩 상수가 아니라 실제 결재자를 가리키게 한다 (기존에는 "HITL - WM2608001 (장문경)" 문자열이 라우터에 박혀 있었다).
    admin_employee_id = str(getattr(current_admin, "employee_id", "") or "").strip()
    admin_name = str(getattr(current_admin, "name", "") or "").strip()
    if admin_employee_id or admin_name:
        hitl_inspector = format_worker_label(admin_employee_id, admin_name)
    else:
        hitl_inspector = "HITL 관리자"

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

        # 검수자의 BBox 채택/제외/좌표수정/신규추가 반영. 제외는 삭제가 아니라 표식만 남긴다(감사 추적 유지, 재학습 라벨 재사용 가능성 보존).
        _logs = dict(job.agent_logs or {})
        _defects = list(_logs.get("defects") or [])
        has_bbox_ui_edits = bool(
            item.excludedDefectIndexes or item.adoptedCandidateIndexes
            or item.editedBboxes or item.addedBboxes
        )

        if has_bbox_ui_edits:
            _defects = apply_bbox_edits(_defects, list(_logs.get("yolo_candidates") or []), item)
            _logs["defects"] = _defects
            _logs["hitl_bbox_edit"] = {
                "excluded": item.excludedDefectIndexes or [],
                "adopted": item.adoptedCandidateIndexes or [],
                "edited_bboxes": item.editedBboxes or [],
                "added_bboxes": item.addedBboxes or [],
                "edited_by": hitl_inspector,
            }
            job.agent_logs = _logs

            # BBox 편집분으로 점수를 재산정한다. 재산정 결과와 Critic Stage A 재검증은 _logs["hitl_revalidation"]에 1차 판독과 분리해 기록한다 (배경: 33번 문서).
            from app.models.wms import now_kst
            scored = [d for d in _defects if not d.get("hitl_excluded")]
            hitl_revalidation: Dict[str, Any] = {
                "revalidated_by": hitl_inspector,
                "revalidated_at": now_kst().isoformat(),
            }
            try:
                from app.ai.agents import policy_agent
                recomputed = policy_agent({
                    "defects": scored,
                    "book_title": _logs.get("book_title") or "",
                })
                if recomputed.get("ubci_score") is not None:
                    job.ubci_score = recomputed["ubci_score"]
                    hitl_revalidation["policy_score"] = recomputed["ubci_score"]
                    hitl_revalidation["policy_text"] = recomputed.get("policy_text")
                    _logs["hitl_bbox_edit"]["recomputed_score"] = recomputed["ubci_score"]
            except Exception as e:
                logger.error(f"BBox 편집 후 점수 재산정 실패 (판정은 유지): {e}")
                hitl_revalidation["policy_error"] = str(e)

            try:
                from app.ai.agents import critic_stage_a_integrity_check
                stage_a_issues = critic_stage_a_integrity_check(scored, len(job.image_urls or []), job.ubci_score)
                hitl_revalidation["critic_stage_a_passed"] = not stage_a_issues
                hitl_revalidation["critic_stage_a_issues"] = stage_a_issues
                if stage_a_issues:
                    logger.warning(f"HITL 편집 후 Critic Stage A 정합성 위반 (job={job.id}): {stage_a_issues}")
            except Exception as e:
                logger.error(f"HITL 편집 후 Critic Stage A 재검증 실패 (판정은 유지): {e}")
                hitl_revalidation["critic_stage_a_error"] = str(e)

            _logs["hitl_revalidation"] = hitl_revalidation
            job.agent_logs = _logs

        # AuditLog(및 그 소스를 읽는 research/export-dataset 재학습 파이프라인)에 남길 최종 BBox 좌표를 여기서 다시 조립한다. 프론트가 보내는 item.defectCoordinates는 모달을 연 시점의 agent_logs.defect_coordinates 스냅샷이라 위 exclude/adopt/edit 결과를 반영하지 못한다 - 그 값을 그대로 감사 로그에 남기면 관리자가 오탐이라고 제외한 BBox까지 "검증 완료" 라벨로 재학습 데이터에 흘러간다. build_defect_coordinates는 파이프라인 완료 시점에도 쓰는 동일 함수라 포맷이 항상 일치한다.
        from app.ai.langgraph_wrapper import LangGraphInspectionWrapper
        final_defect_coordinates = LangGraphInspectionWrapper.build_defect_coordinates(
            _defects, job.image_urls or []
        )
        # 최종 BBox 좌표를 job.agent_logs에도 동기화한다 (감사 로그와 같은 값 유지, 배경: 33번 문서).
        _logs["defect_coordinates"] = final_defect_coordinates
        job.agent_logs = _logs

        # Determine new status based on decision
        if item.decision.startswith("APPROVE"):
            job.status = JobStatusEnum.APPROVED
            # HITL 최종 결재 승인 시: 창고 보관 랙(Zone B-12-4 등) 위치 할당 및 재고(InventoryUsedItem) 편입
            from app.domains.inventory.service import assign_rack_location_after_inspection
            from app.models.wms import clamp_ubci_score_to_grade
            target_grade = item.targetGrade or (job.agent_logs.get("suggested_grade") if job.agent_logs else "NORMAL")
            # [2026-08-06 수정] 관리자가 등급을 하향/상향 확정하면 점수도 확정 등급의 공식 경계 구간으로 재산정한다. 종전에는 AI 산출 점수(예: 100)를 그대로 넘겨 "UBCI 100점 (NORMAL 등급)" 모순 표기 + 동적 가격의 상태 보정이 MINT 기준으로 계산되는 문제가 있었다. 재산정은 보증서 생성보다 먼저 수행한다 (보증서 본문의 점수/등급 표기가 확정값을 따르도록).
            job.ubci_score = clamp_ubci_score_to_grade(job.ubci_score, target_grade)
            # lpn_barcode가 없으면 새로 채번한다.
            from app.domains.inventory.service import generate_next_lpn_barcode
            lpn = (job.agent_logs.get("lpn_barcode") if job.agent_logs else None) or generate_next_lpn_barcode(session, zone="B")
            if not (job.agent_logs and job.agent_logs.get("lpn_barcode")):
                job.agent_logs = {**(job.agent_logs or {}), "lpn_barcode": lpn}
            # HITL 승인 건의 보증서 본문 생성(GPT-4o-mini, build_certificate_document)을 더 이상 이 요청 스레드에서 동기 호출하지 않는다. 종전에는 매 승인 건마다 여기서 LLM을 호출했는데, 합성 시드 데이터 다건을 한 번에 승인하는 요청에서 순차 LLM 호출이 쌓여 wms-secret-api 컨테이너가 OOM(exit 137)으로 죽었다. 등급 확정·랙 배정·재고 편입은 그대로 이 자리에서 동기로 끝내고, 보증서 문서화만 커밋 후 워커로 큐잉한다 (app/worker/tasks.py generate_hitl_certificate - 판정/집행 분리 원칙 준수).
            approved_job_ids_for_cert.append(str(job.id))

            try:
                cert_code = str(job.id)[:6].upper()
                cert_url = f"/certificate/CERT-20260728-{cert_code}"
                assign_rack_location_after_inspection(
                    session,
                    lpn_barcode=lpn,
                    final_grade=target_grade,
                    book_id=job.book_id,
                    # clamp_ubci_score_to_grade가 위에서 항상 확정 등급 구간의 정수를 보장한다
                    ubci_score=job.ubci_score,
                    source_job_id=str(job.id),
                    certificate_url=cert_url,
                    inspection_source="HITL",
                    inspected_by=hitl_inspector,
                )
            except Exception as ex:
                logger.error(f"Failed to assign rack location: {ex}")
        elif item.decision.startswith("REJECT"):
            job.status = JobStatusEnum.REJECTED
            # 승인(APPROVE) 분기와 달리 반려 분기는 랙 배정 자체를 호출하지 않아, HITL에서 반려된 건은 Zone E(격리/폐기) 배정도 안 되고 InventoryUsedItem row도 안 만들어져 실물 추적이 안 되고 있었다 - 자동 반려 경로(worker/tasks.py)와 동일하게 Zone E 격리 랙 배정을 호출하도록 교정.
            from app.domains.inventory.service import assign_rack_location_after_inspection, generate_next_lpn_barcode
            from app.models.wms import clamp_ubci_score_to_grade
            lpn = (job.agent_logs.get("lpn_barcode") if job.agent_logs else None) or generate_next_lpn_barcode(session, zone="B")
            if not (job.agent_logs and job.agent_logs.get("lpn_barcode")):
                job.agent_logs = {**(job.agent_logs or {}), "lpn_barcode": lpn}
            # 반려 확정도 승인 분기와 동일하게 점수를 확정 등급(REJECT) 구간으로 재산정한다 (검수 내역 목록의 점수-등급 모순 방지, 기존 or 40 임의 폴백 제거).
            job.ubci_score = clamp_ubci_score_to_grade(job.ubci_score, "REJECT")
            try:
                assign_rack_location_after_inspection(
                    session,
                    lpn_barcode=lpn,
                    final_grade="REJECT",
                    final_status="REJECTED",
                    book_id=job.book_id,
                    ubci_score=job.ubci_score,
                    source_job_id=str(job.id),
                    inspection_source="HITL",
                    inspected_by=hitl_inspector,
                )
            except Exception as ex:
                logger.error(f"Failed to assign rack location (reject): {ex}")
            rejected_job_ids.append(str(job.id))
        elif item.decision in ["RE_CHECK", "AI_REINSPECT"]:
            # 존재하지 않는 app.domains.returns.service.process_inspection을 import해서 실제로 호출되면 100% ImportError로 죽던 코드였다 (Pipeline A/B 통합 이전의 잔재). 상태만 PENDING으로 되돌리고 아무것도 재큐잉하지 않아 작업이 그대로 멈춰있던 문제도 함께 수정 - /admin/hitl/{job_id}/re-inspect와 동일하게 Celery로 재큐잉한다. was_hitl은 재큐잉 전 원래 상태(previous_state) 기준.
            was_hitl = _is_hitl_required(previous_state)
            job.status = JobStatusEnum.PENDING
            job.retry_count += 1
            session.add(job)
            session.commit()

            from app.worker.tasks import process_inspection
            try:
                process_inspection.delay(str(job.id), was_hitl=was_hitl)
            except Exception as e:
                logger.warning(f"Celery 재큐잉 실패, 인프로세스로 폴백: {e}")
                import threading
                threading.Thread(
                    target=process_inspection,
                    args=(str(job.id),),
                    kwargs={"was_hitl": was_hitl},
                    daemon=True,
                ).start()
        else:
            raise BadRequestException(f"Unknown decision: {item.decision}")
        
        # Save Agent Logs / Comments
        # 기존에는 job.agent_logs 딕셔너리를 제자리 변경(in-place mutation)했는데, SQLAlchemy는 JSONB 컬럼의 제자리 변경을 감지하지 못해(MutableDict 미적용) UPDATE문에 아예 포함되지 않았다. 그 결과 관리자의 결정/등급/메모가 DB에 한 번도 저장된 적이 없다. 새 dict를 할당해 변경을 확실히 감지시킨다.
        job.agent_logs = {
            **(job.agent_logs or {}),
            "admin_decision": item.decision,
            "admin_comment": item.reasonComment,
            "primary_reason_code": item.primaryReasonCode,
            "target_grade": item.targetGrade,
        }
        
        session.add(job)
        
        valid_admin_id = _resolve_admin_audit_id(current_admin, session)

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
            defect_coordinates=final_defect_coordinates,
            review_duration_ms=item.reviewDurationMs
        )
        session.add(audit)
        audit_logs.append(audit)
        processed_count += 1
        
    session.commit()

    # 반려(매입 불가) 확정 건은 커밋 완료 후 Restock 판정 그래프를 비동기로 태워 자동 발주 제안(order_proposals)을 생성한다. 커밋 전에 큐잉하면 태스크가 새 세션에서 primary_reason_code가 저장되기 전의 agent_logs를 읽는 레이스가 생긴다. 라우터는 큐잉만 하고 즉시 응답한다 (판정/집행 분리 - 관리자 화면은 LLM을 기다리지 않는다).
    if rejected_job_ids:
        from app.worker.tasks import enqueue_restock_proposal
        for rejected_id in rejected_job_ids:
            enqueue_restock_proposal(rejected_id)

    # 승인 확정 건의 보증서 생성(GPT-4o-mini)도 같은 이유로 커밋 후 워커에 큐잉한다. 같은 요청 안에서 N건을 동기로 돌리다 OOM으로 죽은 사고의 재발 방지.
    if approved_job_ids_for_cert:
        from app.worker.tasks import enqueue_hitl_certificate
        for approved_id in approved_job_ids_for_cert:
            enqueue_hitl_certificate(approved_id, hitl_inspector)

    return {
        "status": "success",
        "processed_count": processed_count,
        "message": "HITL overrides successfully applied."
    }



@router.post("/{job_id}/re-inspect")
def trigger_ai_reinspection(job_id: str, session: Session = Depends(get_db)):
    """
    [Master AI Re-inspection Engine]
    HITL 관리자가 재검수를 요청하면, 앱 전체가 공유하는 단일 Celery 파이프라인(app.worker.tasks.process_inspection - WBF+GPT-4o Vision Agent, Redlock+DLQ)으로 재큐잉한다.
    과거에는 요청을 블로킹하며 이미 폐기된 app.ai.graph.build_wms_graph()를 동기 실행했고, 존재하지 않는 JobStatusEnum.INSPECTED 값을 대입하는 등 실행 자체가 불가능한 버그가 있었다 (Pipeline A/B 통합 작업 중 발견). /inbound/retry와 동일한 비동기 재큐잉 패턴으로 통일한다.
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

    # 잘못된 ID로 재검수를 눌러도 404가 아니라 "DB의 아무 검수 작업 한 건"을 다시 큐에 밀어넣어, 전혀 관계없는 도서가 재검수되고 그 등급이 덮어써졌다. 정직하게 404를 낸다.
    if not job:
        raise NotFoundException(f"ReturnJob with ID {job_id} not found")

    if not job.image_urls:
        raise BadRequestException("No images found for this job.")

    # was_hitl을 process_inspection에 태스크 인자로 직접 넘긴다.
    was_hitl = str(job.status) == JobStatusEnum.HITL_REQUIRED.value

    job.status = JobStatusEnum.PENDING.value
    job.retry_count = (job.retry_count or 0) + 1
    session.add(job)
    session.commit()

    from app.worker.tasks import process_inspection
    try:
        process_inspection.delay(str(job.id), was_hitl=was_hitl)
    except Exception as e:
        import threading
        threading.Thread(
            target=process_inspection,
            args=(str(job.id),),
            kwargs={"was_hitl": was_hitl},
            daemon=True,
        ).start()

    return {
        "status": "queued",
        "message": "재검수 작업이 Celery 큐에 등록되었습니다. 진행 상황은 SSE로 확인하세요.",
        "job_id": str(job.id),
    }

@router.get("/{job_id}/assist")
def get_hitl_assist_briefing(
    job_id: str,
    session: Session = Depends(get_db),
    current_admin = Depends(admin_only),
):
    """
    [RAG 기능 B] HITL 결재 관리자 보조 브리핑.

    애매한 건을 결재해야 하는 관리자에게 판단 재료 세 가지를 모아 제공한다.
      1) 관련 규정 조항  - ChromaDB 벡터 검색 (policy_data_master.yaml 71청크)
      2) 유사 과거 판정  - 같은 결함 유형 + 인접 UBCI 점수대의 확정 이력 (SQL)
      3) 쟁점 정리       - 위 둘을 근거로 GPT-4o-mini가 작성

    LLM은 결재를 대신 결정하지 않는다. 최종 판단 권한은 관리자에게 있으며, 응답의 disclaimer 필드가 이를 명시한다.
    """
    from app.models.wms import Book
    from app.core.rag_service import build_hitl_briefing

    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise BadRequestException(f"Invalid job_id UUID: {job_id}")

    job = session.get(ReturnJob, job_uuid)
    if not job:
        raise NotFoundException(f"ReturnJob with ID {job_id} not found")

    logs = job.agent_logs or {}
    defects = logs.get("defects") or []
    book = session.get(Book, job.book_id) if job.book_id else None
    book_title = book.title if book else ""

    # --- 유사 과거 판정 사례 (RAG가 아니라 SQL - 우리 실제 결재 이력이 근거다) ---
    defect_types = {str(d.get("type")) for d in defects if isinstance(d, dict) and d.get("type")}
    similar_cases: List[Dict[str, Any]] = []

    finished = session.exec(
        select(ReturnJob)
        .where(ReturnJob.id != job_uuid)
        .where(ReturnJob.status.in_([JobStatusEnum.APPROVED.value, JobStatusEnum.REJECTED.value]))
        .order_by(ReturnJob.updated_at.desc())
        .limit(80)
    ).all()

    for past in finished:
        past_logs = past.agent_logs or {}
        past_types = {
            str(d.get("type"))
            for d in (past_logs.get("defects") or [])
            if isinstance(d, dict) and d.get("type")
        }
        # 결함 유형이 하나라도 겹치거나, UBCI 점수가 ±8점 이내로 근접한 건을 유사 사례로 본다.
        score_close = (
            job.ubci_score is not None
            and past.ubci_score is not None
            and abs(past.ubci_score - job.ubci_score) <= 8
        )
        if not (past_types & defect_types) and not score_close:
            continue

        past_book = session.get(Book, past.book_id) if past.book_id else None
        similar_cases.append({
            "lpn": past_logs.get("lpn_barcode"),
            "book_title": past_book.title if past_book else "미상",
            "ubci_score": past.ubci_score,
            "final_status": past.status,
            "defect_types": sorted(past_types),
            "admin_decision": past_logs.get("admin_decision"),
            "admin_comment": past_logs.get("admin_comment"),
            "decided_at": to_kst_iso(past.updated_at),
        })
        if len(similar_cases) >= 5:
            break

    briefing = build_hitl_briefing(
        book_title=book_title,
        ubci_score=job.ubci_score,
        suggested_grade=logs.get("suggested_grade"),
        defects=defects,
        critic_reason=logs.get("critic_text") or logs.get("supervisor_rationale") or logs.get("repair_directive"),
        similar_cases=similar_cases,
    )

    return {
        "job_id": str(job.id),
        "lpn_barcode": logs.get("lpn_barcode"),
        "book_title": book_title,
        "ubci_score": job.ubci_score,
        "suggested_grade": logs.get("suggested_grade"),
        # Policy Agent가 감점 시점에 이미 붙여둔 근거 조항 (있으면 그대로 노출)
        "deduction_basis": logs.get("deduction_basis") or [],
        **briefing,
    }


def to_kst_iso(dt) -> Optional[str]:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


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
        .order_by(ReturnJob.updated_at.desc())  # 처리(결재) 시간 역순
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
                updated_at=job.updated_at.isoformat() if job.updated_at else None,
                # 완료 화면이므로 결재 코멘트 우선
                human_issue_notes=_latest_admin_memo(job.agent_logs, prefer_recall=False),
            )
        )
    return output
