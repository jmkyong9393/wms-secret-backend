import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from sqlmodel import Session, select


from app.core.database import engine
from app.models.wms import ReturnJob


logger = logging.getLogger(__name__)

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
    job.updated_at = datetime.utcnow()

    session.add(job)
    session.commit()
    session.refresh(job)

# Celery 작업 시작 시 ReturnJob을 조회하고 PROCESSING 상태로 변경하는 함수
def prepare_processing_job(
        return_job_id: str,
        celery_task_id: str,
) -> Tuple[uuid.UUID, uuid.UUID, str, str]:
    
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
            job.updated_at = datetime.utcnow()
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
            job.image_url or "",
        )
    

# LangGraph 결과와 WMS 호출 결과를 ReturnJob에 저장하는 함수
def save_inspection_result(
        return_job_id: uuid.UUID,
        ai_result: Dict[str,Any],
        final_status: str,
        extra_logs: Dict[str, Any],
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
        job.agent_logs = {
            **(ai_result.get("agent_logs") or {}),
            **extra_logs,
        }
        job.status = final_status
        job.updated_at = datetime.utcnow()
     

        # 최종 DB 저장
        session.add(job)
        session.commit()
        session.refresh(job)

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
        job.updated_at = datetime.utcnow()

        session.add(job)
        session.commit()
        session.refresh(job)

        return job