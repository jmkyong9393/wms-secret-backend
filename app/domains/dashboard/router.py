from fastapi import APIRouter, Depends, Query
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy import or_, cast, String
from sqlmodel import Session, select, func
from app.db.session import get_db
from app.models.wms import (
    ReturnJob, InventoryUsedItem, Inventory, Order, Book, JobStatusEnum, ubci_grade_from_score,
    AdminAuditLog,
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

    # 6. 관리자 결재를 거친 건을 분리한다.
    #    status만 보면 관리자가 소환해 손으로 승인한 건도 APPROVED에 섞여 "자동 승인율"로
    #    잡힌다. 사람이 개입한 건은 정의상 자동이 아니므로 감사 로그 유무로 갈라낸다.
    def _human_touched(statuses: List[str]) -> int:
        return session.exec(
            select(func.count(func.distinct(AdminAuditLog.target_id)))
            .where(AdminAuditLog.target_type == "RETURN_JOB")
            .where(AdminAuditLog.target_id.in_(
                select(cast(ReturnJob.id, String)).where(ReturnJob.status.in_(statuses))
            ))
        ).one() or 0

    decided_statuses = [
        JobStatusEnum.APPROVED.value,
        JobStatusEnum.REJECTED.value,
        JobStatusEnum.HITL_REQUIRED.value,
    ]
    # 자동 승인율에서 빼는 것은 "승인된 건 중 사람이 손댄 것"뿐이다. 반려·대기 건을 같이
    # 빼면 분자가 과소 집계된다.
    human_approved = _human_touched([JobStatusEnum.APPROVED.value])
    human_reviewed = _human_touched(decided_statuses)
    auto_approved = max(approved - human_approved, 0)

    # 7. 심사 대기를 누가 올렸는지로 쪼갠다. 관리자가 재고에서 소환한 건과 AI가 스스로
    #    올린 건은 성격이 다르다 - 전자는 사람이 이미 의심한 건이다.
    pending_by_admin = _human_touched([JobStatusEnum.HITL_REQUIRED.value])
    pending_by_ai = max(pending_issues - pending_by_admin, 0)

    return {
        "today_inbound": today_inbound,
        "today_outbound": today_outbound,
        "today_inspection": today_inspection,
        "pending_issues": pending_issues,
        # approval_rate는 사람 개입분을 포함한 "최종" 승인율이다. AI 단독 성과가 아니다.
        "approval_rate": round(approved / decided * 100, 1) if decided else 0.0,
        "auto_approval_rate": round(auto_approved / decided * 100, 1) if decided else 0.0,
        "rejection_rate": round(rejected / decided * 100, 1) if decided else 0.0,
        "hitl_rate": round(pending_issues / decided * 100, 1) if decided else 0.0,
        "decided_total": decided,
        "human_reviewed": human_reviewed,
        "human_approved": human_approved,
        "auto_approved": auto_approved,
        "pending_by_ai": pending_by_ai,
        "pending_by_admin": pending_by_admin,
    }


# 검수 접수 전 상태. LPN 라벨만 발급되고 아직 검수를 받지 않아 어느 경로로도 분류할 수 없다.
_NOT_YET_INSPECTED = "PENDING_INSPECTION"


@router.get("/inspection-breakdown")
def get_inspection_breakdown(session: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    검수 경로를 **책 1권 단위 최종 상태**로 집계해 합이 100%가 되게 반환한다.
    분모는 **통합 재고**다 - 중고 LPN 1행 = 1권, 신품은 묶음 재고 수량(권)을 더한다.

    건별로 세지 않는 이유: 같은 책을 여러 번 재검수하면 return_jobs 행이 늘어 한 권이
    여러 번 계산된다. 재검수를 몇 번 했든 그 책의 최종 귀속은 하나여야 한다.

    분류는 상호배타이며 우선순위가 있다 (관리자 > AI 이관 > 자동):
      D. 신품 Fast-Track - UBCI 검수 자체를 타지 않는다 (LPN 미발급, 수량 관리)
      C. 관리자 판단     - 관리자가 결재한 이력이 있다 (소환·승인·반려 무엇이든)
      B. AI 이관         - 관리자 개입 없이 AI가 사람에게 넘겼다
      A. AI 자동검수     - 위 둘 다 아니다

    **한계**: B는 2026-08-16 이전 건을 소급하지 못한다. 그전에는 이관 사유(reason_code)가
    매 실행 덮어써져, AI가 올렸다가 재검수로 자동 확정된 건에 흔적이 남지 않았다.
    그런 건은 A로 잡힌다 (agent_logs.escalations 적재는 그날부터 시작).
    """
    items = session.exec(
        select(InventoryUsedItem).where(
            or_(
                InventoryUsedItem.item_status.is_(None),
                InventoryUsedItem.item_status != _NOT_YET_INSPECTED,
            )
        )
    ).all()
    excluded = session.exec(
        select(func.count(InventoryUsedItem.id))
        .where(InventoryUsedItem.item_status == _NOT_YET_INSPECTED)
    ).one() or 0

    # 대조표를 한 번에 읽어 LPN마다 쿼리를 날리지 않는다 (재고 수백 건 규모).
    admin_targets = {
        row for row in session.exec(
            select(AdminAuditLog.target_id).where(AdminAuditLog.target_type == "RETURN_JOB")
        ).all()
    }
    job_rows = session.exec(select(ReturnJob.id, ReturnJob.status, ReturnJob.agent_logs)).all()
    job_status = {str(r[0]): r[1] for r in job_rows}
    job_escalated = {
        str(r[0]) for r in job_rows
        if isinstance((r[2] or {}).get("escalations"), list) and (r[2] or {}).get("escalations")
    }

    # 신품은 LPN이 없는 묶음 재고라 수량으로 센다. 단위가 '권'으로 같아 중고와 합산된다.
    new_stock = session.exec(select(func.coalesce(func.sum(Inventory.quantity), 0))).one() or 0

    counts = {"AI_AUTO": 0, "HITL_AI": 0, "HITL_ADMIN": 0, "NEW_FASTTRACK": int(new_stock)}
    for it in items:
        job_id = str(it.source_job_id) if it.source_job_id else None
        if job_id and job_id in admin_targets:
            counts["HITL_ADMIN"] += 1
        elif (
            (job_id and job_id in job_escalated)
            or it.inspection_source == "PENDING_HITL"
            or it.item_status == "HITL_PENDING"
            or (job_id and job_status.get(job_id) == JobStatusEnum.HITL_REQUIRED.value)
        ):
            counts["HITL_AI"] += 1
        else:
            counts["AI_AUTO"] += 1

    used_total = len(items)
    total = used_total + counts["NEW_FASTTRACK"]
    labels = {
        "AI_AUTO": "AI 자동검수",
        "HITL_AI": "HITL 이관 (AI 판단)",
        "HITL_ADMIN": "HITL 이관 (관리자 판단)",
        "NEW_FASTTRACK": "신품 Fast-Track (무검수)",
    }
    order = ("AI_AUTO", "HITL_AI", "HITL_ADMIN", "NEW_FASTTRACK")
    return {
        "total": total,
        "used_total": used_total,
        "new_total": counts["NEW_FASTTRACK"],
        "excluded_not_inspected": excluded,
        "buckets": [
            {
                "key": k,
                "label": labels[k],
                "count": counts[k],
                "pct": round(counts[k] / total * 100, 1) if total else 0.0,
                # 신품을 뺀 "검수를 실제로 받은 물량" 안에서의 비중. 검수 품질을 볼 때 쓴다.
                "pct_inspected": (
                    round(counts[k] / used_total * 100, 1)
                    if used_total and k != "NEW_FASTTRACK" else None
                ),
            }
            for k in order
        ],
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
    #
    # [수정 이력] value는 파이 조각 각도 계산용 실개수(건)다. 프론트 범례가 이 값에 그대로
    # "%"를 붙여 렌더해 617%/965%/532% 같은 값이 나왔다. 문구를 호출부(프론트)가 지어내면
    # 같은 사건이 화면마다 다르게 표시된다는 이 코드베이스의 원칙대로, 퍼센트는 총합 대비
    # 비율을 여기서 확정해 pct로 함께 내려준다 (value는 그대로 유지 - Pie 조각 크기용).
    grade_total = sum(grade_counts.values())
    def _pct(n: int) -> float:
        return round(n / grade_total * 100, 1) if grade_total else 0.0

    ubci_grade_data = [
        {"name": "MINT (95~100점)", "value": grade_counts["MINT"], "pct": _pct(grade_counts["MINT"]), "color": "#10b981"},
        {"name": "GOOD (85~94점)", "value": grade_counts["GOOD"], "pct": _pct(grade_counts["GOOD"]), "color": "#3b82f6"},
        {"name": "NORMAL (65~84점)", "value": grade_counts["NORMAL"], "pct": _pct(grade_counts["NORMAL"]), "color": "#f59e0b"},
        {"name": "REJECT (65점 미만)", "value": grade_counts["REJECT"], "pct": _pct(grade_counts["REJECT"]), "color": "#ef4444"},
    ]
    # 파이 중앙 라벨(MINT+GOOD 합산 비율)도 프론트가 "86%"로 하드코딩해두고 있었다 - 실데이터와
    # 무관하게 항상 같은 숫자가 떠 있었다. 여기서 실계산값을 함께 내려 하드코딩을 대체한다.
    mint_good_pct = _pct(grade_counts["MINT"] + grade_counts["GOOD"])

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
        "ubci_mint_good_pct": mint_good_pct,
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
    주간 인사이트 스냅샷 조회 (weekly_insights 테이블).

    [2026-08-12 리팩토링] 집계 로직은 weekly_insight_service로 분리했고, 정규 생성 주체는
    Celery Beat(매일 00:05 KST)이다. 여기서는 조회만 하되, 크론이 아직 안 돌았거나 실패한
    경우를 대비해 같은 함수로 폴백 생성한다(자기치유). 집계 창은 ISO 주 경계로 고정되므로
    누가 언제 방문하든 같은 주차는 같은 값을 낸다.
    """
    from app.domains.dashboard.weekly_insight_service import (
        build_weekly_insight, iso_week_bounds, serialize_weekly_insight,
    )
    from app.models.wms import WeeklyInsight

    report_week, _, _ = iso_week_bounds(now_kst())
    existing = session.exec(
        select(WeeklyInsight).where(WeeklyInsight.report_week == report_week)
    ).first()
    if existing:
        return serialize_weekly_insight(existing, cached=True)

    insight, created = build_weekly_insight(session)
    return serialize_weekly_insight(insight, cached=not created)


@router.get("/weekly-insights/history")
def get_weekly_insights_history(
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    지난 주간 인사이트 이력 (2026-08-09 신설).

    weekly_insights는 지연 물질화라 실제로 방문된 주만 행이 쌓인다 - ISO 주 1건뿐이라
    1년에 52행 수준이라 용량 문제는 아니다. 다만 기존에는 이번 주 1건만 조회하는
    엔드포인트뿐이라 과거 주를 볼 방법이 없었다. HITL 대기열처럼 전체를 한 번에 fetch하는
    패턴을 반복하지 않도록, 여기서는 처음부터 limit/offset 배치 페이지네이션을 강제한다
    (프론트는 "더보기" 버튼으로 다음 배치를 이어붙인다).
    """
    from app.models.wms import WeeklyInsight

    total = session.exec(select(func.count(WeeklyInsight.id))).one()
    rows = session.exec(
        select(WeeklyInsight)
        .order_by(WeeklyInsight.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    return {
        "items": [_serialize_weekly_insight(w, cached=True) for w in rows],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
    }


# 직렬화/서사 생성은 weekly_insight_service가 단일 정의를 갖는다 (크론과 공유).
from app.domains.dashboard.weekly_insight_service import (  # noqa: E402
    serialize_weekly_insight as _serialize_weekly_insight,
)


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
