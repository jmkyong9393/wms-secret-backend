from fastapi import APIRouter, Depends, Request, Response
from sqlmodel import Session
from datetime import timedelta

from app.db.session import get_db
from app.core.config import settings
from app.core.limiter import limiter
from app.core.security import get_current_user
from app.core.exceptions import (
    BadRequestException,
    InvalidCredentialsException,
    InactiveAccountException,
    TooManyLoginAttemptsException,
)
from app.core.sse_ticket_service import issue_sse_ticket
from app.domains.auth import throttle
from app.core import password_policy
from app.domains.auth.schemas import (
    LoginRequest,
    LoginResponse,
    ChangePasswordRequest,
    PasswordPolicyResponse,
    PrivacyConsentRequest,
)
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
@limiter.limit(settings.LOGIN_IP_RATE_LIMIT)
def login(
    request: Request,
    response: Response,
    login_req: LoginRequest,
    session: Session = Depends(get_db),
):
    """
    사번+비밀번호 인증 후 JWT를 HttpOnly 쿠키로 발급한다.

    브루트포스 방어는 2단이다:
    - IP 기준(@limiter.limit): 봇의 대량 시도를 거르는 광역 그물. 프록시 뒤에서도 실제
      클라이언트 IP로 버킷이 나뉜다(app/core/limiter.py 참고).
    - 사번 기준(throttle): 실제 계정 보호. **실패했을 때만** 세고 성공하면 리셋하므로,
      옆 사람의 오타가 내 로그인을 막지 않는다.
    """
    # 인증을 시도하기 전에 스로틀 상태부터 확인한다 - 차단 중에는 DB 조회조차 하지 않는다.
    blocked, _, retry_after = throttle.get_throttle_state(login_req.employee_id)
    if blocked:
        raise TooManyLoginAttemptsException(retry_after_seconds=retry_after)

    user = user_service.authenticate_user(
        session=session, employee_id=login_req.employee_id, password=login_req.password
    )
    if not user:
        remaining = throttle.register_failure(login_req.employee_id)
        raise InvalidCredentialsException(remaining_attempts=remaining)

    user_status_str = (
        str(user.status.value) if hasattr(user.status, "value") else str(user.status)
    )
    if user_status_str != "ACTIVE":
        # 자격증명 자체는 맞았으므로 실패 카운터를 올리지 않는다 (재시도해도 결과가 같은
        # 상태이고, 관리자 조치가 필요한 사안이라 스로틀로 가릴 이유가 없다).
        raise InactiveAccountException()

    # 로그인 성공 - 누적된 실패 기록을 즉시 지운다.
    throttle.clear(login_req.employee_id)

    role_str = str(user.role.value) if hasattr(user.role, "value") else str(user.role)

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = user_service.create_access_token(
        data={"sub": user.employee_id, "role": role_str},
        expires_delta=access_token_expires,
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
    current_user: User = Depends(get_current_user),
):
    """
    내 프로필 정보 수정 (이름/이메일/전화번호/주소). 비밀번호는 여기서 다루지 않는다 -
    PATCH /api/v1/auth/password 를 사용한다.
    """
    user = user_service.update_user_profile(
        session=session, user=current_user, update_in=update_req
    )
    return user


@router.post(
    "/privacy-consent",
    response_model=UserResponse,
    summary="개인정보 수집·이용 동의 기록",
)
def submit_privacy_consent(
    consent: PrivacyConsentRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    개인정보 수집·이용 동의를 기록한다 (개인정보 보호법 제15조).

    동의 여부가 아니라 **동의 시각**을 남긴다 - 처리자에게 입증책임이 있으므로
    "언제 받았는가"가 남지 않으면 동의를 받았다는 사실 자체를 증명할 수 없다.
    """
    if not consent.agreed:
        raise BadRequestException(
            "서비스 이용을 위해서는 개인정보 수집·이용 동의가 필요합니다."
        )

    user = user_service.record_privacy_consent(session=session, user=current_user)
    return user


@router.post("/sse-ticket", summary="SSE 스트림 1회성 접근 티켓 발급")
async def issue_stream_ticket(
    scope: str,
    current_user: User = Depends(get_current_user),
):
    """쿠키를 실을 수 없는 클라이언트용 SSE 접근 티켓 발급. scope에는 스트림 경로를 그대로 넣는다."""
    role = (
        str(current_user.role.value)
        if hasattr(current_user.role, "value")
        else str(current_user.role)
    )
    ticket, expires_in = await issue_sse_ticket(
        scope=scope, employee_id=current_user.employee_id, role=role
    )
    return {"ticket": ticket, "expires_in": expires_in, "scope": scope}


@router.get(
    "/password-policy",
    response_model=PasswordPolicyResponse,
    summary="비밀번호 작성 규칙 조회",
)
def get_password_policy():
    """
    화면이 안내 문구와 사전검증 기준을 서버에서 받아가기 위한 공개 엔드포인트.
    비밀번호를 정하기 전에 규칙을 알아야 하므로 인증을 요구하지 않는다(규칙 자체는 비밀이 아니다).
    """
    return PasswordPolicyResponse(
        descriptions=password_policy.POLICY_DESCRIPTIONS,
        min_length_two_classes=password_policy.MIN_LENGTH_TWO_CLASSES,
        min_length_three_classes=password_policy.MIN_LENGTH_THREE_CLASSES,
        max_length=password_policy.MAX_LENGTH,
        max_sequential_run=password_policy.MAX_SEQUENTIAL_RUN,
    )


@router.patch("/password", response_model=UserResponse)
def change_password(
    change_req: ChangePasswordRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
