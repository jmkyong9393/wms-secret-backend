import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

import httpx
from pottery import Redlock
from pottery.exceptions import QuorumNotAchieved
from redis import Redis

from app.models.wms import ReturnJob
from app.core.celery_app import celery_app
from app.services.langgraph_wrapper import LangGraphInspectionWrapper
from app.services.redis_pubsub import publish_return_job_event
from app.services.return_job_service import (
    prepare_processing_job,
    save_inspection_failed,
    save_inspection_result,
)
from app.services.wms_client import (
    call_wms_approve_api,
    call_wms_reject_api,
)

logger = logging.getLogger(__name__)

# ==========================================
# OpenTelemetry Celery 분산 추적 (SCI 논문 데이터 수집용)
# ==========================================
try:
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    provider = TracerProvider()
    processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    CeleryInstrumentor().instrument()
    print("OpenTelemetry Celery Instrumentation enabled.")
except ImportError:
    print("OpenTelemetry not installed. Skipping tracing setup.")

# Redis Client Setup (for DLQ and Redlock)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = Redis.from_url(REDIS_URL)

DLQ_KEY = "wms:dlq:inspection_tasks"

def push_to_dlq(task_id: str, return_job_id: str, error_msg: str, retries: int) -> None:
    """
    Celery 최대 재시도(Max Retries) 초과 시, 작업을 버리지 않고 
    Redis 기반의 Dead Letter Queue(DLQ)에 적재하여 데이터 유실을 방어합니다.
    추후 관리자가 대시보드(Admin UI)에서 DLQ 목록을 확인하고 원클릭 수동 재처리(Re-queue)를 할 수 있도록 설계되었습니다.
    """
    dlq_payload = {
        "task_id": task_id,
        "return_job_id": return_job_id,
        "error": error_msg,
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "retries": retries
    }
    
    try:
        # 우측 끝에 밀어넣기 (큐 형태 보장)
        redis_client.rpush(DLQ_KEY, json.dumps(dlq_payload))
        logger.error(f"[DLQ] Task {task_id} for job {return_job_id} safely pushed to DLQ.")
    except Exception as e:
        # 최악의 경우 Redis마저 뻗었다면 시스템 치명적 결함이므로 로그로 흔적을 짙게 남김
        logger.critical(f"FATAL: Failed to push DLQ! task={task_id}, job={return_job_id}, err={str(e)}")



# PROCESSING 상태 변경 후 프론트에 진행 상태를 전달하는 Pub/Sub 이벤트 발행 함수
def publish_processing_event(
        return_job_id: uuid.UUID,
        celery_task_id: str,
) -> None:
    publish_return_job_event(
        return_job_id=str(return_job_id),
        event={
            "return_job_id": str(return_job_id),
            "task_id": celery_task_id,
            "status": "PROCESSING",
            "progress": 50,
        },
    )

# 최종 검수 결과(APPROVED/REJECTED)를 프론트에 전달하는 Pub/Sub 이벤트 발행 함수
def publish_final_event(
        job: ReturnJob,
        celery_task_id: str,
) -> None:
    publish_return_job_event(
        return_job_id=str(job.id),
        event={
            "return_job_id": str(job.id),
            "task_id": celery_task_id,
            "status": job.status,
            "progress": 100,
            "ubci_score": job.ubci_score,
        },
    )

# Worker 처리 실패 시 FAILED 상태를 프론트에 전달하는 Pub/Sub 이벤트 발행 함수
def publish_failed_event(
        return_job_id: uuid.UUID,
        celery_task_id: str,
        error: Exception,
) -> None:
    publish_return_job_event(
        return_job_id=str(return_job_id),
        event={
            "return_job_id": str(return_job_id),
            "task_id": celery_task_id,
            "status": "FAILED",
            "progress": 100,
            "error_message": str(error),
        },
    )

# AI decision 결과에 따라 WMS API를 호출하고 최종 ReturnJob status를 결정하는 함수
def execute_wms_action(
        decision: str,
        book_id: uuid.UUID,
) -> Tuple[str, Dict[str, Any]]:
    
    # APPROVE인 경우
    if decision == "APPROVE":

        wms_result = call_wms_approve_api(
            book_id = str(book_id),
        )
        return "APPROVED", {
            "wms_result" : wms_result,
        }
    
    # REJECT인 경우
    if decision == "REJECT":
        reject_reason = "AI_INSPECTION_REJECTED"
            
        wms_result = call_wms_reject_api(
            book_id = str(book_id),
            reason = reject_reason,
        )
        
        return "REJECTED", {
            "wms_result": wms_result,
            "reject_reason": reject_reason,
        }

    raise ValueError(f"Unknown decision: {decision}")



# celery task
@celery_app.task(
        bind=True,
        name="app.worker.process_inspection",
        max_retries=3,
        # 지수 백오프는 코드 내부에 `retry(countdown=...)` 로직으로 직접 구현하여 우아하게 제어함.
)
def process_inspection(self, return_job_id: str) -> Dict[str, Any]:
    """
    LangGraph 기반 AI 비전 검수 메인 워커.
    
    [핵심 방어 로직 적용 (Resilience Architecture)]
    1. Redlock (분산 락): KEDA 스케일 아웃 환경에서 다중 워커가 동일 건을 중복 처리하지 못하도록 Lock 점유.
    2. Exponential Backoff (지수 백오프): LLM API Rate Limit(429)이나 외부 망 통신 장애 발생 시 우회.
    3. DLQ (Dead Letter Queue): 모든 재시도 실패 시 데이터가 증발하지 않도록 큐 격리 (Zero Data Loss).
    """
    celery_task_id = self.request.id
    parsed_return_job_id = uuid.UUID(return_job_id)
    
    # 1. 분산 락(Distributed Lock) 획득
    # 동일한 return_job_id 에 대한 작업을 다른 워커 파드가 동시에 가져가지 못하도록 락을 확보함.
    # auto_release_time 을 300초(5분)로 두어 워커가 크래시 나더라도 락이 자연 해제되게 방어 설계.
    lock_key = f"lock:inspection:{return_job_id}"
    lock = Redlock(key=lock_key, masters={redis_client}, auto_release_time=300)
    
    try:
        # blocking=False: 누군가 이미 락을 쥐고 있으면 쿨하게 포기 (멱등성 보장)
        if not lock.acquire(blocking=False):
            logger.warning(f"Task {celery_task_id} skipped. Job {return_job_id} is already locked by another worker.")
            return {"status": "SKIPPED", "reason": "LOCKED"}
            
        logger.info(f"process_inspection started. task_id={celery_task_id} return_job_id={return_job_id}")

        # 2. ReturnJob 조회 및 PROCESSING 상태 변경
        (
            parsed_return_job_id,
            book_id,
            order_id,
            image_url,
        ) = prepare_processing_job(
            return_job_id=return_job_id,
            celery_task_id=celery_task_id,
        )

        publish_processing_event(
            return_job_id=parsed_return_job_id,
            celery_task_id=celery_task_id,
        )

        # 3. LangGraph Multi-Agent 실행
        langgraph_wrapper = LangGraphInspectionWrapper()
        ai_result = langgraph_wrapper.run_inspection(
            order_id = order_id,
            image_url = image_url
        )

        # 4. AI decision에 따라 WMS API 호출
        decision = ai_result.get("decision")
        if decision not in ["APPROVE", "REJECT"]:
            raise ValueError(f"Unknown AI decision: {decision}")

        final_status, extra_logs = execute_wms_action(
            decision=decision,
            book_id=book_id,
        )

        # 5. AI 결과와 WMS 결과를 ReturnJob에 저장
        job = save_inspection_result(
            return_job_id=parsed_return_job_id,
            ai_result=ai_result,
            final_status=final_status,
            extra_logs=extra_logs,
        )

        # Redis Pub/Sub에 최종 상태 이벤트 발행
        publish_final_event(
            job=job,
            celery_task_id=celery_task_id,
        )

        logger.info(
            f"process_inspection completed gracefully. task_id={celery_task_id} return_job_id={job.id} status={job.status}"
        )

        return {
            "task_id": celery_task_id,
            "return_job_id": str(job.id),
            "order_id": str(job.order_id),
            "book_id": str(job.book_id),
            "status": job.status,
            "ubci_score": job.ubci_score,
        }
        
    except QuorumNotAchieved:
        logger.warning(f"Redlock quorum not achieved for {return_job_id}")
        return {"status": "SKIPPED", "reason": "LOCK_FAILED"}

    except httpx.HTTPError as error:
        # 네트워크/외부 API 에러 발생 시 Exponential Backoff (지수 백오프) 처리
        retries = self.request.retries
        if retries < self.max_retries:
            # 2초, 4초, 8초... 순으로 기하급수적 대기 시간 적용
            backoff_delay = 2 ** retries
            logger.warning(
                f"[Rate Limit / HTTP Error] Retrying task {celery_task_id} in {backoff_delay}s... "
                f"({retries + 1}/{self.max_retries}) | Err: {str(error)}"
            )
            # 예외를 던지며 retry 큐로 재진입 (이때 finally 블록이 실행되며 락 해제됨)
            raise self.retry(exc=error, countdown=backoff_delay)
            
        # 백오프를 모두 소진했음에도 실패한 경우 (Max Retries Exhausted) -> DLQ 격리
        logger.exception(f"HTTP retries exhausted for {return_job_id}. Sending to DLQ.")
        push_to_dlq(celery_task_id, return_job_id, str(error), retries)
        
        failed_job = save_inspection_failed(parsed_return_job_id, celery_task_id, error)
        if failed_job is not None:
            publish_failed_event(failed_job.id, celery_task_id, error)
            
        raise
    
    except Exception as error:
        # 예상치 못한 런타임 에러의 경우 바로 DLQ 격리 및 실패 처리
        logger.exception(f"Unexpected error in process_inspection. task_id={celery_task_id}, sending to DLQ.")
        push_to_dlq(celery_task_id, return_job_id, str(error), self.request.retries)
        
        failed_job = save_inspection_failed(parsed_return_job_id, celery_task_id, error)
        if failed_job is not None:
            publish_failed_event(failed_job.id, celery_task_id, error)
            
        raise

    finally:
        # Exception이 발생하든, 정상 처리되든 락을 무조건 반환하여 Deadlock 방지
        try:
            lock.release()
        except Exception:
            # 락이 만료되었거나 이미 풀린 상태면 무시
            pass




    

    


