# 인증 흐름 통합 테스트 — 로그인 계약, 토큰 검증, /auth/me 보호.
import jwt as pyjwt
import pytest

API = "/api/v1/auth"


def test_login_success_sets_httponly_cookie(client, login_account):
    emp_id, password = login_account
    r = client.post(f"{API}/login", json={"employee_id": emp_id, "password": password})
    if r.status_code == 401:
        pytest.skip("시연 계정 비밀번호 불일치 — TEST_LOGIN_ID/TEST_LOGIN_PW로 지정 필요")
    assert r.status_code == 200
    body = r.json()
    # 토큰은 본문이 아니라 HttpOnly 쿠키로만 전달된다(XSS 탈취 경로 차단 설계).
    assert "access_token" not in body
    assert body["employee_id"] == emp_id
    assert body["role"] in ("MASTER", "ADMIN", "WORKER", "GUEST")
    assert "token" in r.cookies


def test_login_unknown_employee_returns_401(client):
    # 실계정을 쓰면 로그인 스로틀에 실패가 누적되므로 가상 사번으로만 실패를 유도한다.
    r = client.post(f"{API}/login", json={"employee_id": "ZZ9999999", "password": "nope"})
    assert r.status_code == 401


def test_login_missing_field_returns_422(client):
    r = client.post(f"{API}/login", json={"employee_id": "ZZ9999999"})
    assert r.status_code == 422


def test_me_without_token_returns_401(client):
    r = client.get(f"{API}/me")
    assert r.status_code == 401


def test_me_with_valid_token(client, master_user, master_headers):
    r = client.get(f"{API}/me", headers=master_headers)
    assert r.status_code == 200
    assert r.json()["employee_id"] == master_user.employee_id


def test_me_with_forged_token_returns_401(client, master_user):
    # 다른 키로 서명된 토큰은 서명 검증에서 거부되어야 한다.
    forged = pyjwt.encode({"sub": master_user.employee_id}, "wrong-secret", algorithm="HS256")
    r = client.get(f"{API}/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401
