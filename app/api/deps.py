from sqlmodel import Session
from app.core.database import engine

def get_db():
    """
    FastAPI 의존성 주입(DI)용 DB 세션 제너레이터.
    API 요청 생명주기와 동기화되어 안전하게 트랜잭션을 commit/rollback 합니다.
    """
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
