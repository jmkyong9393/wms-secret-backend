"""
FDS(Fraud Detection System) 서비스 - 룰 엔진 + Analyst Agent 2단 구조.

[아키텍처 원칙 2026-08-04]
- 탐지/위험점수 산출은 100% 결정론적 룰 엔진이 담당한다 (재현성·감사 추적성 보장,
  Policy Agent·Auto-PO와 동일 원칙 - 비즈니스 숫자 판단에 LLM 미개입).
- FDS Analyst Agent(gpt-4o-mini)는 룰 엔진이 확정한 "사실(evidence)"을 입력받아
  정황 해석(fraud_reason)과 권고 조치(recommended_action) **서술문만** 생성한다.
  LLM 장애 시 결정론적 템플릿으로 폴백한다 (fail-open).
- LangGraph 4-Agent 검수 파이프라인(프리즈 구역)과는 완전히 분리된 별도 모듈이다.

탐지 룰 4종:
  R1_BLIND_APPROVAL : 관리자 평균 HITL 결재시간 1초 미만 (블라인드 결재 / 모럴 해저드)
  R2_GRADE_OVERRIDE : AI 제안등급 대비 2단계 이상 상향 승인 반복 (등급 오버라이드 남용)
  R3_NIGHT_BULK     : 야간(22~06시) 대량 주문 (비정상 발주 패턴)
  R4_RETURN_ABUSE   : 동일 고객 반복 반품 요청 (반품 어뷰징)
"""
import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlmodel import Session, select, func

from app.models.wms import (
    AdminAuditLog, FdsReport, Order, OrderStatusEnum, User, now_kst,
)

logger = logging.getLogger(__name__)

# 등급 서열 (R2 상향 폭 계산용)
_GRADE_RANK = {"REJECT": 0, "NORMAL": 1, "GOOD": 2, "MINT": 3}

# 룰별 임계값 (결정론적 상수 - 변경 시 이 파일만 수정)
BLIND_APPROVAL_THRESHOLD_MS = 1000     # R1: 평균 결재시간 1초 미만
GRADE_OVERRIDE_MIN_STEPS = 2           # R2: 2단계 이상 상향
GRADE_OVERRIDE_MIN_COUNT = 2           # R2: 반복 기준 횟수
NIGHT_BULK_MIN_PRICE = 500_000         # R3: 야간 주문 임계 금액 (원)
RETURN_ABUSE_MIN_COUNT = 3             # R4: 반복 반품 기준 횟수
DEDUP_WINDOW_HOURS = 24                # 동일 룰+대상 재적발 억제 창


class _AnalystVerdict(BaseModel):
    fraud_reason: str = Field(description="적발 정황 한 문장 해석 (한국어, 255자 이내)")
    recommended_action: str = Field(description="관리자가 취해야 할 권고 조치 1~2문장 (한국어)")


try:
    from langchain_openai import ChatOpenAI
    _analyst_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
except Exception:
    _analyst_llm = None


class FdsService:
    """FDS 룰 엔진 + Analyst Agent. 2-Layer 원칙에 따라 라우터는 이 서비스만 호출한다."""

    # ---------- 룰 엔진 (결정론) ----------

    def _rule_blind_approval(self, session: Session) -> List[Dict[str, Any]]:
        """R1: 관리자별 평균 HITL 결재시간이 임계 미만이면 블라인드 결재 의심."""
        rows = session.exec(
            select(
                AdminAuditLog.admin_id,
                func.count(AdminAuditLog.id),
                func.avg(AdminAuditLog.review_duration_ms),
                func.min(AdminAuditLog.review_duration_ms),
            )
            .where(AdminAuditLog.review_duration_ms.is_not(None))
            .group_by(AdminAuditLog.admin_id)
        ).all()

        detections = []
        for admin_id, total, avg_ms, min_ms in rows:
            if avg_ms is None or avg_ms >= BLIND_APPROVAL_THRESHOLD_MS:
                continue
            user = session.get(User, admin_id)
            target = f"{user.employee_id} ({user.name})" if user else str(admin_id)
            # 점수: 평균 결재시간이 짧을수록 위험 (1초=60점 기준 선형, 상한 95)
            score = min(95, 60 + int((BLIND_APPROVAL_THRESHOLD_MS - avg_ms) / BLIND_APPROVAL_THRESHOLD_MS * 35))
            detections.append({
                "rule_code": "R1_BLIND_APPROVAL",
                "target_type": "ADMIN",
                "target_name": target,
                "fraud_score": score,
                "evidence": {
                    "total_reviews": int(total),
                    "avg_duration_ms": round(float(avg_ms), 1),
                    "min_duration_ms": int(min_ms) if min_ms is not None else None,
                    "threshold_ms": BLIND_APPROVAL_THRESHOLD_MS,
                },
            })
        return detections

    def _rule_grade_override(self, session: Session) -> List[Dict[str, Any]]:
        """R2: AI 제안등급 대비 2단계 이상 상향 승인이 반복되는 관리자."""
        rows = session.exec(
            select(AdminAuditLog).where(AdminAuditLog.action.like("APPROVE%"))
        ).all()

        per_admin: Dict[Any, List[int]] = {}
        for log in rows:
            target_grade = (log.target_grade or "").upper()
            if target_grade not in _GRADE_RANK:
                continue
            # 당시 AI 제안 등급은 대상 ReturnJob의 agent_logs.suggested_grade에 있음
            from app.models.wms import ReturnJob
            try:
                from uuid import UUID as _UUID
                job = session.get(ReturnJob, _UUID(log.target_id))
            except Exception:
                job = None
            suggested = ((job.agent_logs or {}).get("suggested_grade") or "").upper() if job else ""
            if suggested not in _GRADE_RANK:
                continue
            steps_up = _GRADE_RANK[target_grade] - _GRADE_RANK[suggested]
            if steps_up >= GRADE_OVERRIDE_MIN_STEPS:
                per_admin.setdefault(log.admin_id, []).append(steps_up)

        detections = []
        for admin_id, ups in per_admin.items():
            if len(ups) < GRADE_OVERRIDE_MIN_COUNT:
                continue
            user = session.get(User, admin_id)
            target = f"{user.employee_id} ({user.name})" if user else str(admin_id)
            score = min(95, 55 + len(ups) * 10 + max(ups) * 5)
            detections.append({
                "rule_code": "R2_GRADE_OVERRIDE",
                "target_type": "ADMIN",
                "target_name": target,
                "fraud_score": score,
                "evidence": {
                    "override_count": len(ups),
                    "max_steps_up": max(ups),
                    "min_required_steps": GRADE_OVERRIDE_MIN_STEPS,
                },
            })
        return detections

    def _rule_night_bulk(self, session: Session) -> List[Dict[str, Any]]:
        """R3: 야간(22~06시 KST) 생성 + 임계 금액 초과 주문."""
        since = now_kst() - timedelta(days=7)
        orders = session.exec(
            select(Order).where(Order.created_at >= since, Order.type != "AUTO_PO")
        ).all()

        detections = []
        for o in orders:
            hour = o.created_at.hour if o.created_at else 12
            is_night = hour >= 22 or hour < 6
            if not is_night or (o.total_price or 0) < NIGHT_BULK_MIN_PRICE:
                continue
            score = min(90, 50 + int((o.total_price - NIGHT_BULK_MIN_PRICE) / 100_000))
            detections.append({
                "rule_code": "R3_NIGHT_BULK",
                "target_type": "CUSTOMER",
                "target_name": o.customer_name or "미상 고객",
                "fraud_score": score,
                "evidence": {
                    "order_id": str(o.id),
                    "order_hour_kst": hour,
                    "total_price": float(o.total_price or 0),
                    "threshold_price": NIGHT_BULK_MIN_PRICE,
                },
            })
        return detections

    def _rule_return_abuse(self, session: Session) -> List[Dict[str, Any]]:
        """R4: 최근 30일 내 동일 고객의 반복 반품 요청."""
        since = now_kst() - timedelta(days=30)
        rows = session.exec(
            select(Order.customer_name, func.count(Order.id))
            .where(
                Order.created_at >= since,
                Order.status == OrderStatusEnum.RETURN_REQUESTED.value,
                Order.customer_name.is_not(None),
            )
            .group_by(Order.customer_name)
        ).all()

        detections = []
        for customer, cnt in rows:
            if cnt < RETURN_ABUSE_MIN_COUNT:
                continue
            score = min(90, 45 + int(cnt) * 10)
            detections.append({
                "rule_code": "R4_RETURN_ABUSE",
                "target_type": "CUSTOMER",
                "target_name": customer,
                "fraud_score": score,
                "evidence": {
                    "return_count_30d": int(cnt),
                    "threshold_count": RETURN_ABUSE_MIN_COUNT,
                },
            })
        return detections

    # ---------- Analyst Agent (gpt-4o-mini, 서술만) ----------

    _RULE_LABEL = {
        "R1_BLIND_APPROVAL": "블라인드 결재 의심 (평균 결재시간 임계 미만)",
        "R2_GRADE_OVERRIDE": "AI 제안등급 대비 과도한 상향 승인 반복",
        "R3_NIGHT_BULK": "야간 대량 주문 패턴",
        "R4_RETURN_ABUSE": "단기간 반복 반품 요청",
    }

    _FALLBACK_ACTION = {
        "R1_BLIND_APPROVAL": "해당 관리자의 최근 결재 건 표본을 추출해 재검토하고, 반복 시 결재 권한 조정을 검토하십시오.",
        "R2_GRADE_OVERRIDE": "상향 승인된 품목의 실물 재검수를 지시하고, 오버라이드 사유 기재를 의무화하십시오.",
        "R3_NIGHT_BULK": "해당 주문의 출고를 보류하고 고객사 담당자에게 주문 의사를 유선으로 재확인하십시오.",
        "R4_RETURN_ABUSE": "해당 고객사의 반품 승인 프로세스를 수동 검토로 전환하고 반품 사유 증빙을 요구하십시오.",
    }

    def _analyze_with_agent(self, detection: Dict[str, Any]) -> Dict[str, str]:
        """룰 엔진이 확정한 사실만 입력으로 받아 해석/권고 서술을 생성. 실패 시 템플릿 폴백."""
        rule = detection["rule_code"]
        fallback = {
            "fraud_reason": f"[{self._RULE_LABEL.get(rule, rule)}] 대상: {detection['target_name']} / 근거: {json.dumps(detection['evidence'], ensure_ascii=False)}"[:255],
            "recommended_action": self._FALLBACK_ACTION.get(rule, "관리자 수동 검토가 필요합니다."),
        }
        if not _analyst_llm:
            return fallback
        try:
            from langchain_core.messages import HumanMessage
            structured = _analyst_llm.with_structured_output(_AnalystVerdict)
            prompt = f"""당신은 B2B 도서 물류센터의 FDS(이상거래 탐지) 분석 담당 AI입니다.
아래는 결정론적 룰 엔진이 이미 확정한 적발 사실입니다. 수치를 새로 판단하거나 바꾸지 말고,
이 사실을 바탕으로 (1) 정황 해석 한 문장(fraud_reason, 255자 이내)과
(2) 관리자가 취할 권고 조치 1~2문장(recommended_action)만 한국어로 작성하세요.

적발 룰: {rule} ({self._RULE_LABEL.get(rule, rule)})
적발 대상: {detection['target_name']} (유형: {detection['target_type']})
위험 점수: {detection['fraud_score']}점 (룰 엔진 산출 - 변경 금지)
근거 수치: {json.dumps(detection['evidence'], ensure_ascii=False)}
"""
            verdict: _AnalystVerdict = structured.invoke([HumanMessage(content=prompt)])
            return {
                "fraud_reason": verdict.fraud_reason[:255],
                "recommended_action": verdict.recommended_action,
            }
        except Exception as e:
            logger.warning(f"[FDS Analyst Agent] LLM 서술 생성 실패, 결정론적 템플릿 폴백: {e}")
            return fallback

    # ---------- 오케스트레이션 ----------

    def run_scan(self, session: Session, use_agent: bool = True) -> Dict[str, Any]:
        """
        전체 룰 스캔 실행. 신규 적발 건은 fds_reports에 INSERT하고
        notifications:global 채널에 실시간 발행한다.
        동일 룰+대상은 DEDUP_WINDOW_HOURS 내 재적발을 억제한다 (알림 스팸 방지).
        """
        detections: List[Dict[str, Any]] = []
        for rule_fn in (self._rule_blind_approval, self._rule_grade_override,
                        self._rule_night_bulk, self._rule_return_abuse):
            try:
                detections.extend(rule_fn(session))
            except Exception as e:
                logger.warning(f"[FDS] 룰 실행 실패 ({rule_fn.__name__}): {e}")

        dedup_since = now_kst() - timedelta(hours=DEDUP_WINDOW_HOURS)
        new_reports: List[FdsReport] = []
        skipped = 0

        for det in detections:
            exists = session.exec(
                select(FdsReport).where(
                    FdsReport.rule_code == det["rule_code"],
                    FdsReport.customer_name == det["target_name"],
                    FdsReport.detected_at >= dedup_since,
                )
            ).first()
            if exists:
                skipped += 1
                continue

            narrative = self._analyze_with_agent(det) if use_agent else {
                "fraud_reason": f"[{self._RULE_LABEL.get(det['rule_code'], det['rule_code'])}] {det['target_name']}"[:255],
                "recommended_action": self._FALLBACK_ACTION.get(det["rule_code"], ""),
            }

            report = FdsReport(
                customer_name=det["target_name"],
                fraud_score=det["fraud_score"],
                fraud_reason=narrative["fraud_reason"],
                rule_code=det["rule_code"],
                target_type=det["target_type"],
                recommended_action=narrative["recommended_action"],
            )
            session.add(report)
            new_reports.append(report)

        session.commit()

        # 신규 적발 건 실시간 알림 발행 (기존 notifications:global 채널 재사용 -> Header 종 실데이터화)
        for report in new_reports:
            self._publish_alert(report)

        return {
            "scanned_rules": 4,
            "raw_detections": len(detections),
            "new_reports": len(new_reports),
            "deduplicated": skipped,
        }

    def _publish_alert(self, report: FdsReport) -> None:
        try:
            import redis as sync_redis
            from app.core.redis_pubsub import REDIS_URL
            from app.domains.notifications.router import NOTIFICATIONS_CHANNEL

            event = {
                "type": "FDS_ALERT",
                "category": "FDS 이상거래",
                "title": f"FDS 적발: {report.customer_name} (위험 {report.fraud_score}점)",
                "description": report.fraud_reason or "",
                "rule_code": report.rule_code,
                "timestamp": now_kst().isoformat(),
            }
            client = sync_redis.Redis.from_url(REDIS_URL, decode_responses=True)
            try:
                client.publish(NOTIFICATIONS_CHANNEL, json.dumps(event, ensure_ascii=False))
            finally:
                client.close()
        except Exception as e:
            logger.warning(f"[FDS] 실시간 알림 발행 실패 (적발 저장은 완료됨): {e}")

    # ---------- 조회 ----------

    def list_reports(self, session: Session, limit: int = 50) -> List[Dict[str, Any]]:
        rows = session.exec(
            select(FdsReport).order_by(FdsReport.detected_at.desc()).limit(limit)
        ).all()
        return [
            {
                "id": str(r.id),
                "target_name": r.customer_name,
                "target_type": r.target_type or "CUSTOMER",
                "rule_code": r.rule_code or "SIMULATED",
                "fraud_score": r.fraud_score,
                "fraud_reason": r.fraud_reason,
                "recommended_action": r.recommended_action,
                "detected_at": r.detected_at.isoformat() if r.detected_at else None,
            }
            for r in rows
        ]

    def summary(self, session: Session) -> Dict[str, Any]:
        week_ago = now_kst() - timedelta(days=7)
        total = session.exec(select(func.count(FdsReport.id))).one() or 0
        this_week = session.exec(
            select(func.count(FdsReport.id)).where(FdsReport.detected_at >= week_ago)
        ).one() or 0
        by_rule = session.exec(
            select(FdsReport.rule_code, func.count(FdsReport.id)).group_by(FdsReport.rule_code)
        ).all()
        recent = self.list_reports(session, limit=3)
        return {
            "total_reports": int(total),
            "this_week": int(this_week),
            "by_rule": {str(code or "SIMULATED"): int(cnt) for code, cnt in by_rule},
            "recent": recent,
        }


fds_service = FdsService()
