# API 통합 테스트 공용 픽스처.
# 원칙: 읽기 전용 GET과 4xx 유도만 수행한다 — 로컬 시연 시드를 오염시키지 않는다.
# 인증은 DB에 실존하는 계정으로 JWT를 직접 서명해 사용한다(비밀번호 비의존).
import datetime as dt
import os

import jwt
import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.db.session import engine
from app.models.wms import User


@pytest.fixture(autouse=True)
def _fresh_cookies(client):
    # client 픽스처가 module 스코프라 로그인 쿠키가 다음 테스트로 새어 들어간다.
    # 서버는 쿠키를 Authorization 헤더보다 우선하므로, 매 테스트 전에 비워 격리한다.
    client.cookies.clear()
    yield


def _mint_headers(employee_id: str) -> dict:
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
    return _mint_headers(master_user.employee_id)


@pytest.fixture(scope="session")
def worker_headers(worker_user) -> dict:
    return _mint_headers(worker_user.employee_id)


@pytest.fixture(scope="session")
def login_account() -> tuple[str, str]:
    # 실비밀번호 로그인 테스트용. 기본은 시연 계정 규칙, 환경변수로 재지정 가능.
    return (
        os.getenv("TEST_LOGIN_ID", "WM2608001"),
        os.getenv("TEST_LOGIN_PW", "1234"),
    )
