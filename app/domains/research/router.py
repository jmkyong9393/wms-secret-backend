from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from typing import List, Dict, Any
from sqlalchemy import func, cast, String
from app.db.session import get_db
from app.models.wms import AdminAuditLog, UserRoleEnum, ReturnJob
from app.core.security import RoleChecker

router = APIRouter(prefix="/research", tags=["Research & Analytics"])

# 데이터셋 export는 다른 관리자 도메인과 동일하게 MASTER/ADMIN 공통 권한.
admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])
# FDS 리포트는 관리자별 검수 속도(blind approval 의심)를 분석하는 자기감시성 리포트라
# ADMIN이 자신의 이상 승인 패턴을 스스로 조회할 수 있게 되는 걸 막기 위해 MASTER 전용 유지.
master_only = RoleChecker([UserRoleEnum.MASTER])


@router.get("/export-dataset")
def export_mlops_dataset(
    session: Session = Depends(get_db), current_admin=Depends(admin_only)
):
    """
    MLOps용 BBox 좌표 데이터셋 추출기 (SCI 논문용)
    HITL 대시보드에서 관리자가 검증 완료(Approved)한 좌표(defectCoordinates)를
    AI 학습(YOLO/COCO)을 위해 정제하여 JSON으로 반환합니다.
    """
    # .cast(str)은 Python str 타입 객체를 SQLAlchemy 타입 자리에 넘겨
    # AttributeError('str' object has no attribute '_isnull')로 항상 500을 냈다 - 이 엔드포인트가
    # 한 번도 정상 동작한 적이 없었다는 뜻이다. target_id(str 컬럼)와 ReturnJob.id(UUID 컬럼)를
    # 비교하려면 SQLAlchemy cast() 함수로 UUID를 String으로 명시 변환해야 한다.
    statement = (
        select(AdminAuditLog, ReturnJob)
        .join(ReturnJob, AdminAuditLog.target_id == cast(ReturnJob.id, String))
        .where(AdminAuditLog.defect_coordinates != None)
    )

    results = session.exec(statement).all()

    dataset = []
    for audit, job in results:
        # BBox가 유효한 경우만 추출
        if audit.defect_coordinates and len(audit.defect_coordinates) > 0:
            # 관리자가 오탐으로 제외(hitl_excluded)한 BBox는 재학습 데이터로
            # 내보내지 않는다 - 사람이 "결함 아님"이라고 확인한 판독을 정답 라벨로 흘리면
            # 재학습이 그 오탐을 오히려 강화한다. hitl_adopted/hitl_bbox_edited는 사람이
            # 확정/보정한 값이라 그대로 포함(오히려 원본 AI 판독보다 신뢰도 높은 라벨).
            filtered_groups = []
            for group in audit.defect_coordinates:
                if not isinstance(group, dict):
                    continue
                boxes = [
                    b
                    for b in (group.get("bboxes") or [])
                    if isinstance(b, dict) and not b.get("hitl_excluded")
                ]
                if boxes:
                    filtered_groups.append({**group, "bboxes": boxes})
            if not filtered_groups:
                continue
            dataset.append(
                {
                    "image_url": job.image_urls[0] if job.image_urls else None,
                    "target_grade": audit.target_grade,
                    "primary_reason": audit.primary_reason_code,
                    "bboxes": filtered_groups,  # 이미지별 {image_index, image_url, bboxes:[{xmin,ymin,xmax,ymax,...}]}
                    "verified_by": str(audit.admin_id),
                    "verified_at": audit.created_at.isoformat(),
                }
            )

    return {"status": "success", "total_records": len(dataset), "dataset": dataset}


@router.get("/fds-report")
def generate_fds_report(
    session: Session = Depends(get_db), current_admin=Depends(master_only)
):
    """
    작업자 신뢰성 및 모럴 해저드 방어를 위한 FDS 리포트 (관리자별 결재 행태 분석).

    종전에는 **전 기간 누적 평균 < 1초**로 판정했다. FDS 룰 엔진
    R1과 같은 결함이 있었다: ① 과거 신중한 결재가 현재의 블라인드 결재를 영구히 희석하고,
    ② 평균은 이상치 한 건에 무너진다(200초 결재 1건이 0.5초 결재 12건을 덮는다).
    판정 기준을 `app/domains/fds/service.py`의 R1과 **동일한 정의**로 통일한다 —
    같은 현상을 두 화면이 다르게 판정하면 어느 쪽도 신뢰할 수 없기 때문이다.
    """
    from app.domains.fds.service import (
        BLIND_APPROVAL_MIN_FAST_RATIO,
        BLIND_APPROVAL_MIN_SAMPLES,
        BLIND_APPROVAL_THRESHOLD_MS,
        BLIND_APPROVAL_WINDOW_DAYS,
    )
    from app.models.wms import now_kst
    from datetime import timedelta

    since = now_kst() - timedelta(days=BLIND_APPROVAL_WINDOW_DAYS)
    rows = session.exec(
        select(AdminAuditLog).where(
            AdminAuditLog.review_duration_ms != None,
            AdminAuditLog.created_at >= since,
        )
    ).all()

    per_admin: Dict[Any, List[int]] = {}
    for log in rows:
        per_admin.setdefault(log.admin_id, []).append(int(log.review_duration_ms))

    report = []
    for admin_id, durations in per_admin.items():
        total = len(durations)
        fast = [d for d in durations if d < BLIND_APPROVAL_THRESHOLD_MS]
        fast_ratio = len(fast) / total
        # 표본이 적으면 판정하지 않는다 (우연히 빠른 1~2건으로 사람을 지목하지 않음)
        is_suspicious = (
            total >= BLIND_APPROVAL_MIN_SAMPLES
            and fast_ratio >= BLIND_APPROVAL_MIN_FAST_RATIO
        )

        report.append(
            {
                "admin_id": str(admin_id),
                "total_reviews": total,
                "fast_reviews": len(fast),
                "fast_ratio_pct": round(fast_ratio * 100, 1),
                "median_duration_ms": sorted(durations)[total // 2],
                "min_duration_ms": min(durations),
                "max_duration_ms": max(durations),
                "is_suspicious": is_suspicious,
                "alert_message": (
                    f"Warning: {len(fast)}/{total} reviews under "
                    f"{BLIND_APPROVAL_THRESHOLD_MS}ms. High risk of blind approval."
                    if is_suspicious
                    else "Normal"
                ),
            }
        )

    return {
        "status": "success",
        "window_days": BLIND_APPROVAL_WINDOW_DAYS,
        "threshold_ms": BLIND_APPROVAL_THRESHOLD_MS,
        "required_ratio_pct": round(BLIND_APPROVAL_MIN_FAST_RATIO * 100),
        "min_samples": BLIND_APPROVAL_MIN_SAMPLES,
        "worker_stats": report,
    }


@router.get(
    "/hitl-recheck-list", summary="HITL 결재를 거친 도서 LPN 목록 (재검수 전수조사용)"
)
def list_hitl_reviewed_items(
    only_recalled: bool = False,
    session: Session = Depends(get_db),
    current_admin=Depends(admin_only),
) -> Dict[str, Any]:
    """
    관리자가 결재한 검수 건을 **LPN 단위**로 집계한다.

    같은 도서를 여러 번 재검수하면 감사 로그가 여러 줄 쌓인다. 건별로 세면 한 권이
    여러 번 계산되므로 **LPN마다 마지막 조치만** 남기고, 조치 횟수는 별도 열로 센다.

    only_recalled=true면 관리자가 재고 화면에서 직접 되불러온 건(ADMIN_RECALL)만 추린다.
    """
    from app.models.wms import InventoryUsedItem

    rows = session.exec(
        select(AdminAuditLog, ReturnJob)
        .join(ReturnJob, AdminAuditLog.target_id == cast(ReturnJob.id, String))
        .where(AdminAuditLog.target_type == "RETURN_JOB")
        .order_by(AdminAuditLog.created_at.asc())
    ).all()

    by_lpn: Dict[str, Dict[str, Any]] = {}
    for audit, job in rows:
        logs = job.agent_logs or {}
        lpn = logs.get("lpn_barcode")
        if not lpn:
            continue
        rec = by_lpn.setdefault(
            lpn,
            {
                "lpn": lpn,
                "job_id": str(job.id),
                "actions": 0,
                "recalled": False,
                "bbox_edited": False,
            },
        )
        rec["actions"] += 1
        rec["last_action"] = audit.action
        rec["last_reason"] = audit.primary_reason_code
        rec["last_grade"] = audit.target_grade
        rec["last_at"] = (
            audit.created_at.strftime("%Y-%m-%d %H:%M") if audit.created_at else None
        )
        if (
            audit.action == "RECALL_TO_HITL"
            or audit.primary_reason_code == "ADMIN_RECALL"
        ):
            rec["recalled"] = True
        if audit.defect_coordinates:
            rec["bbox_edited"] = True
            # 관리자가 실제로 그린 박스 개수 (이미지별 bboxes 합)
            n = 0
            for g in audit.defect_coordinates:
                if isinstance(g, dict):
                    n += len(g.get("bboxes") or [])
            rec["bbox_count"] = max(int(rec.get("bbox_count", 0)), n)

    items = list(by_lpn.values())
    if only_recalled:
        items = [x for x in items if x["recalled"]]

    # 재고 현황을 붙여 준다 - 이미 출고된 건은 재검수 대상이 아니다.
    lpns = [x["lpn"] for x in items]
    inv = {}
    if lpns:
        for it in session.exec(
            select(InventoryUsedItem).where(InventoryUsedItem.lpn_barcode.in_(lpns))
        ).all():
            inv[it.lpn_barcode] = it
    for x in items:
        it = inv.get(x["lpn"])
        x["item_status"] = getattr(it, "item_status", None) if it else None
        x["confirmed_grade"] = getattr(it, "condition_grade", None) if it else None
        x["ubci_score"] = getattr(it, "ubci_score", None) if it else None
        x["title"] = None
        if it and getattr(it, "book_id", None):
            from app.models.wms import Book

            b = session.get(Book, it.book_id)
            x["title"] = b.title if b else None

    items.sort(key=lambda x: (not x["recalled"], x["lpn"]))
    return {
        "total": len(items),
        "recalled_count": sum(1 for x in items if x["recalled"]),
        "bbox_edited_count": sum(1 for x in items if x["bbox_edited"]),
        "items": items,
    }
