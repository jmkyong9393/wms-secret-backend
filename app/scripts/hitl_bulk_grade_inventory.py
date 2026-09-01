"""
HITL 승인 대기 합성테스트데이터 일괄 등급확정 - 재고 편입 (2026-08-08)

실행:  docker exec wms-secret-api python app/scripts/hitl_bulk_grade_inventory.py

[배경]
/admin/hitl/override API는 승인 시 건당 GPT-4o-mini로 보증서 본문을 생성한다
(app/domains/admin/router.py의 submit_hitl_override). 합성 시드 데이터 169건을
그 경로로 일괄 처리하려다 순차 LLM 호출 부하로 wms-secret-api 컨테이너가
OOM(exit 137)으로 죽었다. 조장 지시로 이 배치는 보증서 생성을 생략한다.

프리즈 규정 준수: LangGraph 4-Agent 파이프라인(app/ai/agents)은 건드리지 않는다.
이 스크립트는 admin 라우터의 APPROVE_DOWNGRADE 분기를 그대로 재현하되
build_certificate_document() 호출(LLM)만 제외한다. 랙 배정/재고 편입은 라우터와
동일하게 assign_rack_location_after_inspection()(LLM 미사용, 결정론적)을 그대로 쓴다.

제외 대상 3건(LPN-260806-A002, LPN-260804-A010, LPN-260804-A009)은 건드리지 않는다
- suggested_grade=MINT / reason_code=AWAITING_HUMAN_REVIEW로, "전건 오탐 제외 시
  무결점 등급 금지" 프리즈 규정(01-freeze-zones.md)이 적용되는 케이스라 사람 결재로 남겨둔다.

등급/사유 기본값은 프런트엔드(admin/hitl/page.tsx)의 초기값 로직과 동일하게 산정한다:
targetGrade = suggested_grade or "B", primaryReasonCode = reason_code
(단, 비어있거나 AWAITING_HUMAN_REVIEW면 "DMG_INT_DOODLE"로 폴백).
"""

import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime
from uuid import UUID

from sqlmodel import Session, select

from app.core.constants import format_worker_label
from app.db.session import engine
from app.models.wms import (
    AdminAuditLog,
    JobStatusEnum,
    ReturnJob,
    User,
    UserRoleEnum,
    clamp_ubci_score_to_grade,
)

EXCLUDE_LPNS = {"LPN-260806-A002", "LPN-260804-A010", "LPN-260804-A009"}


def main() -> None:
    with Session(engine) as db:
        admin = db.exec(select(User).where(User.employee_id == "WM2608001")).first()
        if not admin:
            admin = db.exec(
                select(User).where(
                    User.role.in_([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])
                )
            ).first()
        if not admin:
            print("관리자 계정을 찾지 못해 중단합니다.")
            return
        hitl_inspector = format_worker_label(admin.employee_id, admin.name)

        jobs = db.exec(
            select(ReturnJob).where(
                ReturnJob.status.in_(
                    [JobStatusEnum.HITL_REQUIRED, JobStatusEnum.PENDING]
                )
            )
        ).all()

        from app.domains.inventory.service import assign_rack_location_after_inspection

        processed = 0
        skipped_excluded = []
        for job in jobs:
            agent_logs = dict(job.agent_logs or {})
            lpn = agent_logs.get("lpn_barcode")
            if lpn in EXCLUDE_LPNS:
                skipped_excluded.append(lpn)
                continue
            if not lpn:
                print(f"  !! LPN 없는 job 건너뜀: {job.id}")
                continue

            previous_state = job.status
            target_grade = agent_logs.get("suggested_grade") or "B"
            reason_code = (
                agent_logs.get("reason_code")
                or agent_logs.get("primary_reason_code")
                or ""
            )
            if not reason_code or reason_code == "AWAITING_HUMAN_REVIEW":
                reason_code = "DMG_INT_DOODLE"

            job.status = JobStatusEnum.APPROVED
            job.ubci_score = clamp_ubci_score_to_grade(job.ubci_score, target_grade)

            cert_code = str(job.id)[:6].upper()
            cert_url = f"/certificate/CERT-20260728-{cert_code}"
            try:
                assign_rack_location_after_inspection(
                    db,
                    lpn_barcode=lpn,
                    final_grade=target_grade,
                    book_id=job.book_id,
                    ubci_score=job.ubci_score,
                    source_job_id=str(job.id),
                    certificate_url=cert_url,
                    inspection_source="HITL",
                    inspected_by=hitl_inspector,
                )
            except Exception as ex:
                print(f"  !! 랙 배정 실패 (job {job.id}, lpn {lpn}): {ex}")

            job.agent_logs = {
                **agent_logs,
                "admin_decision": "APPROVE_DOWNGRADE",
                "admin_comment": "관리자 검수 오버라이드 (합성데이터 일괄 처리, 보증서 생성 생략)",
                "primary_reason_code": reason_code,
                "target_grade": target_grade,
            }
            db.add(job)

            db.add(
                AdminAuditLog(
                    admin_id=admin.id,
                    target_type="RETURN_JOB",
                    target_id=str(job.id),
                    action="APPROVE_DOWNGRADE",
                    previous_state=previous_state,
                    new_state=job.status,
                    target_grade=target_grade,
                    primary_reason_code=reason_code,
                    defect_coordinates=agent_logs.get("defect_coordinates") or [],
                    review_duration_ms=0,
                )
            )
            processed += 1
            print(f"  [{processed}] {lpn} -> {target_grade} ({reason_code})")

        db.commit()

    print()
    print(
        f"처리 완료: {processed}건 승인/재고편입, 제외 {len(skipped_excluded)}건 ({sorted(skipped_excluded)})"
    )


if __name__ == "__main__":
    main()
