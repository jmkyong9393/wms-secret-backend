import sys
import os

sys.stdout.reconfigure(encoding='utf-8')

from sqlmodel import Session, select, delete
from app.db.session import engine
from app.models.wms import InventoryUsedItem, ReturnJob, InboundJob

target_lpns = [f"LPN-260803-A{str(i).zfill(3)}" for i in range(1, 9)]
print(f"🗑️ Delete Target LPN List: {target_lpns}")

with Session(engine) as session:
    deleted_used_count = 0
    deleted_return_count = 0
    
    # 1. Delete from inventory_used_items
    for lpn in target_lpns:
        statement = select(InventoryUsedItem).where(InventoryUsedItem.lpn_barcode == lpn)
        items = session.exec(statement).all()
        for item in items:
            session.delete(item)
            deleted_used_count += 1

    # 2. Delete matching ReturnJobs by agent_logs lpn_barcode
    return_jobs = session.exec(select(ReturnJob)).all()
    for rj in return_jobs:
        if rj.agent_logs and isinstance(rj.agent_logs, dict):
            lpn = rj.agent_logs.get('lpn_barcode')
            if lpn in target_lpns:
                session.delete(rj)
                deleted_return_count += 1
                
    session.commit()
    print(f"✅ DB Delete Operation Completed Successfully!")
    print(f"  • InventoryUsedItem Records Deleted: {deleted_used_count}건")
    print(f"  • ReturnJob Records Deleted: {deleted_return_count}건")
