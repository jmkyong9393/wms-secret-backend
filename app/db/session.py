from typing import Generator

from sqlmodel import Session, create_engine

from app.core.config import settings

# SQL 원문 로그는 기본 꺼짐(SQL_ECHO). 켜면 쿼리 파라미터가 로그 수집기까지 흘러간다.
# pre_ping: DB 재시작·유휴 순단 후 죽은 커넥션 재사용으로 500이 나는 것을 방지 (장기 무인 운영 대비)
engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
    pool_pre_ping=True,
    pool_recycle=1800,
)


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
