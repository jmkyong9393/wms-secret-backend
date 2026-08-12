"""주간 인사이트 집계 서비스 (Celery Beat 크론 + API 폴백 공용).

[2026-08-12 리팩토링] 종전에는 이 로직이 `/dashboard/weekly-insights` 라우터 안에만 있었고,
"이번 ISO 주차 행이 없으면 요청 시점에 즉석 생성"하는 지연 물질화 방식이었다. 두 가지 결함이 있었다:

1. **라벨과 데이터 창이 어긋났다.** report_week는 ISO 주차인데 집계 창은 "지금부터 과거 7일"
   이었다. 월요일 0시 직후에 누가 처음 방문하면 새 주차 라벨에 지난주 꼬리 데이터가 담기고,
   그 값이 그대로 캐시로 굳었다(실측: 2026-W33이 08-10 00:01에 검수 0건 수준으로 고정됨).
2. **생성 시점이 "누가 언제 처음 들어왔는가"에 좌우됐다.** 주간 리포트는 매주 같은 기준으로
   확정돼야 신뢰할 수 있는 지표인데, 방문 시각이 집계 창을 바꾸면 지표의 의미가 흔들린다.

이제 집계 창을 ISO 주 경계(월 00:00 ~ 다음 월 00:00)로 못박고, Celery Beat이 매일 00:05 KST에
호출한다. API는 조회만 하되, 크론이 실패했거나 첫 배포 직후라 행이 없으면 같은 함수로 폴백해
생성한다(자기치유).
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from sqlmodel import Session, func, select

from app.models.wms import (
    Book, Inventory, InventoryUsedItem, JobStatusEnum, Location, Order,
    ReturnJob, WeeklyInsight, now_kst,
)

# 검수 1건당 수작업 6분 vs AI 30초, 시급 12,000원 (결정론적 상수)
MANUAL_MINUTES_PER_ITEM = 6.0
AI_MINUTES_PER_ITEM = 0.5
HOURLY_WAGE_KRW = 12000


def iso_week_bounds(ref: datetime) -> Tuple[str, datetime, datetime]:
    """`ref`가 속한 ISO 주의 (라벨, 시작, 끝)을 돌려준다.

    시작 = 그 주 월요일 00:00, 끝 = 다음 주 월요일 00:00 (끝은 배타적).
    라벨은 `2026-W33` 형식으로 report_week 컬럼과 동일하다.
    """
    day_start = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())  # 월요일=0
    week_end = week_start + timedelta(days=7)
    iso = week_start.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}", week_start, week_end


def build_weekly_insight(
    session: Session,
    ref: Optional[datetime] = None,
    *,
    force: bool = False,
) -> Tuple[WeeklyInsight, bool]:
    """`ref`가 속한 ISO 주의 인사이트를 집계해 저장하고 (행, 신규생성여부)를 돌려준다.

    force=False면 이미 있는 행을 그대로 반환한다(멱등). force=True면 재집계해 덮어쓴다 -
    진행 중인 주의 러닝 스냅샷을 매일 갱신할 때 쓴다.
    """
    ref = ref or now_kst()
    report_week, week_start, week_end = iso_week_bounds(ref)

    existing = session.exec(
        select(WeeklyInsight).where(WeeklyInsight.report_week == report_week)
    ).first()
    if existing and not force:
        return existing, False

    # 1) 주간 검수 건수 -> 절감 인건비 추정
    week_inspections = session.exec(
        select(func.count(ReturnJob.id)).where(
            ReturnJob.created_at >= week_start, ReturnJob.created_at < week_end
        )
    ).one() or 0
    saved_minutes = MANUAL_MINUTES_PER_ITEM - AI_MINUTES_PER_ITEM
    saved_labor_cost = int(week_inspections * saved_minutes / 60 * HOURLY_WAGE_KRW)

    # 2) 결함 다발 출판사 Top 3 (반려 건 기준)
    pub_rows = session.exec(
        select(Book.publisher, func.count(ReturnJob.id))
        .join(Book, ReturnJob.book_id == Book.id)
        .where(
            ReturnJob.status == JobStatusEnum.REJECTED,
            ReturnJob.created_at >= week_start,
            ReturnJob.created_at < week_end,
        )
        .group_by(Book.publisher)
        .order_by(func.count(ReturnJob.id).desc())
        .limit(3)
    ).all()
    top_publishers = {
        "items": [{"publisher": p or "미상", "reject_count": int(c)} for p, c in pub_rows]
    }

    # 3) 창고 Zone 점유 핫스팟 (중고 재고 기준 - 시점 스냅샷이라 주 경계와 무관)
    zone_rows = session.exec(
        select(Location.zone, func.count(InventoryUsedItem.id))
        .join(Location, InventoryUsedItem.location_id == Location.id)
        .group_by(Location.zone)
        .order_by(func.count(InventoryUsedItem.id).desc())
    ).all()
    location_hotspots = {"zones": [{"zone": z, "count": int(c)} for z, c in zone_rows]}

    # 4) 반품 예측 (최근 4주 반품 요청 단순 이동평균 - 결정론적)
    four_weeks_ago = week_end - timedelta(days=28)
    recent_returns = session.exec(
        select(func.count(Order.id)).where(
            Order.status == "RETURN_REQUESTED",
            Order.created_at >= four_weeks_ago,
            Order.created_at < week_end,
        )
    ).one() or 0
    predicted_returns = round(recent_returns / 4)

    # 5) 주간 물류 처리량 (입고/출고)
    week_inbound = session.exec(
        select(func.count(InventoryUsedItem.id)).where(
            InventoryUsedItem.created_at >= week_start,
            InventoryUsedItem.created_at < week_end,
        )
    ).one() or 0
    week_orders = session.exec(
        select(func.count(Order.id)).where(
            Order.created_at >= week_start,
            Order.created_at < week_end,
            Order.type != "AUTO_PO",
        )
    ).one() or 0
    logistics = {
        "week_inbound": int(week_inbound),
        "week_orders": int(week_orders),
        "week_inspections": int(week_inspections),
    }

    stats = {
        "report_week": report_week,
        "week_inspections": int(week_inspections),
        "saved_labor_cost_krw": saved_labor_cost,
        "top_defective_publishers": top_publishers["items"],
        "zone_hotspots": location_hotspots["zones"][:3],
        "predicted_returns_next_week": predicted_returns,
        "week_inbound": int(week_inbound),
        "week_orders": int(week_orders),
    }
    narrative = generate_insight_narrative(stats)

    if existing:
        existing.saved_labor_cost_krw = saved_labor_cost
        existing.top_defective_publishers = top_publishers
        existing.location_hotspots = location_hotspots
        existing.logistics_hotspots = logistics
        existing.predicted_returns = predicted_returns
        existing.ai_narrative = narrative
        insight = existing
    else:
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
    return insight, True


def generate_insight_narrative(stats: Dict[str, Any]) -> str:
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


def serialize_weekly_insight(w: WeeklyInsight, cached: bool) -> Dict[str, Any]:
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
