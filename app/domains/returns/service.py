from sqlmodel import Session
from fastapi import Depends
from typing import List

from app.db.session import get_db
from app.models.wms import ReturnJob, JobStatusEnum
from app.worker.tasks import process_inspection
from uuid import UUID

class ReturnService:
    """
    Spring Boot의 @Service 역할 수행.
    비즈니스 로직을 Controller(Router)와 분리하여 추상화 및 테스트 용이성을 극대화합니다.
    (2-Layer 아키텍처 원칙에 따라 Repository 생략 후 직접 Session 제어)
    """
    def __init__(self, db: Session = Depends(get_db)):
        # FastAPI의 강력한 DI(의존성 주입) 엔진이 get_db()를 호출하여
        # 트랜잭션 관리용 Session 객체를 받아 Service 계층에 주입
        self.db = db

    def trigger_inspection(self, book_id: UUID, location_id: UUID, image_urls: List[str]) -> UUID:
        """
        반품 접수 및 AI 검수 파이프라인 트리거 비즈니스 로직
        """
        # 1. 반품 접수 DB 레코드 생성 (Router-Service 2-Layer 패턴)
        new_job = ReturnJob(
            book_id=book_id,
            image_urls=image_urls,
            status=JobStatusEnum.PENDING.value
        )
        self.db.add(new_job)
        self.db.flush() # INSERT 후 반환된 Primary Key(id)를 new_job 객체에 로드 (commit은 get_db가 담당)
        
        # 2. Celery Worker로 비동기 검수 파이프라인 위임 (Non-blocking)
        process_inspection.delay(str(new_job.id), image_urls)
        
        return new_job.id
