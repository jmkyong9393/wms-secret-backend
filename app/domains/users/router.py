from fastapi import APIRouter, Depends, status, Request, Response
from sqlmodel import Session
from datetime import timedelta

from app.core.database import get_session
from app.core.config import settings
from app.domains.users.schemas import UserCreate, UserResponse, LoginRequest
from app.domains.users.service import user_service
from app.core.exceptions import InvalidCredentialsException, InactiveAccountException
from app.core.limiter import limiter

router = APIRouter()

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def register(request: Request, user_in: UserCreate, session: Session = Depends(get_session)):
    """
    초대 코드 기반 자동 권한(Role) 할당 및 회원 가입
    - 올바른 초대 코드를 입력하면 즉시 ACTIVE 상태로 권한이 부여됩니다.
    """
    user = user_service.register_user(session=session, user_in=user_in)
    return user

@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, response: Response, login_req: LoginRequest, session: Session = Depends(get_session)):
    """
    JWT Access Token 발급 및 HttpOnly 쿠키 설정
    """
    user = user_service.authenticate_user(session=session, employee_id=login_req.employee_id, password=login_req.password)
    if not user:
        raise InvalidCredentialsException()
    if user.status != "ACTIVE":
        raise InactiveAccountException()
        
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = user_service.create_access_token(
        data={"sub": user.employee_id, "role": user.role}, expires_delta=access_token_expires
    )
    
    # HttpOnly 쿠키에 토큰 세팅
    response.set_cookie(
        key="token",
        value=access_token,
        httponly=True,
        secure=False, # TODO: 운영 환경(HTTPS) 배포 시 True로 변경
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    
    # 프론트 라우팅 및 상태 관리를 위한 일반 쿠키
    response.set_cookie(
        key="role",
        value=user.role,
        httponly=False,
        secure=False,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    
    return {"access_token": access_token, "token_type": "bearer", "message": "Login successful"}

@router.post("/logout")
def logout(response: Response):
    """
    로그아웃: 발급된 쿠키를 강제 만료(삭제) 처리합니다.
    """
    response.delete_cookie(key="token")
    response.delete_cookie(key="role")
    return {"message": "Logout successful"}
