from sqlmodel import SQLModel, create_engine, Session
from app.core.config import settings

# 1. DB 접속 엔진 생성 (echo=True로 하면 실행되는 SQL 쿼리가 터미널에 출력됩니다)
engine = create_engine(settings.DATABASE_URL, echo=True)

# 2. 의존성 주입(Dependency Injection)용 세션 제너레이터 (전역 트랜잭션 관리)
def get_session():
    with Session(engine) as session:
        try:
            yield session
            # 에러 없이 API 로직이 끝나면 자동 커밋
            session.commit()
        except Exception:
            # 예외 발생 시 즉시 롤백 (Dirty Data 방지)
            session.rollback()
            raise
        finally:
            # 리소스 해제는 with 문이 알아서 해주지만 명시적 표기
            session.close()

# 3. 앱 시작 시 테이블 자동 생성 함수 (FastAPI의 on_startup에서 호출됨)
def create_db_and_tables():
    """앱 시작 시 테이블 생성 (PoC용)"""
    SQLModel.metadata.create_all(engine)
