"""
유령 LPN 정리 — 채번만 되고 검수로 이어지지 않은 inventory_used_items 행을 삭제한다.

삭제 대상은 아래를 모두 만족하는 행이다.
  - item_status == PENDING_INSPECTION   (검수·입고가 진행된 적 없음)
  - source_job_id is None               (검수 원장에 연결된 적 없음)
  - 해당 LPN으로 접수된 ReturnJob 없음  (촬영본이 큐에 들어가 있지 않음)
  - created_at 이 유예 시간보다 오래됨   (지금 진행 중인 작업 보호)

기본은 dry-run이다. 실제 삭제는 --apply, 삭제 전 JSON 백업은 --backup 으로 지정한다.

  python -m app.scripts.purge_ghost_lpn                                  # 미리보기
  python -m app.scripts.purge_ghost_lpn --backup /tmp/ghost_lpn.json --apply
"""

import argparse
import json
import sys
from datetime import timedelta

from sqlmodel import Session, select

from app.db.session import engine
from app.models.wms import InventoryUsedItem, ReturnJob, now_kst

DEFAULT_GRACE_MINUTES = 60


def collect_ghosts(db: Session, grace_minutes: int) -> list[InventoryUsedItem]:
    cutoff = now_kst() - timedelta(minutes=grace_minutes)

    candidates = db.exec(
        select(InventoryUsedItem).where(
            InventoryUsedItem.item_status == "PENDING_INSPECTION",
            InventoryUsedItem.source_job_id.is_(None),
            InventoryUsedItem.created_at < cutoff,
        )
    ).all()
    if not candidates:
        return []

    # 촬영본이 검수 큐에 접수된 LPN은 제외한다. ReturnJob은 LPN을 agent_logs(JSONB)에 담는다.
    lpns = [item.lpn_barcode for item in candidates]
    queued = db.exec(
        select(ReturnJob.agent_logs["lpn_barcode"].astext).where(
            ReturnJob.agent_logs["lpn_barcode"].astext.in_(lpns)
        )
    ).all()
    queued_set = {row for row in queued if row}

    return [item for item in candidates if item.lpn_barcode not in queued_set]


def main() -> int:
    parser = argparse.ArgumentParser(description="유령 LPN 정리")
    parser.add_argument(
        "--apply", action="store_true", help="실제로 삭제한다 (미지정 시 미리보기)"
    )
    parser.add_argument(
        "--backup", metavar="PATH", help="삭제 대상을 JSON으로 저장할 경로"
    )
    parser.add_argument(
        "--grace-minutes",
        type=int,
        default=DEFAULT_GRACE_MINUTES,
        help=f"이 시간보다 최근에 채번된 건은 건드리지 않는다 (기본 {DEFAULT_GRACE_MINUTES}분)",
    )
    args = parser.parse_args()

    with Session(engine) as db:
        ghosts = collect_ghosts(db, args.grace_minutes)

        total = db.exec(select(InventoryUsedItem)).all()
        pending = [i for i in total if i.item_status == "PENDING_INSPECTION"]
        print(
            f"전체 LPN {len(total)}건 / PENDING_INSPECTION {len(pending)}건 / 삭제 대상 {len(ghosts)}건"
        )

        if not ghosts:
            print("삭제 대상이 없습니다.")
            return 0

        payload = [
            {
                "id": str(g.id),
                "lpn_barcode": g.lpn_barcode,
                "book_id": str(g.book_id),
                "location_id": str(g.location_id),
                "item_status": g.item_status,
                "condition_grade": g.condition_grade,
                "created_at": g.created_at.isoformat() if g.created_at else None,
            }
            for g in ghosts
        ]

        for row in payload:
            print(f"  {row['lpn_barcode']}  created={row['created_at']}")

        if args.backup:
            with open(args.backup, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"백업 저장: {args.backup}")

        if not args.apply:
            print("\n[dry-run] 실제 삭제하려면 --apply 를 붙여 다시 실행하세요.")
            return 0

        if not args.backup:
            print("\n[중단] 삭제 전 --backup 경로를 반드시 지정하세요.")
            return 1

        for g in ghosts:
            db.delete(g)
        db.commit()
        print(f"\n삭제 완료: {len(ghosts)}건")

    return 0


if __name__ == "__main__":
    sys.exit(main())
