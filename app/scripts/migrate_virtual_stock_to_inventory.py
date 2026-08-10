"""books.virtual_stock 잔량을 Inventory 행으로 이관한다 (신품 재고 SSOT 일원화).

시드 데이터가 위치 없이 virtual_stock에만 수량을 심어둔 탓에, 신품 재고가
Inventory(위치 보유)와 virtual_stock(위치 없음) 두 곳에 나뉘어 있었다. 읽는 쪽마다
공식이 달라 발주 에이전트는 virtual_stock만, 주문 검증은 max(둘), 출고 목록은
Inventory 우선 폴백을 썼다. 이관 후에는 Inventory 합계 하나만 본다.

배치 규칙은 Fast-Track 입고와 동일한 3D 알고리즘(recommend_optimal_warehouse_zone)을
그대로 태워, 이관된 재고도 실제 입고분과 같은 칸에 놓이게 한다.

    python -m app.scripts.migrate_virtual_stock_to_inventory           # dry-run (기본)
    python -m app.scripts.migrate_virtual_stock_to_inventory --apply   # 실제 반영
"""

import sys

from sqlmodel import Session, select

from app.db.session import engine
from app.models.wms import Book, Inventory, InventoryLog, now_kst
from app.domains.inventory.service import (
    get_or_create_location,
    recommend_optimal_warehouse_zone,
)


def migrate(apply: bool = False) -> int:
    moved_books = 0
    moved_qty = 0

    with Session(engine) as db:
        books = db.exec(select(Book).where(Book.virtual_stock > 0)).all()
        print(f"대상 도서: {len(books)}종 / 총 {sum(b.virtual_stock or 0 for b in books)}권")
        print("-" * 78)

        for b in books:
            qty = int(b.virtual_stock or 0)
            if qty <= 0:
                continue

            rec_zone, rec_rack, rec_shelf = recommend_optimal_warehouse_zone(
                grade="NEW",
                category=b.category_type or "IT/컴퓨터",
                base_price=b.base_price or 20000.0,
                standard_size=b.standard_size,
            )
            loc_label = f"{rec_zone}-{rec_rack}-{rec_shelf}"

            # 이미 Inventory 행이 있으면 그 행에 더한다. 새 행을 만들면 같은 도서가
            # 같은 칸에 두 행으로 갈라져 합계는 맞아도 원장이 지저분해진다.
            existing_qty = sum(
                int(r.quantity or 0)
                for r in db.exec(select(Inventory).where(Inventory.book_id == b.id)).all()
            )
            print(f"  {b.title[:38]:40s} virtual={qty:3d} → {loc_label:8s} (기존 Inventory={existing_qty})")

            if not apply:
                moved_books += 1
                moved_qty += qty
                continue

            location = get_or_create_location(db, zone=rec_zone, rack=rec_rack, shelf=rec_shelf)
            inv = db.exec(
                select(Inventory).where(
                    Inventory.book_id == b.id, Inventory.location_id == location.id
                )
            ).first()
            if inv:
                inv.quantity = (inv.quantity or 0) + qty
                inv.updated_at = now_kst()
            else:
                inv = Inventory(book_id=b.id, location_id=location.id, quantity=qty)
            db.add(inv)

            # 이관은 수량 이동이지 신규 입고가 아니다. 원장에 남기되 사유를 구분한다.
            db.add(InventoryLog(
                transaction_type="INBOUND",
                book_id=b.id,
                condition_grade="NEW",
                quantity_change=qty,
                picked_location=loc_label,
                worker_id="MIGRATION_VSTOCK",
            ))

            b.virtual_stock = 0
            b.updated_at = now_kst()
            db.add(b)

            moved_books += 1
            moved_qty += qty

        if apply:
            db.commit()

    print("-" * 78)
    print(f"{'반영 완료' if apply else 'DRY-RUN (반영 안 함)'}: {moved_books}종 / {moved_qty}권")
    if not apply:
        print("실제 반영하려면 --apply 를 붙여 다시 실행하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(migrate(apply="--apply" in sys.argv))
