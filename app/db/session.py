from sqlmodel import Session, create_engine
from typing import Generator
from app.core.config import settings

# 데이터베이스 통신을 담당할 엔진 객체 생성
# echo=True는 개발 중 실행되는 모든 SQL 쿼리를 터미널에 출력하여 디버깅을 돕습니다. (운영 시 False 권장)
engine = create_engine(settings.DATABASE_URL, echo=True)

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI 엔드포인트(라우터)에서 데이터베이스 세션을 안전하게 열고 닫기 위한 의존성 주입(DI) 제너레이터입니다.
    사용 예시: def read_books(session: Session = Depends(get_db)):
    """
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise