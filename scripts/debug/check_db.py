from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from app.models.wms import InventoryUsedItem, ReturnJob

engine = create_engine("postgresql://wms:wms1234!@localhost:5432/wms")
Session = sessionmaker(bind=engine)
db = Session()

used_item = db.query(InventoryUsedItem).filter(InventoryUsedItem.id == 'a34a7a97-d40f-4e4f-9591-9ae3baeee3bf').first()
if used_item:
    print(f"UsedItem found: {used_item.id}, source_job_id: {used_item.source_job_id}")
    if used_item.source_job_id:
        job = db.query(ReturnJob).filter(ReturnJob.id == used_item.source_job_id).first()
        print(f"Job found: {job is not None}")
else:
    print("UsedItem not found")

