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

from app.core.constants import format_worker_label
from app.models.wms import (
    AdminAuditLog, FdsReport, Order, OrderStatusEnum, User, now_kst,
)

logger = logging.getLogger(__name__)

# 등급 서열 (R2 상향 폭 계산용)
_GRADE_RANK = {"REJECT": 0, "NORMAL": 1, "GOOD": 2, "MINT": 3}

# 룰별 임계값 (결정론적 상수 - 변경 시 이 파일만 수정)
BLIND_APPROVAL_THRESHOLD_MS = 1000     # R1: 평균 결재시간 1초 미만
BLIND_APPROVAL_WINDOW_DAYS = 7         # R1: 관측창 (누적 평균은 이력에 희석돼 탐지 불능)
BLIND_APPROVAL_MIN_SAMPLES = 5         # R1: 관측창 내 최소 결재 건수 (표본 부족 오탐 방지)
# R1: 관측창 내 "임계 미만" 결재의 비율 하한. 평균 대신 비율을 쓰는 이유는 아래 룰 주석 참조.
BLIND_APPROVAL_MIN_FAST_RATIO = 0.6
GRADE_OVERRIDE_MIN_STEPS = 2           # R2: 2단계 이상 상향
GRADE_OVERRIDE_MIN_COUNT = 2           # R2: 반복 기준 횟수
GRADE_OVERRIDE_WINDOW_DAYS = 30        # R2: 관측창 (누적 카운트는 영구 적발 상태로 굳는다)
# R3: 야간 주문 임계 금액. B2B 서점 발주는 50만원(수십 권)이 일상 규모라 금액 단독으로는
# 정상 거래를 무더기로 잡는다. 절대 하한을 올리고, 이력이 있는 고객은 "평소 대비 배수"를
# 함께 본다 - 같은 금액이라도 그 거래처에게 이례적인지가 실제 이상 신호다.
NIGHT_BULK_MIN_PRICE = 2_000_000       # R3: 야간 주문 절대 하한 (원)
NIGHT_BULK_OUTLIER_RATIO = 3.0         # R3: 해당 고객 평소 주문 평균 대비 배수
NIGHT_BULK_HISTORY_DAYS = 90           # R3: 평소 주문 평균을 낼 관측 구간
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
        """R1: 최근 관측창에서 관리자 평균 HITL 결재시간이 임계 미만이면 블라인드 결재 의심.

        [수정 이력 2026-08-12] 종전에는 **전 기간 누적 평균**을 봤다. 두 가지가 잘못됐다:
        1. 오래전 신중했던 결재가 지금의 블라인드 결재를 영구히 희석한다. 실측(운영):
           결재 5건 누적 평균 133초인 관리자는, 지금부터 0.5초짜리 결재를 **1,325건**
           연속으로 해야 비로소 임계선을 넘는다. 사실상 탐지 불능이다.
        2. 이력이 쌓일수록 룰이 점점 더 둔해진다 - 운영 기간에 반비례해 무력화된다.
        FDS는 "지금 이 사람이 어떻게 결재하고 있는가"를 봐야 하므로 관측창을 최근 N일로
        자른다. 표본이 너무 적으면(우연히 빠른 1~2건) 오탐이 되므로 최소 건수도 함께 본다.

        3. **평균이 아니라 비율로 판단한다.** 관측창을 잘라도 평균은 이상치 한 건에
           무너진다 - 200초짜리 신중한 결재 1건이 0.5초짜리 날림 결재 12건을 덮어
           평균을 16초로 만든다(실측). 블라인드 결재의 실체는 "평균이 짧다"가 아니라
           "들여다보지 않고 넘긴 건이 많다"이므로, 임계 미만 건의 **비율**을 본다.
           비율은 이상치에 견고하고 근거로 제시하기도 명확하다("12건 중 10건이 1초 미만").
        """
        since = now_kst() - timedelta(days=BLIND_APPROVAL_WINDOW_DAYS)
        rows = session.exec(
            select(AdminAuditLog).where(
                AdminAuditLog.review_duration_ms.is_not(None),
                AdminAuditLog.created_at >= since,
            )
        ).all()

        per_admin: Dict[Any, List[int]] = {}
        for log in rows:
            per_admin.setdefault(log.admin_id, []).append(int(log.review_duration_ms))

        detections = []
        for admin_id, durations in per_admin.items():
            total = len(durations)
            if total < BLIND_APPROVAL_MIN_SAMPLES:
                continue  # 표본 부족 - 우연히 빠른 소수 건으로 사람을 지목하지 않는다

            fast = [d for d in durations if d < BLIND_APPROVAL_THRESHOLD_MS]
            fast_ratio = len(fast) / total
            if fast_ratio < BLIND_APPROVAL_MIN_FAST_RATIO:
                continue

            user = session.get(User, admin_id)
            target = format_worker_label(user.employee_id, user.name) if user else str(admin_id)
            # 점수: 날림 결재 비율이 높을수록 위험 (60% = 60점 기준, 100% = 95점)
            score = min(95, 60 + int((fast_ratio - BLIND_APPROVAL_MIN_FAST_RATIO) * 100))
            detections.append({
                "rule_code": "R1_BLIND_APPROVAL",
                "target_type": "ADMIN",
                "target_name": target,
                "fraud_score": score,
                "evidence": {
                    "window_days": BLIND_APPROVAL_WINDOW_DAYS,
                    "total_reviews": total,
                    "fast_reviews": len(fast),
                    "fast_ratio_pct": round(fast_ratio * 100, 1),
                    "median_duration_ms": sorted(durations)[total // 2],
                    "threshold_ms": BLIND_APPROVAL_THRESHOLD_MS,
                    "required_ratio_pct": round(BLIND_APPROVAL_MIN_FAST_RATIO * 100),
                },
            })
        return detections

    def _rule_grade_override(self, session: Session) -> List[Dict[str, Any]]:
        """R2: 최근 관측창에서 AI 제안등급 대비 2단계 이상 상향 승인이 반복되는 관리자.

        [수정 이력 2026-08-12] 종전에는 전 기간을 봤다. 누적 카운트라 한 번 임계에 닿으면
        **영원히 적발 상태로 남는다** - 반년 전 오버라이드 2건 때문에 지금도 매 스캔마다
        같은 사람이 올라온다(중복 억제는 24시간짜리라 그 뒤엔 다시 뜬다). 등급은 매입가를
        정하는 값이라 최근 행태를 봐야 의미가 있으므로 관측창을 자른다.
        """
        since = now_kst() - timedelta(days=GRADE_OVERRIDE_WINDOW_DAYS)
        rows = session.exec(
            select(AdminAuditLog).where(
                AdminAuditLog.action.like("APPROVE%"),
                AdminAuditLog.created_at >= since,
            )
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
            target = format_worker_label(user.employee_id, user.name) if user else str(admin_id)
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
        """R3: 야간(22~06시 KST) + 절대 하한 초과 + 그 고객 평소 대비 이례적인 주문.

        [수정 이력 2026-08-12] 종전에는 "야간 AND 50만원 초과"만 봤다. B2B 서점 발주에서
        50만원은 수십 권 규모라 일상 거래이고, 업무 시간 외 발주도 흔하다 - 정상 거래를
        무더기로 적발하는 룰이었다. 절대 하한을 200만원으로 올리고, **거래 이력이 있는
        고객은 평소 평균의 3배를 함께 넘겨야** 적발한다. 같은 금액이라도 그 거래처에게
        이례적인지가 실제 신호이기 때문이다. 신규 고객(이력 없음)은 비교 기준이 없으므로
        절대 하한만으로 판단한다.
        """
        now = now_kst()
        since = now - timedelta(days=7)
        history_since = now - timedelta(days=NIGHT_BULK_HISTORY_DAYS)

        orders = session.exec(
            select(Order).where(Order.created_at >= since, Order.type != "AUTO_PO")
        ).all()

        detections = []
        for o in orders:
            hour = o.created_at.hour if o.created_at else 12
            is_night = hour >= 22 or hour < 6
            price = float(o.total_price or 0)
            if not is_night or price < NIGHT_BULK_MIN_PRICE:
                continue

            # 이 고객의 평소 주문 규모 (이번 주문 자신은 제외)
            avg_row = session.exec(
                select(func.avg(Order.total_price)).where(
                    Order.customer_name == o.customer_name,
                    Order.created_at >= history_since,
                    Order.id != o.id,
                    Order.type != "AUTO_PO",
                )
            ).one()
            baseline = float(avg_row) if avg_row else 0.0

            if baseline > 0 and price < baseline * NIGHT_BULK_OUTLIER_RATIO:
                continue  # 이 거래처에겐 평소 규모 - 야간이라는 이유만으로 잡지 않는다

            ratio = round(price / baseline, 1) if baseline > 0 else None
            score = min(90, 50 + int((price - NIGHT_BULK_MIN_PRICE) / 500_000) * 5)
            detections.append({
                "rule_code": "R3_NIGHT_BULK",
                "target_type": "CUSTOMER",
                "target_name": o.customer_name or "미상 고객",
                "fraud_score": score,
                "evidence": {
                    "order_id": str(o.id),
                    "order_hour_kst": hour,
                    "total_price": price,
                    "threshold_price": NIGHT_BULK_MIN_PRICE,
                    "customer_avg_90d": round(baseline) if baseline else None,
                    "outlier_ratio": ratio,
                    "required_ratio": NIGHT_BULK_OUTLIER_RATIO if baseline > 0 else None,
                },
            })
        return detections

    def _rule_return_abuse(self, session: Session) -> List[Dict[str, Any]]:
        """R4: 최근 30일 내 동일 고객의 반복 반품 요청.

        [실질 미발동 / 확장예정 — 2026-08-12 확인]
        이 룰은 `Order.status == RETURN_REQUESTED`를 조회하는데, **런타임에 그 상태를
        세팅하는 코드 경로가 없다**(전수 grep: 이 룰과 대시보드 반품 지표가 읽기만 한다).
        B2B 출고 반품 접수 플로우가 아직 구현되지 않았기 때문이다. 시드 데이터로 심은
        행은 잡히므로(로컬 1건 실측) 룰 자체는 정상 동작하나, 운영 DB에서는 0건이다.
        반품 플로우가 생기면 그대로 동작하므로 삭제하지 않고 존치한다.
        """
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
        """FDS 적발을 알림으로 발행한다 (DB 적재 + 실시간 채널).

        [수정 이력 2026-08-12] 종전에는 Redis 채널에 직접 publish만 하고 notifications
        테이블에는 남기지 않았다. 헤더 종 아이콘은 DB를 읽으므로 새로고침하면 사라졌고,
        마침 SSE를 보고 있지 않았다면 애초에 뜨지도 않았다 - 그래서 FDS 관제 화면에
        알림이 하나도 쌓이지 않았다. 출고 알림(emit_outbound_event)이 겪은 것과 같은
        결함이며, 같은 처방으로 emit()에 위임해 적재와 발행을 한 번에 처리한다.
        """
        try:
            from app.domains.notifications.service import emit

            emit(
                type="FDS_ALERT",
                title=f"FDS 적발: {report.customer_name} (위험 {report.fraud_score}점)",
                description=report.fraud_reason or "",
                ref_type="FDS_REPORT",
                ref_id=str(report.id),
                target_role="ADMIN",
            )
        except Exception as e:
            logger.warning(f"[FDS] 알림 발행 실패 (적발 저장은 완료됨): {e}")

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
