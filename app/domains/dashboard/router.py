from fastapi import APIRouter, Depends, Query
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy import or_
from sqlmodel import Session, select, func
from app.db.session import get_db
from app.models.wms import (
    ReturnJob, InventoryUsedItem, Inventory, Order, Book, JobStatusEnum, ubci_grade_from_score,
)
from app.core.security import RoleChecker, UserRoleEnum
from app.models.wms import now_kst

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
    dependencies=[Depends(RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN]))]
)

@router.get("/kpi")
def get_kpi(session: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    오늘의 실시간 핵심 성과 지표(KPI)를 DB SQL 집계 쿼리로 반환합니다.

    [수정 이력 2026-08-04] 자동 승인율/반려율이 프론트에 91.7%/4.8%로 하드코딩되어 있던 것을
    return_jobs 실집계로 교체 - approval_rate/rejection_rate/hitl_rate 필드 신설.
    """
    today_start = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)

    # 1. 오늘 검수 건수 (ReturnJob)
    today_inspection = session.exec(
        select(func.count(ReturnJob.id)).where(ReturnJob.created_at >= today_start)
    ).one() or 0

    # 2. 승인 대기 건수 (HITL_REQUIRED)
    pending_issues = session.exec(
        select(func.count(ReturnJob.id)).where(ReturnJob.status == JobStatusEnum.HITL_REQUIRED)
    ).one() or 0

    # 3. 오늘 입고 완료 수량 (InventoryUsedItem)
    today_inbound = session.exec(
        select(func.count(InventoryUsedItem.id)).where(InventoryUsedItem.created_at >= today_start)
    ).one() or 0

    # 4. 오늘 출고 완료 주문 수량 (Order)
    today_outbound = session.exec(
        select(func.count(Order.id)).where(Order.created_at >= today_start)
    ).one() or 0

    # 5. 전체 기간 승인/반려/HITL 이관율 실집계
    approved = session.exec(
        select(func.count(ReturnJob.id)).where(ReturnJob.status == JobStatusEnum.APPROVED)
    ).one() or 0
    rejected = session.exec(
        select(func.count(ReturnJob.id)).where(ReturnJob.status == JobStatusEnum.REJECTED)
    ).one() or 0
    decided = approved + rejected + pending_issues

    return {
        "today_inbound": today_inbound,
        "today_outbound": today_outbound,
        "today_inspection": today_inspection,
        "pending_issues": pending_issues,
        "approval_rate": round(approved / decided * 100, 1) if decided else 0.0,
        "rejection_rate": round(rejected / decided * 100, 1) if decided else 0.0,
        "hitl_rate": round(pending_issues / decided * 100, 1) if decided else 0.0,
        "decided_total": decided,
    }

@router.get("/charts")
def get_dashboard_charts(session: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    7일간 일별 입출고 물량, 등급 분포, 카테고리 분포 SQL 실집계 데이터 반환.

    [수정 이력, 2026-08-04] ReturnJob 모델에는 final_grade 컬럼이 없어 기존
    `select(ReturnJob.final_grade, ...)` 쿼리는 실행 시 크래시했다. ubci_score를
    조회해 ubci_grade_from_score()로 Python 단에서 등급 버켓팅하도록 교정.
    volume_data/category_data도 하드코딩을 걷어내고 실집계로 교체.
    """
    seven_days_ago = now_kst() - timedelta(days=7)

    # 보유 중고 재고의 등급 분포 (UBCI_Specification_v2.0.0.0.md 공식 경계값 기준 버켓팅).
    #
    # 검수 이력(return_jobs)이 아니라 재고를 센다. 파이프라인을 태운 건만 세면 이관·시드
    # 등 다른 경로로 들어온 재고가 빠져 "보유 재고의 품질 구성"을 나타내지 못한다.
    # 신품 Fast-track은 무검수 입고라 UBCI 점수가 없으므로 이 분포에 포함되지 않는다.
    #
    # condition_grade 문자열이 아니라 ubci_score로 버켓팅한다. 레거시 행에 'A'/'B' 같은
    # 비표준 등급 값이 남아 있어 문자열로 세면 등급 축이 깨진다.
    score_rows = session.exec(
        select(InventoryUsedItem.ubci_score).where(
            InventoryUsedItem.ubci_score.is_not(None),
            or_(
                InventoryUsedItem.item_status.is_(None),
                InventoryUsedItem.item_status.notin_(
                    ["HITL_PENDING", "HITL_REQUIRED", "PENDING_INSPECTION"]
                ),
            ),
        )
    ).all()
    grade_counts = {"MINT": 0, "GOOD": 0, "NORMAL": 0, "REJECT": 0}
    for score in score_rows:
        grade_counts[ubci_grade_from_score(score)] += 1

    # 신품 Fast-track은 무검수 입고라 UBCI 점수가 없고, 수량 단위도 다르다(권수 vs LPN 건수).
    # 같은 파이에 넣으면 신품 물량이 등급 조각을 압도해 등급 구성을 읽을 수 없다.
    # 신품/중고 비교는 카테고리별 누적 막대가 담당한다.
    ubci_grade_data = [
        {"name": "MINT (95~100점)", "value": grade_counts["MINT"], "color": "#10b981"},
        {"name": "GOOD (85~94점)", "value": grade_counts["GOOD"], "color": "#3b82f6"},
        {"name": "NORMAL (65~84점)", "value": grade_counts["NORMAL"], "color": "#f59e0b"},
        {"name": "REJECT (65점 미만)", "value": grade_counts["REJECT"], "color": "#ef4444"},
    ]

    # 7일간 일별 입출고 실집계 (입고: InventoryUsedItem 생성 시각, 출고: AUTO_PO를 제외한 Order 생성 시각)
    volume_data = []
    for i in range(7):
        day_start = (seven_days_ago + timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        inbound_count = session.exec(
            select(func.count(InventoryUsedItem.id)).where(
                InventoryUsedItem.created_at >= day_start,
                InventoryUsedItem.created_at < day_end,
            )
        ).one() or 0

        outbound_count = session.exec(
            select(func.count(Order.id)).where(
                Order.created_at >= day_start,
                Order.created_at < day_end,
                Order.type != "AUTO_PO",
            )
        ).one() or 0

        volume_data.append({
            "date": day_start.strftime("%m-%d"),
            "inbound": inbound_count,
            "outbound": outbound_count,
        })

    # 카테고리별 실재고 보유 수량 집계 (중고 / 신품 분리).
    #
    # 도서 마스터(Book) 종수가 아니라 실제로 창고에 있는 수량을 센다. 재고가 0인
    # 카테고리도 마스터에는 존재하므로, 종수를 세면 보유 현황이 부풀려진다.
    #   - 중고: LPN 단위 1권 = 1행. 검수·결재 대기 건은 아직 적재 전이라 제외한다.
    #   - 신품: Fast-track 입고분으로 수량(quantity) 합계.
    used_rows = session.exec(
        select(Book.category_type, func.count(InventoryUsedItem.id))
        .join(Book, InventoryUsedItem.book_id == Book.id)
        .where(
            or_(
                InventoryUsedItem.item_status.is_(None),
                InventoryUsedItem.item_status.notin_(
                    ["HITL_PENDING", "HITL_REQUIRED", "PENDING_INSPECTION"]
                ),
            )
        )
        .group_by(Book.category_type)
    ).all()

    new_rows = session.exec(
        select(Book.category_type, func.coalesce(func.sum(Inventory.quantity), 0))
        .join(Book, Inventory.book_id == Book.id)
        .group_by(Book.category_type)
    ).all()

    totals: Dict[str, Dict[str, int]] = {}
    for category, count in used_rows:
        totals.setdefault(category or "GENERAL", {"used": 0, "new": 0})["used"] += int(count or 0)
    for category, qty in new_rows:
        totals.setdefault(category or "GENERAL", {"used": 0, "new": 0})["new"] += int(qty or 0)

    category_data = sorted(
        (
            {"name": name, "used": v["used"], "new": v["new"], "count": v["used"] + v["new"]}
            for name, v in totals.items()
        ),
        key=lambda r: r["count"],
        reverse=True,
    )

    return {
        "volume_data": volume_data,
        "ubci_grade_data": ubci_grade_data,
        "category_data": category_data,
    }

@router.get("/logs")
def get_dashboard_logs(
    limit: int = Query(30, ge=1, le=100),
    since: Optional[datetime] = Query(
        None,
        description="이 시각 이후 생성된 건만 반환. 화면의 로그 비우기가 기준 시각을 넘겨 쓴다.",
    ),
    session: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """
    최근 AI 비전 검수 트랜잭션 로그 (return_jobs 원장, 최신순).

    `since`는 화면 표시를 자르기 위한 값일 뿐 원장을 지우지 않는다. 검수 이력은
    매입가 산정 근거이자 감사 대상이므로 조회 API가 삭제 수단을 제공하지 않는다.
    """
    stmt = select(ReturnJob, Book).outerjoin(Book, ReturnJob.book_id == Book.id)
    if since is not None:
        stmt = stmt.where(ReturnJob.created_at > since)

    rows = session.exec(stmt.order_by(ReturnJob.created_at.desc()).limit(limit)).all()

    logs = []
    for job, book in rows:
        grade = ubci_grade_from_score(job.ubci_score) if job.ubci_score is not None else None
        logs.append({
            "id": str(job.id),
            "transaction_type": "INBOUND_INSPECTION" if job.status == JobStatusEnum.APPROVED else "HITL_PENDING",
            # 실제 도서명을 쓴다. 도서를 특정하지 못한 건은 지어내지 않고 null로 둔다.
            "book_title": book.title if book else None,
            "condition_grade": grade,
            "quantity_change": 1,
            "date": job.created_at.isoformat() if job.created_at else None,
        })

    return logs

@router.get("/weekly-insights")
def get_weekly_insights(session: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    주간 인사이트 스냅샷 (weekly_insights 테이블 - 지연 생성 배치).

    [설계 2026-08-04] 별도 스케줄러(Celery Beat) 없이, 요청 시점에 이번 ISO 주차 row가 없으면
    즉석에서 집계·생성해 저장한다(지연 물질화). 모든 수치는 결정론적 SQL 집계이고,
    ai_narrative 서사 문장만 Insight Analyst(gpt-4o-mini)가 생성한다 (실패 시 템플릿 폴백).
    """
    from datetime import timedelta
    from app.models.wms import WeeklyInsight, Book

    now = now_kst()
    iso = now.isocalendar()
    report_week = f"{iso[0]}-W{iso[1]:02d}"

    existing = session.exec(
        select(WeeklyInsight).where(WeeklyInsight.report_week == report_week)
    ).first()
    if existing:
        return _serialize_weekly_insight(existing, cached=True)

    week_start = now - timedelta(days=7)

    # 1) 이번 주 검수 건수 -> 절감 인건비 추정 (검수 1건당 수작업 6분 vs AI 30초, 시급 12,000원 상수)
    week_inspections = session.exec(
        select(func.count(ReturnJob.id)).where(ReturnJob.created_at >= week_start)
    ).one() or 0
    saved_minutes_per_item = 6 - 0.5
    saved_labor_cost = int(week_inspections * saved_minutes_per_item / 60 * 12000)

    # 2) 결함 다발 출판사 Top 3 (반려 건 기준)
    pub_rows = session.exec(
        select(Book.publisher, func.count(ReturnJob.id))
        .join(Book, ReturnJob.book_id == Book.id)
        .where(ReturnJob.status == JobStatusEnum.REJECTED, ReturnJob.created_at >= week_start)
        .group_by(Book.publisher)
        .order_by(func.count(ReturnJob.id).desc())
        .limit(3)
    ).all()
    top_publishers = {"items": [{"publisher": p or "미상", "reject_count": int(c)} for p, c in pub_rows]}

    # 3) 창고 Zone 점유 핫스팟 (중고 재고 기준)
    from app.models.wms import Location
    zone_rows = session.exec(
        select(Location.zone, func.count(InventoryUsedItem.id))
        .join(Location, InventoryUsedItem.location_id == Location.id)
        .group_by(Location.zone)
        .order_by(func.count(InventoryUsedItem.id).desc())
    ).all()
    location_hotspots = {"zones": [{"zone": z, "count": int(c)} for z, c in zone_rows]}

    # 4) 반품 예측 (최근 4주 반품 요청 단순 이동평균 - 결정론적)
    four_weeks_ago = now - timedelta(days=28)
    recent_returns = session.exec(
        select(func.count(Order.id)).where(
            Order.status == "RETURN_REQUESTED", Order.created_at >= four_weeks_ago
        )
    ).one() or 0
    predicted_returns = round(recent_returns / 4)

    # 5) 주간 물류 처리량 (입고/출고)
    week_inbound = session.exec(
        select(func.count(InventoryUsedItem.id)).where(InventoryUsedItem.created_at >= week_start)
    ).one() or 0
    week_orders = session.exec(
        select(func.count(Order.id)).where(Order.created_at >= week_start, Order.type != "AUTO_PO")
    ).one() or 0
    logistics = {"week_inbound": int(week_inbound), "week_orders": int(week_orders),
                 "week_inspections": int(week_inspections)}

    stats_for_narrative = {
        "report_week": report_week,
        "week_inspections": int(week_inspections),
        "saved_labor_cost_krw": saved_labor_cost,
        "top_defective_publishers": top_publishers["items"],
        "zone_hotspots": location_hotspots["zones"][:3],
        "predicted_returns_next_week": predicted_returns,
        "week_inbound": int(week_inbound),
        "week_orders": int(week_orders),
    }
    narrative = _generate_insight_narrative(stats_for_narrative)

    insight = WeeklyInsight(
        report_week=report_week,
        saved_labor_cost_krw=saved_labor_cost,
        top_defective_publishers=top_publishers,
        location_hotspots=location_hotspots,
        logistics_hotspots=logistics,
        predicted_returns=predicted_returns,
        ai_narrative=narrative,
    )
    session.add(insight)
    session.commit()
    session.refresh(insight)
    return _serialize_weekly_insight(insight, cached=False)


def _serialize_weekly_insight(w, cached: bool) -> Dict[str, Any]:
    return {
        "report_week": w.report_week,
        "saved_labor_cost_krw": w.saved_labor_cost_krw,
        "top_defective_publishers": w.top_defective_publishers or {"items": []},
        "location_hotspots": w.location_hotspots or {"zones": []},
        "logistics": w.logistics_hotspots or {},
        "predicted_returns": w.predicted_returns,
        "ai_narrative": w.ai_narrative,
        "generated_at": w.created_at.isoformat() if w.created_at else None,
        "cached": cached,
    }


def _generate_insight_narrative(stats: Dict[str, Any]) -> str:
    """집계 수치(결정론)를 입력으로 주간 경영 서사만 생성. LLM 장애 시 템플릿 폴백."""
    fallback = (
        f"{stats['report_week']} 주간: AI 검수 {stats['week_inspections']}건 처리로 "
        f"약 {stats['saved_labor_cost_krw']:,}원의 검수 인건비를 절감했습니다. "
        f"다음 주 반품은 약 {stats['predicted_returns_next_week']}건으로 예상됩니다."
    )
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage
        import json as _json

        # [수정 이력] 수치를 raw 정수로 주면 LLM이 단위를 오해해 부풀리는 사고가 실제로
        # 발생했다 (절감액 8,800원 -> "8,800,000원"으로 서술). 금액/수량을 단위까지 붙인
        # 완성 문자열로 넘겨 인용만 하게 하고, 재구성 여지를 차단한다.
        formatted = dict(stats)
        formatted["saved_labor_cost_krw"] = f"{stats['saved_labor_cost_krw']:,}원"
        formatted["week_inspections"] = f"{stats['week_inspections']}건"
        formatted["predicted_returns_next_week"] = f"{stats['predicted_returns_next_week']}건"
        formatted["week_inbound"] = f"{stats['week_inbound']}건"
        formatted["week_orders"] = f"{stats['week_orders']}건"

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
        prompt = f"""당신은 B2B 도서 물류센터의 경영 분석 AI입니다. 아래 주간 집계 수치(이미 확정된
사실)를 바탕으로, 경영진 대시보드에 띄울 3~4문장의 한국어 주간 인사이트 요약을 작성하세요.
핵심 수치 인용 + 주목할 패턴 1가지 + 다음 주 관전 포인트 1가지 구성, 담백한 보고체.

[절대 규칙] 아래 JSON의 수치 문자열(예: "8,800원", "12건")을 **한 글자도 바꾸지 말고 그대로
인용**하세요. 단위를 바꾸거나(원->만원), 자릿수를 늘리거나, 새로운 숫자를 만들면 안 됩니다.

주간 집계(JSON): {_json.dumps(formatted, ensure_ascii=False)}"""
        result = llm.invoke([HumanMessage(content=prompt)])
        text = (result.content or "").strip()
        return text if text else fallback
    except Exception as e:
        print(f"[Weekly Insight] LLM 서사 생성 실패, 템플릿 폴백: {e}")
        return fallback


@router.get("/ai-quality")
def get_ai_quality_stats(session: Session = Depends(get_db)) -> Dict[str, int]:
    """
    AI 비전 모델 상태 등급 DB SQL 집계 통계 (ubci_score 기반 - final_grade 컬럼 없음)
    """
    score_rows = session.exec(
        select(ReturnJob.ubci_score).where(ReturnJob.ubci_score.is_not(None))
    ).all()

    counts = {"MINT": 0, "GOOD": 0, "NORMAL": 0, "REJECT": 0}
    for score in score_rows:
        counts[ubci_grade_from_score(score)] += 1

    # 집계 결과가 0건이면 0건 그대로 반환한다. 표본이 없을 때 임의 분포로 채우면
    # 화면이 검수 실적을 지어내게 된다.
    return counts
