from pydantic import BaseModel, Field
from typing import Optional


class LoginRequest(BaseModel):
    employee_id: str
    password: str


class LoginResponse(BaseModel):
    """
    로그인 응답 본문. JWT는 여기 포함하지 않는다 - HttpOnly 쿠키(Set-Cookie)로만 전달되어
    JS에서 절대 접근할 수 없게 한다 (XSS로 토큰이 탈취되는 경로 원천 차단).
    """

    message: str = "Login successful"
    employee_id: str
    name: str
    role: str
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    # 최초 로그인 강제 변경(must_change_password=True) 시에는 서버가 검증을 건너뛰므로 생략 가능하다.
    # 이미 온보딩을 마친 계정은 이 값이 없거나 틀리면 거부된다 (app/domains/users/service.py::change_password).
    current_password: Optional[str] = None
    # 길이/조합 규칙은 app/core/password_policy.py가 단독으로 판정한다.
    # 여기서 min_length를 함께 걸면 pydantic 422가 먼저 터져 "무엇이 부족한지"를 항목별로
    # 안내하지 못하고, 규칙 임계값이 두 곳으로 갈라져 서로 어긋난다.
    new_password: str = Field(..., min_length=1)


class PrivacyConsentRequest(BaseModel):
    """
    개인정보 수집·이용 동의 (개인정보 보호법 제15조 제2항).
    필수 동의만 존재하므로 agreed=False는 거부한다 - 동의하지 않으면 서비스 이용이 불가하다는
    사실을 화면에서 먼저 고지한다.
    """

    agreed: bool


class PasswordPolicyResponse(BaseModel):
    """
    화면이 안내 문구와 사전검증 기준을 서버에서 받아가기 위한 응답.
    규칙을 프론트에 복제해두면 서버가 바뀔 때 조용히 어긋나므로 서버를 단일 진실 공급원으로 둔다.
    """

    descriptions: list[str]
    min_length_two_classes: int
    min_length_three_classes: int
    max_length: int
    max_sequential_run: int
