import sys
import random
from uuid import uuid4
from datetime import datetime

sys.path.append(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend')

from app.db.session import engine
from sqlmodel import Session, select
from app.models.wms import Book, Location, Inventory, InventoryUsedItem, ConditionGradeEnum

GRADES = [
    (ConditionGradeEnum.MINT.value, 98),
    (ConditionGradeEnum.GOOD.value, 86),
    (ConditionGradeEnum.NORMAL.value, 72),
]

with Session(engine) as db:
    books = db.exec(select(Book)).all()
    locations = db.exec(select(Location)).all()

    if not locations:
        print("No locations found! Creating default locations...")
        for z in ["Zone A", "Zone B", "Zone C", "Zone D", "Zone E"]:
            for r in range(1, 4):
                for s in range(1, 5):
                    loc = Location(
                        id=uuid4(),
                        zone=z,
                        rack=f"Rack {str(r).zfill(2)}",
                        shelf=f"Shelf {str(s).zfill(2)}",
                        barcode=f"LOC-{z.replace(' ', '')}-R{r}-S{s}",
                        is_active=True
                    )
                    db.add(loc)
        db.commit()
        locations = db.exec(select(Location)).all()

    print(f"Seeding Inventory and InventoryUsedItem for {len(books)} books across {len(locations)} locations...")

    for idx, book in enumerate(books):
        loc = locations[idx % len(locations)]

        # 1. Create or update Inventory record
        inv_stmt = select(Inventory).where(Inventory.book_id == book.id, Inventory.location_id == loc.id)
        existing_inv = db.exec(inv_stmt).first()
        if not existing_inv:
            inv = Inventory(
                id=uuid4(),
                book_id=book.id,
                location_id=loc.id,
                quantity=random.randint(15, 60)
            )
            db.add(inv)

        # 2. Create InventoryUsedItem LPN record
        grade_val, score_val = GRADES[idx % len(GRADES)]
        lpn_str = f"LPN-20260731-{str(idx + 1).zfill(4)}"
        
        used_stmt = select(InventoryUsedItem).where(InventoryUsedItem.lpn_barcode == lpn_str)
        existing_used = db.exec(used_stmt).first()
        if not existing_used:
            used_item = InventoryUsedItem(
                id=uuid4(),
                book_id=book.id,
                location_id=loc.id,
                lpn_barcode=lpn_str,
                ubci_score=score_val,
                condition_grade=grade_val,
                item_status="IN_STOCK"
            )
            db.add(used_item)

    db.commit()

    total_inv = len(db.exec(select(Inventory)).all())
    total_used = len(db.exec(select(InventoryUsedItem)).all())

    print(f"Successfully seeded Inventory ({total_inv} records) and InventoryUsedItem ({total_used} LPN items)!")
