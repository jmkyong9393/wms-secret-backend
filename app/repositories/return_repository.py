from sqlmodel import Session
from app.models.wms import ReturnJob, JobStatusEnum

class ReturnRepository:
    """
    Spring Boot의 @Repository 역할 수행 (DAO).
    DB(Session)를 주입받아 오직 DB Query 작업만 전담하며, 비즈니스 로직은 포함하지 않습니다.
    """
    def __init__(self, db: Session):
        self.db = db

    def create_pending_job(self, book_id: int, location_id: int) -> ReturnJob:
        """새로운 PENDING 상태의 반품 작업을 생성하고 식별자를 할당받습니다."""
        new_job = ReturnJob(
            book_id=book_id,
            location_id=location_id,
            status=JobStatusEnum.PENDING.value
        )
        self.db.add(new_job)
        self.db.flush() # INSERT 후 반환된 Primary Key(id)를 new_job 객체에 로드
        return new_job
