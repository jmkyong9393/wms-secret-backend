from sqlmodel import Session, select
from app.db.session import engine
from app.models.wms import ReturnJob, JobStatusEnum

with Session(engine) as session:
    jobs = session.exec(select(ReturnJob).where(ReturnJob.status == JobStatusEnum.HITL_REQUIRED.value)).all()
    for job in jobs:
        # Include all 7 raw images captured for job-0c2929a0
        job.image_urls = [
            f"http://localhost:8000/experiment_data/job-0c2929a0/raw_{i}.jpg" for i in range(7)
        ]
        session.add(job)
    session.commit()
    print("Updated ReturnJob with ALL 7 inspection images successfully!")
