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


@celery_app.task(name="app.worker.tasks.generate_hitl_certificate", max_retries=1)
def generate_hitl_certificate(return_job_id: str, hitl_inspector: str) -> Dict[str, Any]:
    """
    HITL 승인 건의 고객 보증서 본문을 비동기로 생성한다.

    [배경 - 2026-08-08] 종전에는 admin 라우터(app/domains/admin/router.py
    submit_hitl_override)가 승인 항목마다 build_certificate_document()(GPT-4o-mini)를
    요청 스레드에서 동기 호출했다. 합성 시드 데이터 다건을 한 번에 일괄 승인하는
    요청에서 순차 LLM 호출이 쌓여 wms-secret-api 컨테이너가 OOM(exit 137)으로 죽었다.
    반려 확정 건의 Restock 제안 큐잉(enqueue_restock_proposal)과 동일한 패턴으로,
    등급 확정·랙 배정·재고 편입까지는 라우터가 동기로 끝내고 보증서 생성만 워커로
    넘긴다 (판정/집행 분리 - 등급 확정과 보증서 문서화는 별개 관심사).
    """
    from app.db.session import engine
    from app.ai.agents import build_certificate_document

    try:
        parsed_job_id = uuid.UUID(return_job_id)
        with Session(engine) as session:
            job = session.get(ReturnJob, parsed_job_id)
            if not job:
                return {"status": "SKIPPED", "reason": "RETURN_JOB_NOT_FOUND"}

            agent_logs = dict(job.agent_logs or {})
            target_grade = agent_logs.get("target_grade") or "NORMAL"

            notes = [f"관리자 수동 검수 확정 (검수자: {hitl_inspector})"]
            reason = agent_logs.get("primary_reason_code")
            if reason:
                notes.append(f"판정 사유: {reason}")
            if target_grade:
                notes.append(f"확정 등급: {target_grade}")
            comment = (agent_logs.get("admin_comment") or "").strip()
            if comment:
                notes.append(f"검수자 의견: {comment}")
            prior = (agent_logs.get("special_notes") or "").strip()
            if prior:
                notes.append(f"AI 판독 특이사항: {prior}")

            cert_state = {
                "ubci_score": job.ubci_score,
                "defects": agent_logs.get("defects") or [],
                "book_title": agent_logs.get("book_title") or "",
                "special_notes": " / ".join(notes),
            }
            cert_doc = build_certificate_document(cert_state)
            cert_doc["cert_id"] = f"CERT-{datetime.now().strftime('%Y%m%d')}-{str(job.id)[:6].upper()}"
            cert_doc["issued_by"] = "HITL"
            cert_doc["inspected_by"] = hitl_inspector

            executed_agents = list(agent_logs.get("executed_agents") or [])
            if "report_agent" not in executed_agents:
                executed_agents.append("report_agent")
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
            session.add(job)
            session.commit()
            logger.info(f"[HITL] 보증서 비동기 생성 완료: {cert_doc['cert_id']} (job {job.id})")
            return {"status": "SUCCESS", "cert_id": cert_doc["cert_id"]}
    except Exception as e:
        logger.exception(f"[HITL] 보증서 비동기 생성 실패 (return_job_id={return_job_id}): {e}")
        return {"status": "FAILED", "error": str(e)}


def enqueue_hitl_certificate(return_job_id: str, hitl_inspector: str) -> None:
    """
    HITL 승인 보증서 생성을 비동기로 큐잉한다. Celery 브로커 장애 시 인프로세스 스레드로
    폴백한다 (enqueue_restock_proposal과 동일 패턴). 결재 자체를 막지 않도록 예외를
    호출자(오버라이드 제출 흐름)로 전파하지 않는다.
    """
    try:
        generate_hitl_certificate.delay(str(return_job_id), hitl_inspector)
    except Exception as e:
        logger.warning(f"[HITL] Celery 큐잉 실패, 인프로세스로 폴백: {e}")
        try:
            import threading
            threading.Thread(
                target=generate_hitl_certificate,
                args=(str(return_job_id), hitl_inspector),
                daemon=True,
            ).start()
        except Exception as e2:
            logger.error(f"[HITL] 인프로세스 폴백마저 실패 - 보증서 생성 건너뜀: {e2}")


from celery.signals import worker_ready


def _requeue_stale_pending_inspections() -> int:
    """
    원장(return_jobs) 기반 미아 작업 복구 스위퍼의 실제 구현.

    2026-08-05 카오스 테스트에서 발견: Redis 전송 계층은 메시지 수신과 unacked 등록
    사이가 비원자적이라, 하드킬 타이밍에 따라 브로커 메시지가 소실될 수 있다.
    브로커는 잃어도 DB 원장에는 작업이 남으므로, 2분 이상 방치된 PENDING 작업을
    재큐잉해 유실을 원천 봉쇄한다.
    (중복 전달은 Redlock과 태스크 내 터미널 상태 검사가 차단한다.)

    반환값은 재큐잉한 건수다.
    """
    from datetime import timedelta
    from app.db.session import engine
    from sqlmodel import Session, select

    try:
        pending_cutoff = now_kst() - timedelta(minutes=2)
        # PROCESSING은 정상 실행일 수 있으므로 task_time_limit(360초)을 확실히 넘긴
        # 것만 미아로 본다. 처리 중 하드킬 + 브로커 재전달까지 소실된 조합(2026-08-12
        # 카오스 v3에서 확인)은 PENDING 스윕만으로는 영원히 복구되지 않는다.
        processing_cutoff = now_kst() - timedelta(minutes=8)
        with Session(engine) as session:
            stale = session.exec(
                select(ReturnJob).where(
                    (
                        (ReturnJob.status == "PENDING")
                        & (ReturnJob.created_at < pending_cutoff)
                    )
                    | (
                        (ReturnJob.status == "PROCESSING")
                        & (ReturnJob.updated_at < processing_cutoff)
                    )
                )
            ).all()
        for job in stale:
            process_inspection.delay(str(job.id))
            logger.warning(
                f"[Sweeper] 미아 {job.status} 작업 재큐잉: return_job_id={job.id}"
            )
        if stale:
            logger.warning(f"[Sweeper] 총 {len(stale)}건 재큐잉 완료")
        return len(stale)
    except Exception as e:
        logger.error(f"[Sweeper] 미아 작업 스캔 실패: {e}")
        return 0


@worker_ready.connect
def requeue_stale_pending_inspections(**_kwargs) -> None:
    """워커 기동 시 1회 즉시 스윕 (주기 실행과 별개로 초기 복구를 앞당긴다)."""
    _requeue_stale_pending_inspections()


@celery_app.task(name="app.worker.tasks.sweep_stale_pending_inspections")
def sweep_stale_pending_inspections() -> int:
    """
    Celery Beat가 60초마다 호출하는 주기 스위퍼 (celery_app.beat_schedule 등록).

    기동 시그널만으로는 부족하다 — 스위퍼 조건이 "2분 이상 방치"인데 워커가 막 재기동한
    시점에는 방금 유실된 작업이 아직 2분 미만이라 걸리지 않는다. 두 조건이 서로를
    무력화해 미아 작업이 다음 재기동 때까지 남는 문제를 2026-08-12 카오스 재실행에서
    실측(25분 방치)했고, 그 대응으로 주기 실행을 추가했다.
    """
    return _requeue_stale_pending_inspections()


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
def process_inspection(self, return_job_id: str, was_hitl: bool = False) -> Dict[str, Any]:
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

        # 터미널 상태 가드 — Redlock은 '동시' 중복만 막는다. visibility_timeout 재전달이나
        # 스위퍼 재큐잉으로 이미 완결된 건이 '순차적으로' 다시 도착하면 락은 비어 있으므로
        # 그대로 재검수(이중 LLM 비용 + 랙 재배정)가 돌게 된다. 여기서 차단한다.
        # (HITL_REQUIRED는 제외 — 관리자 결재 후 재검수 경로가 정상적으로 재진입한다.)
        from app.db.session import engine
        with Session(engine) as _s:
            _job = _s.get(ReturnJob, parsed_return_job_id)
            if _job is not None and _job.status in ("APPROVED", "REJECTED") and not was_hitl:
                logger.warning(
                    f"Task {celery_task_id} skipped. Job {return_job_id} already terminal "
                    f"({_job.status}) - duplicate delivery or sweeper requeue."
                )
                return {"status": "SKIPPED", "reason": f"ALREADY_{_job.status}"}

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
        # [수정 이력 2026-08-12] is_workbook이 제목 키워드에만 의존해 "쉽게 풀어쓴 C언어
        # Express"처럼 실습문제가 실린 도서 다수가 안 걸렸다. inbound/router.py가 이미
        # agent_logs["book_category"]에 심어둔 값을 읽어 2차 신호로 함께 넘긴다.
        book_category = (agent_logs_in or {}).get("book_category") or ""

        # [수정 이력 2026-08-13] book_metadata/book_category는 **입고 시점**에만 agent_logs에
        # 심긴다. 그 이전에 만들어진 job을 재검수하면 둘 다 비어서 ① is_workbook Cap이
        # 미발동해 낙서가 건당 누적되고(실측: C언어 Express 재검수 -100점 -> UBCI 0점 REJECT),
        # ② 완료 알림 도서명이 '도서'로 표기됐다. books 테이블에는 항상 있으므로 DB에서
        # 폴백한다 - book_id는 prepare_processing_job이 이미 돌려준 값이다.
        if not book_title or not book_category:
            try:
                from app.db.session import engine as _engine
                from app.models.wms import Book as _Book
                with Session(_engine) as _s:
                    _book = _s.get(_Book, book_id) if book_id else None
                    if _book:
                        book_title = book_title or (_book.title or "")
                        book_category = book_category or (_book.category_type or "")
            except Exception as _meta_err:
                logger.warning(f"[BookContext] 도서 메타 DB 폴백 실패(검수는 계속): {_meta_err}")

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
            book_category = book_category,
        )

        # 4. AI decision에 따라 창고 랙 위치 확정
        decision = ai_result.get("decision")
        if decision not in ["APPROVE", "REJECT", "HITL"]:
            raise ValueError(f"Unknown AI decision: {decision}")

        # --- HITL 결재 잠금 --- 이미 사람이 봐야 한다고 이관된 건은 재검수 결과와 무관하게
        # 자동 확정하지 않는다. 판정(점수·결함)은 갱신하되 집행만 막아 관리자 결재 경로로
        # 보낸다 (판정/집행 분리 원칙). 배경: 01-freeze-zones.md.
        if was_hitl and decision == "APPROVE":
            logger.info(
                f"[HITL 잠금] 재검수 결과 APPROVE이나 관리자 결재 대기 건이므로 자동 확정을 보류합니다. "
                f"(return_job_id={return_job_id}, ubci_score={ai_result.get('ubci_score')})"
            )
            decision = "HITL"
            ai_result["decision"] = "HITL"
            ai_result["auto_refund_eligible"] = False
            ai_result["reason_code"] = "HITL_LOCKED_REINSPECTION"
            prev_rationale = str(ai_result.get("supervisor_rationale") or "").strip()
            ai_result["supervisor_rationale"] = (
                (prev_rationale + " / " if prev_rationale else "")
                + "재검수 결과는 자동 승인 가능 수준이나, 이미 관리자 결재로 이관된 건이므로 "
                  "자동 확정을 보류하고 결재 대기를 유지합니다 (HITL 잠금)."
            )

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
            # 도서명은 위에서 DB 폴백까지 끝낸 book_title을 재사용한다.
            # [수정 이력 2026-08-13] 여기서 agent_logs.book_metadata를 다시 읽었는데, 입고
            # 시점 이전의 구 job(재검수)은 그 키가 없어 알림이 전부 '도서'로 표기됐다.
            notify_book_title = book_title or "도서"

            if job.status == "HITL_REQUIRED":
                notify_hitl_required(
                    job_id=str(job.id),
                    book_title=notify_book_title,
                    ubci_score=job.ubci_score,
                    reason=job_logs.get("supervisor_rationale") or job_logs.get("critic_text") or "",
                )
            elif job.status == "APPROVED":
                notify_inspection_done(
                    lpn=job_logs.get("lpn_barcode") or "-",
                    book_title=notify_book_title,
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




    

    




@celery_app.task(name="app.worker.tasks.generate_weekly_insight")
def generate_weekly_insight() -> Dict[str, Any]:
    """
    주간 인사이트 정규 생성 배치 (Celery Beat 매일 00:05 KST).

    [2026-08-12 신설] 종전에는 대시보드 첫 방문 시 즉석 생성하는 지연 물질화였다. 집계 창이
    "방문 시점 기준 과거 7일"이라 ISO 주차 라벨과 어긋났고, 생성 시각이 방문자에 좌우돼
    같은 주차가 언제 조회됐느냐에 따라 다른 값으로 굳었다(실측: 2026-W33이 월요일 00:01
    방문으로 거의 빈 데이터에 고정). 집계 창을 ISO 주 경계로 못박고 여기서 매일 갱신한다.

    매일 도는 이유: 진행 중인 주의 러닝 스냅샷을 최신으로 유지하기 위해서다. 월요일 실행분은
    직전 주가 막 닫힌 시점이므로 그 주를 확정(force 재집계)하고, 새 주를 새로 연다.
    """
    from datetime import timedelta

    from app.db.session import engine
    from app.domains.dashboard.weekly_insight_service import (
        build_weekly_insight, iso_week_bounds,
    )
    from app.models.wms import now_kst

    now = now_kst()
    result: Dict[str, Any] = {"finalized": None, "refreshed": None}

    try:
        with Session(engine) as session:
            # 1) 직전 주 확정. 매일 force 재집계하지 않고 행이 없을 때만 만들면, 크론이
            #    하루 걸러도 빈 주차가 생기지 않는다(백필 성격).
            prev_ref = now - timedelta(days=7)
            prev_week, _, _ = iso_week_bounds(prev_ref)
            prev_insight, prev_created = build_weekly_insight(session, prev_ref)
            result["finalized"] = {"week": prev_week, "created": prev_created}

            # 2) 진행 중인 주는 매일 재집계해 러닝 값을 갱신한다.
            cur_insight, _ = build_weekly_insight(session, now, force=True)
            result["refreshed"] = {
                "week": cur_insight.report_week,
                "saved_labor_cost_krw": cur_insight.saved_labor_cost_krw,
            }

            # 3) AI가 만든 서사를 알림으로 결합해 관제 콘솔에 띄운다.
            #    수치는 결정론적 SQL 집계이고 문장만 LLM 생성이라는 원칙은 그대로다.
            try:
                from app.domains.notifications.service import emit

                emit(
                    type="WEEKLY_INSIGHT",
                    title=f"{cur_insight.report_week} 주간 인사이트 갱신",
                    description=(cur_insight.ai_narrative or "")[:500],
                    ref_type="WEEKLY_INSIGHT",
                    ref_id=cur_insight.report_week,
                    target_role="ADMIN",
                )
            except Exception as notify_err:
                logger.warning(f"[WeeklyInsight] 알림 발행 실패(집계는 완료됨): {notify_err}")

    except Exception as e:
        logger.error(f"[WeeklyInsight] 주간 인사이트 생성 실패: {e}")
        result["error"] = str(e)

    return result
