import json
import logging
import os
import uuid
from datetime import datetime, timezone
from app.models.wms import now_kst
from typing import Any, Dict, Optional, Tuple

import httpx
import openai
from pottery import Redlock
from pottery.exceptions import QuorumNotAchieved
from redis import Redis
from sqlmodel import Session

from app.models.wms import ReturnJob
from app.core.celery_app import celery_app
from app.ai.langgraph_wrapper import LangGraphInspectionWrapper
from app.core.redis_pubsub import publish_return_job_event
from app.domains.returns.job_service import (
    prepare_processing_job,
    save_inspection_failed,
    save_inspection_result,
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

def _notify_agent_error(return_job_id: str, error_msg: str) -> None:
    """DLQ 격리 발생을 관제 콘솔 알림으로 올린다. 실패해도 DLQ 적재를 방해하지 않는다."""
    try:
        from app.domains.notifications.service import notify_agent_error
        notify_agent_error(job_id=return_job_id, error_message=error_msg)
    except Exception as e:
        logger.warning(f"[Notification] 에이전트 오류 알림 발행 실패: {e}")


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
        "failed_at": now_kst().isoformat(),
        "retries": retries
    }
    
    try:
        # 우측 끝에 밀어넣기 (큐 형태 보장) + 보관정책 원자 적용:
        # 최대 N건 초과 시 오래된 항목부터 절삭(LTRIM), 마지막 적재 후 TTL 갱신(EXPIRE)
        from app.core.config import settings
        with redis_client.pipeline(transaction=True) as pipeline:
            pipeline.rpush(DLQ_KEY, json.dumps(dlq_payload))
            pipeline.ltrim(DLQ_KEY, -settings.INSPECTION_DLQ_MAX_ENTRIES, -1)
            pipeline.expire(DLQ_KEY, settings.INSPECTION_DLQ_TTL_SECONDS)
            pipeline.execute()
        logger.error(f"[DLQ] Task {task_id} for job {return_job_id} safely pushed to DLQ.")
        _notify_agent_error(return_job_id, error_msg)
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
            "message": "AI 검수(WBF 앙상블 + GPT-4o Vision) 진행 중...",
        },
    )

def _summarize_defects(agent_logs: Dict[str, Any]) -> str:
    defects = (agent_logs or {}).get("defects") or []
    types = sorted({d.get("type", "") for d in defects if isinstance(d, dict) and d.get("type")})
    return ", ".join(types) if types else "정상"

# 최종 검수 결과(APPROVED/REJECTED)를 프론트에 전달하는 Pub/Sub 이벤트 발행 함수
def publish_final_event(
        job: ReturnJob,
        celery_task_id: str,
) -> None:
    from app.models.wms import ubci_grade_from_score

    grade = "HITL_REQUIRED" if job.status == "HITL_REQUIRED" else ubci_grade_from_score(job.ubci_score)
    publish_return_job_event(
        return_job_id=str(job.id),
        event={
            "return_job_id": str(job.id),
            "task_id": celery_task_id,
            "status": job.status,
            "progress": 100,
            "message": "완료",
            "grade": grade,
            "ubci_score": job.ubci_score,
            "defect_description": _summarize_defects(job.agent_logs),
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
            "message": f"에러: {str(error)}",
            "grade": "ERROR",
            "ubci_score": 0,
            "error_message": str(error),
        },
    )

# AI decision 결과에 따라 창고 랙 위치를 확정하고 최종 ReturnJob status를 결정하는 함수
#
# [수정 이력] 기존에는 존재하지 않는 내부 WMS HTTP 엔드포인트(call_wms_approve_api/call_wms_reject_api,
# http://api:8000/api/inventory/approve 등 - 어떤 라우터도 이 경로를 서빙하지 않음)를 호출해 항상
# httpx 에러로 실패하던 코드였다. admin/hitl/router.py의 오버라이드 흐름과 동일하게
# assign_rack_location_after_inspection()을 인프로세스로 직접 호출하도록 교체하여 실제로
# InventoryUsedItem 랙 배정이 이뤄지도록 한다.
def execute_wms_action(
        decision: str,
        book_id: uuid.UUID,
        lpn_barcode: Optional[str],
        final_grade: str,
        ubci_score: Optional[int],
        source_job_id: str,
        inbound_worker_id: Optional[str] = None,
        auto_refund_eligible: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    # [수정 이력] HITL 이관(decision="HITL")도 여기서 함께 처리한다. Zone Z(임시적재)에
    # InventoryUsedItem을 미리 만들어 실물이 어디 있는지 추적 가능하게 하고, 관리자가
    # /admin/hitl/override로 최종 승인/반려하면 assign_rack_location_after_inspection이
    # 같은 lpn_barcode의 기존 row를 찾아 최종 랙(A-D 또는 E)으로 옮긴다.
    if decision == "HITL":
        final_status = "HITL_REQUIRED"
    else:
        final_status = "APPROVED" if decision == "APPROVE" else "REJECTED"

    # 매입 반려 확정 = 재고 편입 무산(판매 기회 소실) 이벤트.
    # Restock 판정 그래프를 비동기 태스크로 태워 자동 발주 제안(order_proposals)을 생성한다.
    # 제안 생성 실패가 반려 처리 자체를 막아서는 안 되므로 enqueue만 하고 지나간다.
    if final_status == "REJECTED":
        enqueue_restock_proposal(source_job_id)

    if not lpn_barcode:
        logger.warning(f"lpn_barcode 없음 (return_job_id={source_job_id}) - 랙 위치 자동 할당을 건너뜁니다.")
        return final_status, {}

    from app.db.session import engine
    from app.domains.inventory.service import assign_rack_location_after_inspection

    if decision == "HITL":
        item_status = "HITL_REQUIRED"
    elif decision == "REJECT":
        item_status = "REJECTED"
    else:
        item_status = "IN_STOCK"

    try:
        with Session(engine) as session:
            # HITL로 넘어가는 건은 아직 사람이 확정하지 않았으므로 검수 주체를 PENDING으로 둔다.
            # 실제 확정자는 관리자가 /admin/hitl/override를 처리하는 시점에 기록된다.
            if decision == "HITL":
                inspection_source, inspected_by = "PENDING_HITL", None
            else:
                inspection_source = "AI_AUTO"
                inspected_by = "Nexus Vision AI (LangGraph 4-Agent)"

            assign_rack_location_after_inspection(
                db=session,
                lpn_barcode=lpn_barcode,
                final_grade=final_grade,
                final_status=item_status,
                book_id=book_id,
                ubci_score=ubci_score if ubci_score is not None else 85,
                source_job_id=source_job_id,
                inspection_source=inspection_source,
                inspected_by=inspected_by,
            )

            # [구조 변경 - 2026-08-04] MINT 자동 매입/환불 승인 집행.
            # 예전에는 auto_refund_agent 노드가 그래프 안에서 이 결정을 내렸는데, 그 경로가
            # Policy/Critic 검증을 건너뛰어 판독 실패 시 전 건이 자동 승인되는 사고를 냈다.
            # 이제 등급 확정은 전 건이 동일한 검증 경로를 통과한 뒤에만 이뤄지고, 그 결과에
            # 붙은 auto_refund_eligible 플래그를 워커가 집행한다 - 판정(Agent)과
            # 집행(WMS Action)의 책임을 분리한 것.
            if auto_refund_eligible and decision == "APPROVE":
                logger.info(
                    f"[Auto-Refund] MINT 무결점 확정 - 관리자 개입 없이 자동 매입 승인 (lpn={lpn_barcode})"
                )
            session.commit()
    except Exception as e:
        logger.warning(f"랙 위치 자동 할당 실패 (lpn={lpn_barcode}): {e}")

    return final_status, {}



# ==========================================
# Restock 판정 그래프 워커 (자동 발주 제안 생성)
# ==========================================

def _extract_reject_reason_code(agent_logs: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    반려 사유 코드 추출 우선순위:
    ① HITL 관리자가 오버라이드 시 지정한 primary_reason_code (사람 판단이 최우선)
    ② AI 파이프라인이 판독한 defects의 결함 유형(중복 제거, 최대 3종)
    """
    logs = agent_logs or {}
    primary = logs.get("primary_reason_code")
    if primary:
        return str(primary)[:50]
    defects = logs.get("defects") or []
    types = sorted({d.get("type", "") for d in defects if isinstance(d, dict) and d.get("type")})
    return ",".join(types[:3])[:50] if types else None


@celery_app.task(name="app.worker.tasks.generate_restock_proposal", max_retries=1)
def generate_restock_proposal(return_job_id: str) -> Dict[str, Any]:
    """
    반려 확정 건에 대해 Restock 판정 그래프(Collector→Agent→Validator)를 실행하고
    order_proposals에 PENDING 제안 카드를 적재한다. ReturnJob 1건 = 도서 1권이므로
    반려 수량은 1로 집계한다 (동일 도서 반복 반려 시 기존 PENDING 카드에 누적).
    """
    from app.db.session import engine
    from app.models.wms import Book
    from app.ai.agents.restock import generate_and_store_proposal

    try:
        parsed_job_id = uuid.UUID(return_job_id)
        with Session(engine) as session:
            job = session.get(ReturnJob, parsed_job_id)
            if not job:
                return {"status": "SKIPPED", "reason": "RETURN_JOB_NOT_FOUND"}
            book = session.get(Book, job.book_id)
            if not book:
                return {"status": "SKIPPED", "reason": "BOOK_NOT_FOUND"}

            proposal = generate_and_store_proposal(
                session,
                book,
                trigger_type="INSPECTION_REJECT",
                source_job_id=job.id,
                rejected_quantity=1,
                reject_reason_code=_extract_reject_reason_code(job.agent_logs),
            )
            if not proposal:
                return {"status": "SKIPPED", "reason": "NO_RESTOCK_NEEDED"}
            logger.info(
                f"[Restock] 자동 발주 제안 적재 완료 - proposal={proposal.id} book='{book.title}' "
                f"qty={proposal.proposed_quantity} urgency={proposal.urgency} source={proposal.ai_source}"
            )
            return {"status": "SUCCESS", "proposal_id": str(proposal.id)}
    except Exception as e:
        logger.exception(f"[Restock] 제안 생성 실패 (return_job_id={return_job_id}): {e}")
        return {"status": "FAILED", "error": str(e)}


def enqueue_restock_proposal(return_job_id: str) -> None:
    """
    Restock 제안 생성을 비동기로 큐잉한다. Celery 브로커 장애 시 인프로세스 스레드로
    폴백한다 (admin/hitl re-inspect의 재큐잉 폴백과 동일 패턴). 어떤 경우에도 예외를
    호출자(반려 처리 흐름)로 전파하지 않는다.
    """
    try:
        generate_restock_proposal.delay(str(return_job_id))
    except Exception as e:
        logger.warning(f"[Restock] Celery 큐잉 실패, 인프로세스로 폴백: {e}")
        try:
            import threading
            threading.Thread(target=generate_restock_proposal, args=(str(return_job_id),), daemon=True).start()
        except Exception as e2:
            logger.error(f"[Restock] 인프로세스 폴백마저 실패 - 제안 생성 건너뜀: {e2}")


@celery_app.task(
    name="app.worker.tasks.scan_safety_stock_proposals",
    max_retries=1,
)
def scan_safety_stock_proposals() -> Dict[str, Any]:
    """
    저재고 스캔 배치 본체. 가용 재고가 안전선 미만인 도서를 순회하며
    Restock 판정 그래프로 제안 카드를 적재한다 (수동 트리거 API와 동일 로직).

    k8s CronJob(app/batch/restock_scan.py)이 이 태스크를 큐잉하고 즉시 종료하므로,
    LLM 호출 비용이 큰 실제 스캔은 상시 워커 풀에서 실행된다.
    """
    from app.db.session import engine
    from app.domains.po.service import po_service

    try:
        with Session(engine) as session:
            result = po_service.scan_safety_stock(session)
        logger.info(
            f"[Restock] 저재고 스캔 배치 완료 - 생성 {result.get('createdCount', 0)}건"
        )
        return result
    except Exception as e:
        logger.exception(f"[Restock] 저재고 스캔 배치 실패: {e}")
        return {"status": "FAILED", "error": str(e)}


# celery task
@celery_app.task(
        bind=True,
        name="app.worker.tasks.process_inspection",
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
            image_urls,
            agent_logs_in,
        ) = prepare_processing_job(
            return_job_id=return_job_id,
            celery_task_id=celery_task_id,
        )
        lpn_barcode = (agent_logs_in or {}).get("lpn_barcode")

        # [수정 이력] image_urls는 이제 브라우저용 CloudFront 공개 URL이다. WBF YOLO 추론은
        # 로컬 파일 경로를 요구하므로, 입고 시 함께 남겨둔 local_image_paths를 우선 사용하고
        # (원격 재다운로드 왕복 제거) 로컬 사본이 없는 과거 건에서만 URL로 폴백한다.
        local_paths = [
            p for p in ((agent_logs_in or {}).get("local_image_paths") or []) if p and os.path.exists(p)
        ]
        inference_images = local_paths or image_urls

        # [수정 이력] Policy Agent의 is_workbook 판정(수험서/문제집 낙서 -15점 단일 Cap)은
        # state["book_title"]을 읽는데, 이 키를 아무도 채워준 적이 없어 항상 빈 문자열이었다.
        # 즉 수험서 Cap이 한 번도 발동하지 않고 낙서가 건당 누적 감점되고 있었다.
        book_title = ((agent_logs_in or {}).get("book_metadata") or {}).get("title") or ""

        publish_processing_event(
            return_job_id=parsed_return_job_id,
            celery_task_id=celery_task_id,
        )

        # 3. LangGraph Multi-Agent 실행
        langgraph_wrapper = LangGraphInspectionWrapper()
        ai_result = langgraph_wrapper.run_inspection(
            return_job_id = return_job_id,
            order_id = order_id,
            image_urls = inference_images,
            display_image_urls = image_urls,
            book_title = book_title,
        )

        # 4. AI decision에 따라 창고 랙 위치 확정
        decision = ai_result.get("decision")
        if decision not in ["APPROVE", "REJECT", "HITL"]:
            raise ValueError(f"Unknown AI decision: {decision}")

        final_status, extra_logs = execute_wms_action(
            decision=decision,
            book_id=book_id,
            lpn_barcode=lpn_barcode,
            final_grade=ai_result.get("final_grade", "NORMAL"),
            ubci_score=ai_result.get("ubci_score"),
            source_job_id=return_job_id,
            inbound_worker_id=(agent_logs_in or {}).get("inbound_worker_id"),
            auto_refund_eligible=bool(ai_result.get("auto_refund_eligible")),
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

        # 관제 콘솔 전역 알림 발행.
        # [수정 이력] 종전에는 실제 파이프라인 사건이 알림을 하나도 만들지 않아, 프론트가
        # 더미 4건을 하드코딩해 들고 있었다. 알림 발행 실패가 검수 결과를 무효화해서는
        # 안 되므로 예외는 여기서 삼킨다.
        try:
            from app.domains.notifications.service import notify_hitl_required, notify_inspection_done

            job_logs = job.agent_logs or {}
            book_title = (job_logs.get("book_metadata") or {}).get("title") or "도서"

            if job.status == "HITL_REQUIRED":
                notify_hitl_required(
                    job_id=str(job.id),
                    book_title=book_title,
                    ubci_score=job.ubci_score,
                    reason=job_logs.get("supervisor_rationale") or job_logs.get("critic_text") or "",
                )
            elif job.status == "APPROVED":
                notify_inspection_done(
                    lpn=job_logs.get("lpn_barcode") or "-",
                    book_title=book_title,
                    grade=ai_result.get("final_grade", "-"),
                    ubci_score=job.ubci_score,
                )
        except Exception as notify_err:
            logger.warning(f"[Notification] 검수 완료 알림 발행 실패(검수 결과에는 영향 없음): {notify_err}")

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

    except (httpx.HTTPError, openai.APIError) as error:
        # 네트워크/외부 API 에러(429 Rate Limit, 타임아웃, 5xx 등) 발생 시 Exponential Backoff (지수 백오프) 처리
        # openai.APIError는 RateLimitError/APITimeoutError/APIConnectionError 등의 부모 클래스라
        # WBF/VLM 호출 중 발생하는 일시적 OpenAI 오류도 DLQ 직행 대신 재시도부터 거치도록 함께 포괄한다.
        retries = self.request.retries
        if retries < self.max_retries:
            # 2초, 4초, 8초... 순으로 기하급수적 대기 시간 적용
            backoff_delay = 2 ** retries
            logger.warning(
                f"[Rate Limit / API Error] Retrying task {celery_task_id} in {backoff_delay}s... "
                f"({retries + 1}/{self.max_retries}) | Err: {str(error)}"
            )
            # 예외를 던지며 retry 큐로 재진입 (이때 finally 블록이 실행되며 락 해제됨)
            raise self.retry(exc=error, countdown=backoff_delay)

        # 백오프를 모두 소진했음에도 실패한 경우 (Max Retries Exhausted) -> DLQ 격리
        logger.exception(f"API retries exhausted for {return_job_id}. Sending to DLQ.")
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




    

    


