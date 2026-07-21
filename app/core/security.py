from fastapi import Depends, Request
from sqlmodel import Session
import jwt
from typing import List

from app.core.config import settings
from app.db.session import get_db
from app.domains.users.repository import user_repository
from app.core.exceptions import UnauthorizedException, ForbiddenException
from app.models.wms import User

def get_token_from_cookie(request: Request) -> str:
    token = request.cookies.get("token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
        if not token:
            raise UnauthorizedException("Not authenticated")
    return token

def get_current_user(token: str = Depends(get_token_from_cookie), session: Session = Depends(get_db)) -> User:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        employee_id: str = payload.get("sub")
        if employee_id is None:
            raise UnauthorizedException("Could not validate credentials")
    except jwt.PyJWTError:
        raise UnauthorizedException("Could not validate credentials")
    
    user = user_repository.get_user_by_employee_id(session, employee_id=employee_id)
    if user is None:
        raise UnauthorizedException("User not found")
    
    # 2026-07-14 추가: Inactive 계정 접근 차단 로직 (보안 강화)
    if user.status != "ACTIVE":
        raise ForbiddenException("Inactive user account")
        
    return user

class RoleChecker:
    """
    의존성 주입(DI) 기반 선언적 권한 인가 클래스
    사용 예: @router.get("/...", dependencies=[Depends(RoleChecker(["MASTER", "WORKER"]))])
    """
    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, user: User = Depends(get_current_user)):
        if user.role not in self.allowed_roles:
            raise ForbiddenException(f"Operation not permitted for role: {user.role}")
        return user
