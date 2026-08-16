import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select


from app.db.session import engine
from app.models.wms import ReturnJob
from app.models.wms import now_kst


logger = logging.getLogger(__name__)

# HITL 이관 이력 보관 상한. 같은 건이 재검수를 반복해도 JSONB가 무한히 커지지 않게 자른다.
ESCALATION_HISTORY_LIMIT = 20

# return_job_id 기준으로 ReturnJob 조회 함수
def find_return_job_by_id(
        session: Session,
        return_job_id: uuid.UUID,
) -> Optional[ReturnJob]:
    statement = (
        select(ReturnJob)
        .where(ReturnJob.id == return_job_id)
    )

    return session.exec(statement).first()

# ReturnJob 상태 변경 함수
def update_return_job_status(
        session: Session,
        job: ReturnJob,
        status: str,
) -> None:
    job.status = status
    job.updated_at = now_kst()

    session.add(job)
    session.commit()
    session.refresh(job)

# Celery 작업 시작 시 ReturnJob을 조회하고 PROCESSING 상태로 변경하는 함수
def prepare_processing_job(
        return_job_id: str,
        celery_task_id: str,
) -> Tuple[uuid.UUID, uuid.UUID, str, List[str], Dict[str, Any]]:
    
    parsed_return_job_id = uuid.UUID(return_job_id)
    
    with Session(engine) as session:
        job = find_return_job_by_id(
            session=session,
            return_job_id=parsed_return_job_id
        )

        if job is None:
            raise ValueError(
                f"ReturnJob을 찾을 수 없습니다. return_job_id={return_job_id}"
            )
        
        # task_id 저장
        if job.task_id is None:
            job.task_id = celery_task_id
            job.updated_at = now_kst()
            session.add(job)
            session.commit()
            session.refresh(job)

        # ReturnJob status = PROCESSING 변경
        update_return_job_status(
            session=session,
            job=job,
            status="PROCESSING",
        )

        return(
            job.id,
            job.book_id,
            str(job.order_id),
            job.image_urls or [],
            job.agent_logs or {},
        )
    

# LangGraph 결과와 WMS 호출 결과를 ReturnJob에 저장하는 함수
def save_inspection_result(
        return_job_id: uuid.UUID,
        ai_result: Dict[str,Any],
        final_status: str,
        extra_logs: Dict[str, Any],
        latency_ms: Optional[int] = None,
) -> ReturnJob:
    
    with Session(engine) as session:
        job = find_return_job_by_id(
            session=session,
            return_job_id=return_job_id,
        )

        if job is None:
            raise ValueError(
                f"ReturnJob을 찾을 수 없습니다. return_job_id={return_job_id}"
            )
        

        # AI 결과를 ReturnJob에 저장
        job.ubci_score = ai_result.get("ubci_score")
        job.final_report = ai_result.get("final_report")
        # [수정 이력] 기존에는 job.agent_logs를 AI 결과로 완전히 덮어써서, 입고 시점에
        # inbound/router.py가 심어둔 lpn_barcode/book_category/book_metadata가 검수 완료와
        # 동시에 사라졌다. HITL 관리자 승인 화면이 이 lpn_barcode를 못 찾아 하드코딩된
        # fallback LPN으로 랙 배정을 시도하게 되는 원인이었다 - 원본 값을 보존하도록 교정.
        prev_logs = job.agent_logs or {}
        job.agent_logs = {
            **prev_logs,
            **(ai_result.get("agent_logs") or {}),
            **extra_logs,
        }

        # AI가 HITL로 이관한 사실을 append-only로 남긴다.
        # reason_code는 매 실행마다 덮어써지므로 이관 시점의 사유가 관리자 결재 후 사라진다.
        # 재학습 정답지는 "언제 왜 사람에게 넘겼는가"가 남아야 쌓이므로 이력을 따로 적재한다.
        if final_status == "HITL_REQUIRED":
            merged = job.agent_logs
            history = list(prev_logs.get("escalations") or [])
            defects = merged.get("defects") or []
            history.append({
                "at": now_kst().isoformat(),
                "by": "AI",                       # 관리자 소환은 admin_audit_logs에 남는다
                # ai_result 최상위가 최신이다. 워커의 HITL 잠금 분기가 여기만 갱신하고
                # agent_logs 안쪽은 그래프가 낸 원래 값("OK" 등)으로 남아 있다.
                "reason_code": ai_result.get("reason_code") or merged.get("reason_code"),
                "ubci_score": ai_result.get("ubci_score"),
                "final_grade": ai_result.get("final_grade"),
                "defect_count": len(defects) if isinstance(defects, list) else 0,
                "defect_types": sorted({
                    d.get("type") for d in defects
                    if isinstance(d, dict) and d.get("type")
                }),
                "rationale": merged.get("supervisor_rationale") or merged.get("critic_text"),
                "retry_count": job.retry_count,
            })
            job.agent_logs = {**merged, "escalations": history[-ESCALATION_HISTORY_LIMIT:]}
        # HITL 수동 검수 대기 건에 대한 AI 재검수인 경우, 사람 관리자의 결재 전까지 HITL_REQUIRED 상태를 보존합니다!
        if job.status == "HITL_REQUIRED":
            job.status = "HITL_REQUIRED"
        else:
            job.status = final_status
        job.updated_at = now_kst()

        # 워커가 이 건을 집어 판정을 저장하기까지 걸린 시간. 큐 대기는 포함하지 않는다.
        # 종전에는 컬럼만 선언돼 있고 채우는 코드가 없어 전 건이 NULL이었고, 검수 소요를
        # created_at~updated_at 차이로 추정할 수밖에 없었다(HITL 대기까지 섞이는 값).
        if latency_ms is not None:
            job.latency_ms = latency_ms

        # 최종 DB 저장
        session.add(job)
        session.commit()
        session.refresh(job)

        # [Supervisor Agent 최종 판정 승인 완료 시점] 
        # Critic Agent ➔ Supervisor Agent 합의가 이의 없이 끝났을 때만 창고 보관 랙(Zone A-E) 최종 할당 실행!
        if final_status in ["COMPLETED", "APPROVED"]:
            from app.domains.inventory.service import assign_rack_location_after_inspection
            final_grade = ai_result.get("final_grade") or "MINT"
            lpn_barcode = ai_result.get("lpn_barcode")
            if lpn_barcode:
                try:
                    assign_rack_location_after_inspection(session, lpn_barcode, final_grade)
                except Exception as e:
                    logger.warning(f"Supervisor 승인 후 랙 위치 자동 할당 건너뜀/오류: {e}")

        return job
    
# 최종 실패시 예외 처리 (재시도 이후 실패 또는 Langraph 실행 오류 등)
def save_inspection_failed(
        return_job_id: uuid.UUID,
        celery_task_id: str,
        error: Exception,
) -> Optional[ReturnJob]:
    with Session(engine) as session:
        job = find_return_job_by_id(
            session=session,
            return_job_id=return_job_id,
        )

        if job is None:
            logger.error(
                "FAILED 상태 저장 실패: ReturnJob을 찾을 수 없습니다. return_job_id=%s",
                return_job_id,
            )
            return None

        job.status = "FAILED"
        job.final_report = "AI 검수 처리 중 오류가 발생했습니다."
        job.agent_logs = {
            **(job.agent_logs or {}),
            "error": {
                "type": type(error).__name__,
                "message": str(error),
                "task_id": celery_task_id,
            },
        }
        job.updated_at = now_kst()

        session.add(job)
        session.commit()
        session.refresh(job)

        return job