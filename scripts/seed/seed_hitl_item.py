from sqlmodel import Session, select
from app.db.session import engine
from app.models.wms import ReturnJob, Book, JobStatusEnum

with Session(engine) as session:
    # Find clean architecture book or create default
    book = session.exec(select(Book)).first()
    
    # Check if ReturnJob exists
    existing = session.exec(select(ReturnJob).where(ReturnJob.status == JobStatusEnum.HITL_REQUIRED.value)).all()
    if not existing:
        hitl_job = ReturnJob(
            book_id=book.id if book else None,
            image_urls=[
                "http://localhost:8000/experiment_data/job-0c2929a0/raw_0.jpg",
                "http://localhost:8000/experiment_data/job-0c2929a0/raw_1.jpg",
                "http://localhost:8000/experiment_data/job-0c2929a0/raw_2.jpg"
            ],
            status=JobStatusEnum.HITL_REQUIRED.value,
            ubci_score=75,
            final_grade="HITL_REQUIRED",
            agent_logs={
                "lpn_barcode": "LPN-260728-A002",
                "defect_description": "[감점: -15점] 수험서 필기/낙서/밑줄",
                "defect_coordinates": [{"ymin": 200, "xmin": 150, "ymax": 300, "xmax": 400, "label": "필기/낙서/밑줄"}]
            }
        )
        session.add(hitl_job)
        session.commit()
        print("Created HITL ReturnJob DB record successfully!")
    else:
        print(f"Already found {len(existing)} HITL jobs in DB!")
