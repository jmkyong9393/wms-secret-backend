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


def _hitl_special_notes(
    agent_logs: Dict[str, Any],
    item: "HitlOverrideRequest",
    inspector: str,
) -> str:
    """HITL 결재 근거를 보증서 생성기에 넘길 특이사항 문장으로 조립한다.

    자동 승인 건과 달리 HITL 건은 **사람이 최종 판단**했으므로, 그 사유와 코멘트가
    보증서에 드러나야 한다. 기존 Vision 특이사항이 있으면 뒤에 덧붙인다.
    """
    parts = [f"관리자 수동 검수 확정 (검수자: {inspector})"]
    if item.primaryReasonCode:
        parts.append(f"판정 사유: {item.primaryReasonCode}")
    if item.targetGrade:
        parts.append(f"확정 등급: {item.targetGrade}")
    comment = (item.reasonComment or "").strip()
    if comment:
        parts.append(f"검수자 의견: {comment}")
    prior = (agent_logs.get("special_notes") or "").strip()
    if prior:
        parts.append(f"AI 판독 특이사항: {prior}")
    return " / ".join(parts)

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
    rejected_job_ids = []  # 커밋 후 Restock 판정 그래프(자동 발주 제안)를 태울 반려 확정 건

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
            from app.models.wms import clamp_ubci_score_to_grade
            target_grade = item.targetGrade or (job.agent_logs.get("suggested_grade") if job.agent_logs else "NORMAL")
            # [2026-08-06 수정] 관리자가 등급을 하향/상향 확정하면 점수도 확정 등급의 공식
            # 경계 구간으로 재산정한다. 종전에는 AI 산출 점수(예: 100)를 그대로 넘겨
            # "UBCI 100점 (NORMAL 등급)" 모순 표기 + 동적 가격의 상태 보정이 MINT 기준으로
            # 계산되는 문제가 있었다. 재산정은 보증서 생성보다 먼저 수행한다 (보증서 본문의
            # 점수/등급 표기가 확정값을 따르도록).
            job.ubci_score = clamp_ubci_score_to_grade(job.ubci_score, target_grade)
            lpn = (job.agent_logs.get("lpn_barcode") if job.agent_logs else None) or f"LPN-260728-A002"
            # [2026-08-06 수정] HITL 승인 건의 보증서 본문을 생성한다.
            #
            # 종전에는 아래 cert_url 문자열만 만들어 재고에 저장했고, 그 URL이 가리키는
            # 보증서 본문(job.agent_logs["certificate"])은 **생성된 적이 없었다.**
            # 원인은 그래프 배선이다 - HITL로 이관되면 human_node에서 조기 종료되어
            # report_agent를 타지 않는다(supervisor.py 주석 참조). 그 결과 HITL 승인 건은
            # 상세/보증서 화면에서 링크는 있는데 내용이 없는 상태였다.
            #
            # 관리자가 입력한 사유 코드와 코멘트를 함께 넣어, 자동 승인 건과 달리
            # **사람의 결재 근거가 보증서에 반영**되게 한다.
            try:
                agent_logs = job.agent_logs or {}
                cert_state = {
                    "ubci_score": job.ubci_score,
                    "defects": agent_logs.get("defects") or [],
                    "book_title": agent_logs.get("book_title") or "",
                    "special_notes": _hitl_special_notes(agent_logs, item, hitl_inspector),
                }
                from app.ai.agents import build_certificate_document
                cert_doc = build_certificate_document(cert_state)
                cert_doc["cert_id"] = f"CERT-{datetime.now().strftime('%Y%m%d')}-{str(job.id)[:6].upper()}"
                cert_doc["issued_by"] = "HITL"
                cert_doc["inspected_by"] = hitl_inspector
                # [2026-08-06 수정] HITL 이관 건은 그래프가 human_node에서 조기 종료되어
                # report_agent 노드를 타지 않으므로, 진단 기록(executed_agents)에 Report Agent가
                # "미실행"으로 남는다. 그러나 보증서 생성(build_certificate_document)은 Report
                # Agent와 동일한 작업이고 여기 HITL 결재 시점에 실제로 수행되었으므로, 실행
                # 기록과 서술(report_text)을 함께 남겨 상세 화면 타임라인에 표시되게 한다.
                # (그래프 재배선이 아니라 결재 시점 집행 기록 - 프리즈 규정의 판정/집행 분리 준수)
                executed_agents = list(agent_logs.get("executed_agents") or [])
                if "report_agent" not in executed_agents:
                    executed_agents.append("report_agent")
                # 결재 시점 타임스탬프를 별도 기록 - 진단 타임라인이 검수 시각 하나로 전 행을
                # 찍지 않고, Report Agent 행만 실제 결재(보증서 생성) 시각을 표시할 수 있게 한다.
                from app.models.wms import now_kst
                report_generated_at = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                report_text = (
                    f"HITL 결재 시점 보증서 생성 - {cert_doc['cert_id']} / 결재자 {hitl_inspector} / "
                    f"최종 확정 UBCI {job.ubci_score}점 ({target_grade}) - 관리자 결재 근거가 보증서 특이사항에 반영됨"
                )
                job.agent_logs = {
                    **agent_logs,
                    "certificate": cert_doc,
                    "executed_agents": executed_agents,
                    "report_text": report_text,
                    "report_generated_at": report_generated_at,
                }
                logger.info(f"HITL 보증서 생성 완료: {cert_doc['cert_id']} (job {job.id})")
            except Exception as ex:
                # 보증서 생성 실패가 결재 자체를 막지 않는다. 다만 조용히 넘어가면
                # 다시 "링크만 있고 내용 없는" 상태가 되므로 반드시 로그를 남긴다.
                logger.error(f"HITL 보증서 생성 실패 (job {job.id}): {ex}")

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
            # [수정 이력] 승인(APPROVE) 분기와 달리 반려 분기는 랙 배정 자체를 호출하지 않아,
            # HITL에서 반려된 건은 Zone E(격리/폐기) 배정도 안 되고 InventoryUsedItem row도
            # 안 만들어져 실물 추적이 안 되고 있었다 - 자동 반려 경로(worker/tasks.py)와
            # 동일하게 Zone E 격리 랙 배정을 호출하도록 교정.
            from app.domains.inventory.service import assign_rack_location_after_inspection
            from app.models.wms import clamp_ubci_score_to_grade
            lpn = (job.agent_logs.get("lpn_barcode") if job.agent_logs else None) or f"LPN-260728-A002"
            # [2026-08-06 수정] 반려 확정도 승인 분기와 동일하게 점수를 확정 등급(REJECT) 구간으로
            # 재산정한다 (검수 내역 목록의 점수-등급 모순 방지, 기존 or 40 임의 폴백 제거).
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

    # 반려(매입 불가) 확정 건은 커밋 완료 후 Restock 판정 그래프를 비동기로 태워
    # 자동 발주 제안(order_proposals)을 생성한다. 커밋 전에 큐잉하면 태스크가 새 세션에서
    # primary_reason_code가 저장되기 전의 agent_logs를 읽는 레이스가 생긴다.
    # 라우터는 큐잉만 하고 즉시 응답한다 (판정/집행 분리 - 관리자 화면은 LLM을 기다리지 않는다).
    if rejected_job_ids:
        from app.worker.tasks import enqueue_restock_proposal
        for rejected_id in rejected_job_ids:
            enqueue_restock_proposal(rejected_id)

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

    LLM은 결재를 대신 결정하지 않는다. 최종 판단 권한은 관리자에게 있으며, 응답의
    disclaimer 필드가 이를 명시한다.
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
