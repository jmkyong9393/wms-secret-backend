from sqlmodel import Session, select
from app.db.session import engine
from app.models.wms import ReturnJob, InventoryUsedItem, Book

with Session(engine) as session:
    print("=== All InventoryUsedItem ===")
    inv_items = session.exec(select(InventoryUsedItem)).all()
    for item in inv_items:
        print(f"ID: {item.id}, LPN: {item.lpn_barcode}, Grade: {item.condition_grade}, Score: {item.ubci_score}, Status: {item.item_status}, Created: {item.created_at}")
        
    print("\n=== All ReturnJob ===")
    return_jobs = session.exec(select(ReturnJob)).all()
    for r in return_jobs:
        print(f"Job ID: {r.id}, Status: {r.status}, Final Grade: {r.final_grade}, Score: {r.ubci_score}, Created: {r.created_at}")
