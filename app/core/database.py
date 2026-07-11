from sqlmodel import SQLModel, create_engine, Session
from app.core.config import settings

engine = create_engine(settings.DATABASE_URL, echo=True)

def get_session():
    with Session(engine) as session:
        yield session

def create_db_and_tables():
    """앱 시작 시 테이블 생성 (PoC용)"""
    SQLModel.metadata.create_all(engine)
