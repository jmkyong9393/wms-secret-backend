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
    new_password: str = Field(..., min_length=4)
