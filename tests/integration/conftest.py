# 통합 스모크 테스트 공용 픽스처.
# tests/api/v1/conftest.py와 같은 원칙: DB 실존 계정으로 JWT를 직접 서명한다(비밀번호 비의존).
import datetime as dt

import jwt
import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.db.session import engine
from app.models.wms import User

# 로컬 전용 가드 - 이 스위트는 쓰기를 포함하므로, DB가 로컬이 아니면 실행 자체를 거부한다.
# (운영/시연 DB는 SELECT만 허용이 프로젝트 원칙. 배포 검증은 실물 등록 테스트가 담당한다.)
_LOCAL_DB_HOSTS = {"localhost", "127.0.0.1", "host.docker.internal", "postgres", "wms-postgres"}


def pytest_sessionstart(session):
    from urllib.parse import urlparse

    host = (urlparse(settings.DATABASE_URL.replace("postgresql://", "http://")).hostname or "")
    if host not in _LOCAL_DB_HOSTS:
        pytest.exit(
            f"통합 스모크는 로컬 DB에서만 실행합니다. 현재 DATABASE_URL 호스트: {host!r} "
            "(운영 DB 오염 방지 가드 - tests/integration/README.md 참조)",
            returncode=3,
        )

    # 빈 DB 자급자족 부트스트랩 - 평가자가 시드 없이 바로 돌릴 수 있게 한다.
    # create_all은 없는 테이블만 만들고 기존 테이블은 건드리지 않는다(멱등).
    from sqlmodel import SQLModel, Session, select
    import app.models.wms as m

    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        if not s.exec(select(m.Location)).first():
            s.add(m.Location(zone="A", rack="01", shelf="01", barcode="A-01-01"))
            s.commit()
        if not s.exec(select(m.User)).first():
            # 최초 관리자(WM2608001/1234)는 서비스의 정식 경로로 생성한다
            from fastapi.testclient import TestClient
            from app.main import app as _app
            with TestClient(_app) as c:
                c.post("/api/v1/users/init-master")
        # 워커 계정도 정식 발급 경로로 1명 확보 (없을 때만)
        if not s.exec(select(m.User).where(m.User.role == "WORKER")).first():
            master = s.exec(select(m.User).where(m.User.role.in_(["MASTER", "ADMIN"]))).first()
            if master:
                from app.domains.users.service import user_service
                from app.domains.users.schemas import UserCreate
                u, _pw = user_service.register_user(session=s, user_in=UserCreate(
                    company_prefix="WM", name="평가용워커", role="WORKER"))
                u.status = "ACTIVE"
                s.add(u)
                s.commit()


@pytest.fixture(autouse=True)
def _fresh_cookies(client):
    # 로그인 쿠키가 다음 테스트로 새지 않도록 매 테스트 전에 비운다.
    client.cookies.clear()
    yield


def mint_headers(employee_id: str) -> dict:
    token = jwt.encode(
        {"sub": employee_id, "exp": dt.datetime.utcnow() + dt.timedelta(minutes=30)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


def _find_active_user(roles: list[str]) -> User | None:
    with Session(engine) as s:
        for role in roles:
            user = s.exec(
                select(User).where(User.role == role, User.status == "ACTIVE")
            ).first()
            if user:
                return user
    return None


@pytest.fixture(scope="session")
def master_user() -> User:
    user = _find_active_user(["MASTER", "ADMIN"])
    if user is None:
        pytest.skip("ACTIVE 상태의 MASTER/ADMIN 계정이 DB에 없음")
    return user


@pytest.fixture(scope="session")
def worker_user() -> User:
    user = _find_active_user(["WORKER"])
    if user is None:
        pytest.skip("ACTIVE 상태의 WORKER 계정이 DB에 없음")
    return user


@pytest.fixture(scope="session")
def master_headers(master_user) -> dict:
    return mint_headers(master_user.employee_id)


@pytest.fixture(scope="session")
def worker_headers(worker_user) -> dict:
    return mint_headers(worker_user.employee_id)
