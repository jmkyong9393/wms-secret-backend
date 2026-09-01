"""
LPN 오연결 복구 — 검수이력이 '다른 도서'의 재고 LPN을 가리키는 건을 끊어낸다.

실행:  docker exec wms-secret-api python app/scripts/repair_lpn_collision.py

[원인]
`generate_lpn()`이 존 문자와 순번을 모두 난수로 뽑고 중복 검사를 하지 않았다.
`inventory_used_items.lpn_barcode`에는 UNIQUE 인덱스가 있어 재고끼리는 막혔지만,
검수이력의 LPN은 `return_jobs.agent_logs`(JSON) 안이라 제약이 걸리지 않는다.
그 결과 검수이력이 무관한 도서의 재고 행과 같은 LPN을 갖게 됐다.
LPN으로 이력↔재고를 조인하는 화면에서 엉뚱한 책의 검수 기록이 붙어 보인다.

채번부는 순차 방식으로 수정 완료(app/domains/inventory/service.py) - 신규 발생은 없다.
이 스크립트는 그 이전에 쌓인 데이터만 정리한다.

[복구 방침]
재고 쪽 LPN을 정답으로 둔다(UNIQUE 인덱스가 지켜준 값이고 실물 라벨과 일치).
잘못 물린 검수이력에만 그 날짜의 미사용 순번을 새로 발급해 오연결을 끊는다.
멱등하다 - 오연결이 없으면 아무것도 하지 않는다.
"""

import re
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text
from sqlmodel import Session

from app.db.session import engine


def main() -> None:
    with Session(engine) as db:
        bad = db.exec(
            text(
                "SELECT r.id, r.agent_logs->>'lpn_barcode' "
                "FROM return_jobs r JOIN inventory_used_items i "
                "  ON i.lpn_barcode = r.agent_logs->>'lpn_barcode' "
                "WHERE r.book_id <> i.book_id ORDER BY 2"
            )
        ).all()
        if not bad:
            print("오연결 없음 - 복구할 것이 없습니다.")
            return

        db.exec(
            text(
                "CREATE TABLE IF NOT EXISTS _bak_20260806_lpn_misllink AS "
                "SELECT r.id, r.agent_logs FROM return_jobs r JOIN inventory_used_items i "
                "  ON i.lpn_barcode = r.agent_logs->>'lpn_barcode' WHERE r.book_id <> i.book_id"
            )
        )
        db.commit()

        # 이미 쓰인 LPN 전체 (재고 + 이력) - 새 번호가 여기에 걸리면 안 된다
        used = {
            r[0]
            for r in db.exec(
                text(
                    "SELECT lpn_barcode FROM inventory_used_items "
                    "UNION SELECT agent_logs->>'lpn_barcode' FROM return_jobs "
                    "WHERE agent_logs->>'lpn_barcode' IS NOT NULL"
                )
            ).all()
            if r[0]
        }

        fixed = 0
        for job_id, old in bad:
            m = re.match(r"^LPN-(\d{6})-([A-Z])\d{3}$", old or "")
            if not m:
                print(f"  !! 형식 불명으로 건너뜀: {old}")
                continue
            date_str, letter = m.groups()
            seq = 1
            while f"LPN-{date_str}-{letter}{seq:03d}" in used:
                seq += 1
            new = f"LPN-{date_str}-{letter}{seq:03d}"
            used.add(new)

            db.exec(
                text(
                    "UPDATE return_jobs SET agent_logs = CAST(jsonb_set("
                    "CAST(agent_logs AS jsonb), '{lpn_barcode}', to_jsonb(CAST(:n AS text))"
                    ") AS json) WHERE id = :i"
                ),
                params={"n": new, "i": job_id},
            )
            print(f"  [job] {old} -> {new}")
            fixed += 1

        db.commit()

    print()
    print(f"오연결 {fixed}건 해소 (백업: _bak_20260806_lpn_misllink)")


if __name__ == "__main__":
    main()
