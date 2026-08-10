"""신품 재고 재시딩 — Inventory 행으로 심는다 (SSOT 준수).

[2026-08-11 수정] 종전에는 `books.virtual_stock`에만 수량을 써넣었다. 신품 재고의 단일
진실 공급원(SSOT)이 `Inventory` 행 합계(app/domains/inventory/service.get_new_stock_qty)로
일원화된 뒤에는, 이 방식으로 심은 재고는 **어디에서도 보이지 않는다** - 주문 화면·발주
스캔·피킹 할당이 전부 Inventory를 읽으므로 전 도서가 재고 0으로 취급된다.

실입고와 같은 관문(fasttrack_new_stock_inbound)을 그대로 통과시킨다. 그래야 위치 배정
(카테고리·판형 3차원 알고리즘)과 InventoryLog 원장까지 실제 입고와 동일하게 남는다.

사용법:
    python -m scripts.seed.reseed_script          # dry-run (기본)
    python -m scripts.seed.reseed_script --apply  # 실제 반영
"""
import sys
from pathlib import Path

# 레포 루트를 import 경로에 넣는다 (절대경로 하드코딩 제거 - 다른 PC/컨테이너에서도 동작).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlmodel import Session, select

from app.db.session import engine
from app.domains.inventory.service import fasttrack_new_stock_inbound, get_new_stock_qty
from app.models.wms import Book

APPLY = "--apply" in sys.argv
SEED_WORKER = "SEED"  # InventoryLog.worker_id에 남는 값 (사람 입고와 구분)


def main() -> None:
    with Session(engine) as db:
        books = db.exec(select(Book)).all()
        print(f"대상 도서: {len(books)}종 (모드: {'APPLY' if APPLY else 'DRY-RUN'})")

        touched = 0
        for idx, b in enumerate(books):
            current = get_new_stock_qty(db, b.id)
            target = (idx % 5) + 2  # 2~6권
            need = target - current
            if need <= 0:
                continue  # 이미 목표 이상 보유 - 중복 시딩하지 않는다(멱등)

            print(f"  + {b.title[:34]:36s} {current}권 -> {target}권")
            if APPLY:
                fasttrack_new_stock_inbound(db, b, need, worker_id=SEED_WORKER)
            touched += 1

        if APPLY:
            db.commit()
            print(f"완료: {touched}종 재고 보충 (Inventory 행 + InventoryLog 기록)")
        else:
            print(f"DRY-RUN: {touched}종이 대상입니다. 실제 반영하려면 --apply 를 붙이세요.")


if __name__ == "__main__":
    main()
