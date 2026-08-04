from sqlmodel import Session, select
from app.db.session import engine
from app.models.wms import InventoryUsedItem, Book

with Session(engine) as session:
    print("=== InventoryUsedItem Records ===")
    items = session.exec(select(InventoryUsedItem)).all()
    print(f"Total InventoryUsedItem count: {len(items)}")
    for item in items:
        print(f"ID: {item.id}, LPN: {item.lpn_barcode}, BookID: {item.book_id}, Grade: {item.condition_grade}, Status: {item.item_status}")
