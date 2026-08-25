"""
WMS 전역 알림 발행 서비스.

[설계 원칙 - 저장과 실시간 전달을 분리]
  1) DB(notifications 테이블)에 영속화  -> 새로고침해도 남는 알림 이력
  2) Redis Pub/Sub(notifications:global)에 발행 -> 접속 중인 화면에 즉시 푸시

종전에는 2)만 있었고 발행하는 곳도 데모용 /trigger-fds 하나뿐이라, 실제 파이프라인
사건(HITL 이관, 검수 실패, 발주 제안 생성)은 어떤 알림도 만들지 않았다. 그래서 프론트가
하드코딩 더미 4건을 들고 있어야 했다.

[중요] 알림 발행 실패가 업무 트랜잭션을 깨뜨려서는 안 된다. 모든 함수는 예외를 삼키고
경고 로그만 남긴다 (알림은 부가 기능이지 업무의 전제 조건이 아니다).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from app.models.wms import Notification, now_kst

logger = logging.getLogger(__name__)

NOTIFICATIONS_CHANNEL = "notifications:global"

# 이벤트 종류별 기본 표시 속성 (프론트가 뱃지 문구/색을 지어내지 않도록 백엔드가 확정한다)
_TYPE_PRESETS: Dict[str, Dict[str, str]] = {
    "AGENT_ERROR": {
        "category": "에이전트 이상감지",
        "severity": "CRITICAL",
        "link_url": "/admin/hitl",
    },
    "HITL_REQUIRED": {
        "category": "정책상 관리자 검토",
        "severity": "WARN",
        "link_url": "/admin/hitl",
    },
    "RESTOCK_PROPOSAL": {
        "category": "자동발주 알림",
        "severity": "INFO",
        "link_url": "/admin/po",
    },
    "FDS_ALERT": {
        "category": "FDS 이상거래",
        "severity": "CRITICAL",
        "link_url": "/admin/fds",
    },
    "INSPECTION_DONE": {
        "category": "검수 완료",
        "severity": "INFO",
        "link_url": "/inspections",
    },
    # [2026-08-08 신설] 출고 파이프라인 3사건. 이전에는 orders/picking.py의
    # publish_outbound_notification()이 이 서비스를 거치지 않고 Redis에만 직접 발행해
    # DB에 남지 않았다 (아래 emit_outbound_event 수정 이력 참고).
    "PICKING_INSTRUCTION_ISSUED": {
        "category": "출고 피킹 지시",
        "severity": "INFO",
        "link_url": "/worker/outbound",
    },
    "PICKING_COMPLETED": {
        "category": "피킹 완료",
        "severity": "INFO",
        "link_url": "/admin/outbound",
    },
    "WAYBILL_ISSUED": {
        "category": "송장 발급",
        "severity": "INFO",
        "link_url": "/worker/outbound",
    },
    "OUTBOUND_SHIPPED": {
        "category": "출고 완료",
        "severity": "INFO",
        "link_url": "/admin/outbound",
    },
    # [2026-08-12 신설] 주간 인사이트 배치(Celery Beat 00:05 KST)가 AI 서사와 함께 올린다.
    "WEEKLY_INSIGHT": {
        "category": "주간 인사이트",
        "severity": "INFO",
        "link_url": "/admin/dashboard",
    },
}


def _publish_to_channel(payload: Dict[str, Any]) -> None:
    """Redis Pub/Sub 채널에 발행한다. 실패해도 조용히 넘어간다."""
    try:
        import redis as sync_redis
        from app.core.redis_pubsub import REDIS_URL

        client = sync_redis.Redis.from_url(REDIS_URL, decode_responses=True)
        try:
            client.publish(
                NOTIFICATIONS_CHANNEL, json.dumps(payload, ensure_ascii=False)
            )
        finally:
            client.close()
    except Exception as e:
        logger.warning(f"[Notification] 실시간 채널 발행 실패 (DB에는 저장됨): {e}")


def to_payload(n: Notification) -> Dict[str, Any]:
    """DB row를 프론트/SSE가 그대로 쓰는 형태로 직렬화한다."""
    return {
        "id": str(n.id),
        "type": n.type,
        "severity": n.severity,
        "category": n.category,
        "title": n.title,
        "description": n.description or "",
        "link_url": n.link_url,
        "ref_type": n.ref_type,
        "ref_id": n.ref_id,
        "target_role": n.target_role,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def emit(
    type: str,
    title: str,
    description: str = "",
    *,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    link_url: Optional[str] = None,
    ref_type: Optional[str] = None,
    ref_id: Optional[str] = None,
    target_role: Optional[str] = None,
    session=None,
) -> Optional[Dict[str, Any]]:
    """
    알림을 DB에 저장하고 실시간 채널에 발행한다.

    session을 넘기면 해당 트랜잭션에 참여하고(호출자가 commit 책임), 넘기지 않으면
    독립 세션을 열어 즉시 커밋한다. Celery 워커처럼 요청 세션이 없는 곳에서도 쓸 수 있다.
    """
    preset = _TYPE_PRESETS.get(type, {})

    notification = Notification(
        type=type,
        severity=severity or preset.get("severity", "INFO"),
        category=category or preset.get("category", "시스템 알림"),
        title=title,
        description=description,
        link_url=link_url or preset.get("link_url"),
        ref_type=ref_type,
        ref_id=str(ref_id) if ref_id else None,
        target_role=target_role,
        created_at=now_kst(),
    )

    try:
        if session is not None:
            session.add(notification)
            session.flush()  # id 확보 (commit은 호출자 트랜잭션에 맡긴다)
        else:
            from sqlmodel import Session
            from app.db.session import engine

            with Session(engine) as own_session:
                own_session.add(notification)
                own_session.commit()
                own_session.refresh(notification)
    except Exception as e:
        # DB 저장 실패 시에도 실시간 푸시는 시도한다 (화면에라도 뜨는 편이 낫다).
        logger.warning(f"[Notification] DB 저장 실패: {e}")

    payload = to_payload(notification)
    _publish_to_channel(payload)
    return payload


# ==========================================
# 도메인 사건별 발행 헬퍼
# ==========================================
# 호출부가 문구를 각자 지어내면 같은 사건이 화면마다 다르게 표시되므로,
# 사건 유형별 문구를 여기서 한 번만 정의한다.


def notify_hitl_required(
    job_id: str, book_title: str, ubci_score: Optional[int], reason: str = ""
) -> None:
    emit(
        type="HITL_REQUIRED",
        title="관리자 수동 검수 필요",
        description=(
            f"'{book_title}' 건이 자동 확정되지 않아 관리자 결재로 이관되었습니다."
            + (f" (UBCI {ubci_score}점)" if ubci_score is not None else "")
            + (f" 사유: {reason}" if reason else "")
        ),
        ref_type="RETURN_JOB",
        ref_id=job_id,
        target_role="ADMIN",
    )


def notify_agent_error(job_id: str, error_message: str) -> None:
    emit(
        type="AGENT_ERROR",
        title="검수 파이프라인 오류",
        description=f"AI 검수 처리 중 오류가 발생해 DLQ로 격리되었습니다. ({error_message[:160]})",
        ref_type="RETURN_JOB",
        ref_id=job_id,
        target_role="ADMIN",
    )


def notify_restock_proposal(
    book_title: str, qty: int, proposal_id: Optional[str] = None
) -> None:
    emit(
        type="RESTOCK_PROPOSAL",
        title="대체 발주 추천 생성",
        description=f"'{book_title}' 반려 건에 대한 대체 발주 추천안이 생성되었습니다. (추천 수량: {qty}권)",
        ref_type="ORDER_PROPOSAL",
        ref_id=proposal_id,
        target_role="ADMIN",
    )


def notify_outbound_event(
    event_type: str,
    title: str,
    description: str,
    *,
    instruction_id: Optional[str] = None,
    target_role: Optional[str] = None,
) -> None:
    """
    출고 파이프라인(피킹 완료/송장 발급/최종 출고) 사건 발행.

    orders/picking.py의 publish_outbound_notification()이
    이 서비스(emit)를 거치지 않고 Redis notifications:global 채널에 직접 publish만
    했다. 그 결과 notifications 테이블에 저장되지 않아, 화면 이동으로 SSE 연결이
    끊기는 순간(출고 흐름은 관제 화면 -> 스캐너 화면 등 여러 페이지를 오가므로 거의
    매번 발생) 발행된 이벤트가 영구 소실됐다. GET /api/v1/notifications는 DB만
    읽으므로, 출고를 처음부터 끝까지 진행해도 알림 패널이 비어 있던 원인이 이것이다
    (입고/HITL 알림은 emit()을 거쳐 DB에 남으므로 정상 동작했다). 페이로드 형태도
    달라(created_at 대신 timestamp, link_url·severity 누락) 실시간 수신에 성공한
    경우조차 클릭 이동과 상대시각 표시가 깨졌다. 이제 다른 도메인과 동일하게 emit()에
    위임해 DB 저장과 올바른 페이로드를 함께 보장한다.
    """
    emit(
        type=event_type,
        title=title,
        description=description,
        ref_type="PICKING_INSTRUCTION",
        ref_id=instruction_id,
        target_role=target_role,
    )


def notify_inspection_done(
    lpn: str, book_title: str, grade: str, ubci_score: Optional[int]
) -> None:
    emit(
        type="INSPECTION_DONE",
        title=f"AI 검수 완료 ({grade})",
        description=f"'{book_title}' 검수가 완료되어 재고에 편입되었습니다. (LPN {lpn} / UBCI {ubci_score}점)",
        ref_type="INVENTORY_ITEM",
        ref_id=lpn,
    )
