"""연구·분석 집계 로직 - MLOps 데이터셋 추출, FDS 리포트, HITL 재검수 목록.

본문은 router.py에서 무수정 이동했다 (2026-09-01 계층 정리).
"""

from typing import Any, Dict, List

from sqlalchemy import String, cast
from sqlmodel import Session, select

from app.models.wms import AdminAuditLog, ReturnJob


def export_mlops_dataset(session: Session) -> dict:
    """HITL 검증 완료 BBox를 YOLO/COCO 학습용 JSON으로 정제한다."""
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


def generate_fds_report(session: Session) -> dict:
    """관리자별 결재 행태 분석 - FDS R1과 동일 정의(윈도우·고속비율)로 판정한다."""
    from datetime import timedelta

    from app.domains.fds.service import (
        BLIND_APPROVAL_MIN_FAST_RATIO,
        BLIND_APPROVAL_MIN_SAMPLES,
        BLIND_APPROVAL_THRESHOLD_MS,
        BLIND_APPROVAL_WINDOW_DAYS,
    )
    from app.models.wms import now_kst

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


def list_hitl_reviewed_items(session: Session, only_recalled: bool) -> Dict[str, Any]:
    """관리자 결재 건을 LPN 단위로 집계한다 (LPN마다 마지막 조치만, 조치 횟수 별도)."""
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
