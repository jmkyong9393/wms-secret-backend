"""InventoryLog.worker_id에 저장된 표시용 라벨을 사번으로 정규화한다.

발주 승인 경로가 `_inspector_label()`이 만든 표시용 문자열(`WM2608001 (장문경)`)을
사번 컬럼에 그대로 저장한 이력이 있다. 이 값은 User 조회에 실패해 화면에 그 문자열이
통째로 노출되고, 작업자별 집계에서도 같은 사람이 둘로 갈린다.

사번 형태(`WM2608001`)만 남기고, 사번 패턴이 아닌 값(`SEED`, `신품 Fast-track (무검수 입고)` 등)은
건드리지 않는다.

사용법:
    python -m app.scripts.normalize_worker_id            # dry-run (기본)
    python -m app.scripts.normalize_worker_id --apply    # 실제 반영
"""
import re
import sys

from sqlmodel import Session, select

from app.db.session import engine
from app.models.wms import InventoryLog, User

APPLY = "--apply" in sys.argv

# `WM2608001 (장문경)` 처럼 사번 뒤에 괄호 이름이 붙은 값만 대상으로 삼는다.
LABEL_PATTERN = re.compile(r"^\s*([A-Za-z]{2}\d{6,})\s*\(\s*.+?\s*\)\s*$")


def main() -> None:
    with Session(engine) as db:
        valid_ids = {u.employee_id for u in db.exec(select(User)).all()}
        rows = db.exec(
            select(InventoryLog).where(InventoryLog.worker_id.is_not(None))
        ).all()

        targets = []
        for r in rows:
            m = LABEL_PATTERN.match(r.worker_id or "")
            if m and m.group(1) in valid_ids:
                targets.append((r, m.group(1)))

        print(f"worker_id 보유 행 {len(rows)}건 / 정규화 대상 {len(targets)}건 "
              f"(모드: {'APPLY' if APPLY else 'DRY-RUN'})")
        for r, emp in targets:
            print(f"  {r.worker_id!r} -> {emp!r}  (log_id={r.id})")
            if APPLY:
                r.worker_id = emp
                db.add(r)

        if not targets:
            print("정규화할 행이 없습니다.")
            return

        if APPLY:
            db.commit()
            print(f"완료: {len(targets)}건 정규화")
        else:
            print("실제 반영하려면 --apply 를 붙이세요.")


if __name__ == "__main__":
    main()
