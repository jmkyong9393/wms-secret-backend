from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from typing import List, Dict, Any
from sqlalchemy import func, cast, String
from app.db.session import get_db
from app.models.wms import AdminAuditLog, UserRoleEnum, ReturnJob
from app.core.security import RoleChecker

router = APIRouter(prefix="/research", tags=["Research & Analytics"])

# 연구용 데이터 추출은 MASTER 권한을 요구 (보안 강화)
master_only = RoleChecker([UserRoleEnum.MASTER])

@router.get("/export-dataset")
def export_mlops_dataset(
    session: Session = Depends(get_db),
    current_admin = Depends(master_only)
):
    """
    MLOps용 BBox 좌표 데이터셋 추출기 (SCI 논문용)
    HITL 대시보드에서 관리자가 검증 완료(Approved)한 좌표(defectCoordinates)를
    AI 학습(YOLO/COCO)을 위해 정제하여 JSON으로 반환합니다.
    """
    # [2026-08-08 수정] .cast(str)은 Python str 타입 객체를 SQLAlchemy 타입 자리에 넘겨
    # AttributeError('str' object has no attribute '_isnull')로 항상 500을 냈다 - 이 엔드포인트가
    # 한 번도 정상 동작한 적이 없었다는 뜻이다. target_id(str 컬럼)와 ReturnJob.id(UUID 컬럼)를
    # 비교하려면 SQLAlchemy cast() 함수로 UUID를 String으로 명시 변환해야 한다.
    statement = select(AdminAuditLog, ReturnJob).join(
        ReturnJob, AdminAuditLog.target_id == cast(ReturnJob.id, String)
    ).where(AdminAuditLog.defect_coordinates != None)
    
    results = session.exec(statement).all()
    
    dataset = []
    for audit, job in results:
        # BBox가 유효한 경우만 추출
        if audit.defect_coordinates and len(audit.defect_coordinates) > 0:
            # [2026-08-08] 관리자가 오탐으로 제외(hitl_excluded)한 BBox는 재학습 데이터로
            # 내보내지 않는다 - 사람이 "결함 아님"이라고 확인한 판독을 정답 라벨로 흘리면
            # 재학습이 그 오탐을 오히려 강화한다. hitl_adopted/hitl_bbox_edited는 사람이
            # 확정/보정한 값이라 그대로 포함(오히려 원본 AI 판독보다 신뢰도 높은 라벨).
            filtered_groups = []
            for group in audit.defect_coordinates:
                if not isinstance(group, dict):
                    continue
                boxes = [
                    b for b in (group.get("bboxes") or [])
                    if isinstance(b, dict) and not b.get("hitl_excluded")
                ]
                if boxes:
                    filtered_groups.append({**group, "bboxes": boxes})
            if not filtered_groups:
                continue
            dataset.append({
                "image_url": job.image_urls[0] if job.image_urls else None,
                "target_grade": audit.target_grade,
                "primary_reason": audit.primary_reason_code,
                "bboxes": filtered_groups,  # 이미지별 {image_index, image_url, bboxes:[{xmin,ymin,xmax,ymax,...}]}
                "verified_by": str(audit.admin_id),
                "verified_at": audit.created_at.isoformat()
            })
            
    return {
        "status": "success",
        "total_records": len(dataset),
        "dataset": dataset
    }

@router.get("/fds-report")
def generate_fds_report(
    session: Session = Depends(get_db),
    current_admin = Depends(master_only)
):
    """
    작업자 신뢰성 및 모럴 해저드 방어를 위한 FDS (Fraud Detection System) 레포트
    각 관리자(admin_id)별 평균 reviewDurationMs를 분석하여,
    비정상적으로 빠르게 승인하는(Abuse) 패턴을 감지합니다.
    """
    # 1. 관리자별 통계 집계
    statement = select(
        AdminAuditLog.admin_id,
        func.count(AdminAuditLog.id).label("total_reviews"),
        func.avg(AdminAuditLog.review_duration_ms).label("avg_duration_ms"),
        func.min(AdminAuditLog.review_duration_ms).label("min_duration_ms"),
        func.max(AdminAuditLog.review_duration_ms).label("max_duration_ms")
    ).where(
        AdminAuditLog.review_duration_ms != None
    ).group_by(AdminAuditLog.admin_id)
    
    stats = session.exec(statement).all()
    
    report = []
    SUSPICIOUS_THRESHOLD_MS = 1000 # 1초 미만의 평균 검수 시간은 비정상으로 간주
    
    for row in stats:
        admin_id, total, avg_ms, min_ms, max_ms = row
        is_suspicious = avg_ms < SUSPICIOUS_THRESHOLD_MS
        
        report.append({
            "admin_id": str(admin_id),
            "total_reviews": total,
            "avg_duration_ms": round(avg_ms, 2) if avg_ms else 0,
            "min_duration_ms": min_ms,
            "max_duration_ms": max_ms,
            "is_suspicious": is_suspicious,
            "alert_message": "Warning: Average review time is below 1 second. High risk of blind approval." if is_suspicious else "Normal"
        })
        
    return {
        "status": "success",
        "threshold_ms": SUSPICIOUS_THRESHOLD_MS,
        "worker_stats": report
    }
