from sqlmodel import Session
from fastapi import Depends
from typing import List

from app.api.deps import get_db
from app.repositories.return_repository import ReturnRepository
from app.worker.tasks import process_inspection

class ReturnService:
    """
    Spring Boot의 @Service 역할 수행.
    비즈니스 로직을 Controller(Router)와 분리하여 추상화 및 테스트 용이성을 극대화합니다.
    """
    def __init__(self, db: Session = Depends(get_db)):
        # FastAPI의 강력한 DI(의존성 주입) 엔진이 get_db()를 호출하여
        # 트랜잭션 관리용 Session 객체를 받아 Repository 계층에 주입
        self.repository = ReturnRepository(db)

    def trigger_inspection(self, book_id: int, location_id: int, image_urls: List[str]) -> int:
        """
        반품 접수 및 AI 검수 파이프라인 트리거 비즈니스 로직
        """
        # 1. 반품 접수 DB 레코드 생성 (Repository 계층 위임)
        new_job = self.repository.create_pending_job(book_id, location_id)
        
        # 2. Celery Worker로 비동기 검수 파이프라인 위임 (Non-blocking)
        process_inspection.delay(str(new_job.id), image_urls)
        
        return new_job.id
