from fastapi import APIRouter, Depends, Request, Response
from sqlmodel import Session
from datetime import timedelta

from app.db.session import get_db
from app.core.config import settings
from app.core.limiter import limiter
from app.core.security import get_current_user
from app.core.exceptions import InvalidCredentialsException, InactiveAccountException
from app.domains.auth.schemas import LoginRequest, LoginResponse, ChangePasswordRequest
from app.domains.users.schemas import UserResponse, UserUpdate
from app.domains.users.service import user_service
from app.models.wms import User

router = APIRouter()


def _set_auth_cookies(response: Response, access_token: str, role: str) -> None:
    max_age = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    # JWT 원본은 HttpOnly로만 내려간다 - JS에서 절대 읽을 수 없다.
    response.set_cookie(
        key="token",
        value=access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        max_age=max_age,
    )
    # role은 미들웨어(Next.js Edge)/클라이언트가 RBAC 리다이렉트에 즉시 사용해야 하므로
    # 의도적으로 HttpOnly가 아니다. 민감정보가 아니므로 노출 허용.
    response.set_cookie(
        key="role",
        value=role,
        httponly=False,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=settings.COOKIE_DOMAIN,
        max_age=max_age,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(key="token", domain=settings.COOKIE_DOMAIN)
    response.delete_cookie(key="role", domain=settings.COOKIE_DOMAIN)


@router.post("/login", response_model=LoginResponse)
@limiter.limit("5/minute")
def login(request: Request, response: Response, login_req: LoginRequest, session: Session = Depends(get_db)):
    """
    사번+비밀번호 인증 후 JWT를 HttpOnly 쿠키로 발급한다.
    """
    user = user_service.authenticate_user(session=session, employee_id=login_req.employee_id, password=login_req.password)
    if not user:
        raise InvalidCredentialsException()

    user_status_str = str(user.status.value) if hasattr(user.status, 'value') else str(user.status)
    if user_status_str != "ACTIVE":
        raise InactiveAccountException()

    role_str = str(user.role.value) if hasattr(user.role, 'value') else str(user.role)

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = user_service.create_access_token(
        data={"sub": user.employee_id, "role": role_str}, expires_delta=access_token_expires
    )

    _set_auth_cookies(response, access_token, role_str)

    return LoginResponse(
        employee_id=user.employee_id,
        name=user.name,
        role=role_str,
        must_change_password=user.must_change_password,
    )


@router.post("/logout")
def logout(response: Response):
    """
    발급된 인증 쿠키를 서버 측에서 강제 만료시킨다.
    """
    _clear_auth_cookies(response)
    return {"message": "Logout successful"}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """내 정보 조회"""
    return current_user


@router.put("/me", response_model=UserResponse)
def update_me(
    update_req: UserUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    내 프로필 정보 수정 (이름/이메일/전화번호/주소). 비밀번호는 여기서 다루지 않는다 -
    PATCH /api/v1/auth/password 를 사용한다.
    """
    user = user_service.update_user_profile(session=session, user=current_user, update_in=update_req)
    return user


@router.patch("/password", response_model=UserResponse)
def change_password(
    change_req: ChangePasswordRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    비밀번호 변경 단일 엔드포인트. 마이페이지의 자율 변경과 최초 로그인 온보딩(강제 변경)이
    모두 이 엔드포인트 하나로 수렴한다 (app/domains/users/service.py::change_password 참고).
    """
    user = user_service.change_password(
        session=session,
        user=current_user,
        current_password=change_req.current_password,
        new_password=change_req.new_password,
    )
    return user
