from fastapi import APIRouter, Depends, status, Request, Response, HTTPException
from sqlmodel import Session, select
from datetime import timedelta

from app.db.session import get_db
from app.core.config import settings
from app.domains.users.schemas import (
    UserCreate, UserResponse, LoginRequest, OnboardingRequest, UserUpdate,
    EmployeeListResponse, BulkCreateEmployeeRequest, BulkCreateEmployeeResponse,
    UpdateEmployeeStatusRequest, UpdateEmployeeStatusResponse,
    UpdateEmployeeRoleRequest, UpdateEmployeeRoleResponse
)
from app.domains.users.service import user_service
from app.core.exceptions import InvalidCredentialsException, InactiveAccountException
from app.core.limiter import limiter
from app.core.security import get_current_user, RoleChecker
from app.models.wms import User

router = APIRouter()

@router.post("/init-master", status_code=status.HTTP_201_CREATED)
@limiter.limit("2/minute")
def init_master(request: Request, session: Session = Depends(get_db)):
    """
    (초기 셋업 전용) DB에 유저가 존재하지 않을 때 최초의 MASTER 계정을 발급합니다.
    """
    existing_user = session.exec(select(User)).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Database is already initialized with users.")
        
    user_in = UserCreate(company_prefix="WM", name="System Admin", role="MASTER")
    user, temp_password = user_service.register_user(session=session, user_in=user_in)
    
    return {
        "message": "Initial Master account created successfully.",
        "employee_id": user.employee_id,
        "temporary_password": temp_password,
        "note": "Please login with this account and change the password in the onboarding screen."
    }

@router.post("/issue", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def issue_account(request: Request, user_in: UserCreate, session: Session = Depends(get_db)):
    """
    (Admin 전용) 사번 발급 및 계정 생성
    - company_prefix 기반 사번 자동 채번
    - 임시 비밀번호는 응답 헤더(또는 로깅)를 통해 Admin에게 전달될 수 있으나 현재는 개발 편의를 위해 임시로 로깅 생략
    """
    user, temp_password = user_service.register_user(session=session, user_in=user_in)
    # 실제 운영에서는 메일 발송이나 Admin에게만 리턴하는 구조 추가 가능
    return user

@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, response: Response, login_req: LoginRequest, session: Session = Depends(get_db)):
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
    
    response.set_cookie(
        key="token",
        value=access_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    
    response.set_cookie(
        key="role",
        value=user.role,
        httponly=False,
        secure=False,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    
    return {
        "access_token": access_token, 
        "token_type": "bearer", 
        "message": "Login successful",
        "must_change_password": user.must_change_password
    }

@router.post("/onboarding", response_model=UserResponse)
def onboarding(
    onboarding_req: OnboardingRequest, 
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    최초 로그인 시 비밀번호 변경 강제 처리
    """
    user = user_service.onboarding_user(session=session, user=current_user, onboarding_in=onboarding_req)
    return user

@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """
    내 정보 조회
    """
    return current_user

@router.put("/me", response_model=UserResponse)
def update_me(
    update_req: UserUpdate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    내 정보 수정 (마이페이지)
    """
    user = user_service.update_user_profile(session=session, user=current_user, update_in=update_req)
    return user

@router.post("/logout")
def logout(response: Response):
    """
    로그아웃: 발급된 쿠키를 강제 만료(삭제) 처리합니다.
    """
    response.delete_cookie(key="token")
    response.delete_cookie(key="role")
    return {"message": "Logout successful"}

# --- Admin / Employee Management Endpoints ---

@router.get("/admin", response_model=EmployeeListResponse)
def get_employee_list(
    keyword: str = None,
    role: str = None,
    status: str = None,
    sort_by: str = "role",
    sort_order: str = "asc",
    page: int = 1,
    size: int = 20,
    session: Session = Depends(get_db),
    _: User = Depends(RoleChecker(["MASTER", "ADMIN"]))
):
    skip = (page - 1) * size
    items, total = user_service.get_all_users_for_admin(session, keyword, role, status, sort_by, sort_order, skip, size)
    
    items_out = []
    for u in items:
        items_out.append({
            "employee_id": u.employee_id,
            "name": u.name,
            "role": u.role,
            "status": u.status,
            "created_at": u.created_at.isoformat() if u.created_at else ""
        })
    return {"items": items_out, "total": total, "page": page, "size": size}

@router.get("/admin/next-employee-id")
def get_next_employee_id(
    prefix: str = "WM",
    session: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker(["MASTER", "ADMIN"]))
):
    next_id = user_service.get_next_employee_id(session, prefix)
    return {"next_employee_id": next_id}

@router.post("/admin/create-accounts", response_model=BulkCreateEmployeeResponse)
def bulk_create_employees(
    req: BulkCreateEmployeeRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker(["MASTER", "ADMIN"]))
):
    users_in = [u.model_dump() for u in req.employees]
    results = user_service.bulk_create_users(session, users_in)
    return {"results": results}

@router.patch("/admin/{employee_id}/status", response_model=UpdateEmployeeStatusResponse)
def update_employee_status(
    employee_id: str,
    req: UpdateEmployeeStatusRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker(["MASTER", "ADMIN"]))
):
    from app.domains.users.repository import user_repository
    target_user = user_repository.get_user_by_employee_id(session, employee_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if current_user.role != "MASTER" and target_user.role == "MASTER":
        raise HTTPException(status_code=403, detail="ADMIN cannot modify MASTER accounts.")
        
    user = user_service.update_user_status(session, employee_id, req.status)
    return {"employee_id": user.employee_id, "status": user.status}

@router.patch("/admin/{employee_id}/role", response_model=UpdateEmployeeRoleResponse)
def update_employee_role(
    employee_id: str,
    req: UpdateEmployeeRoleRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker(["MASTER", "ADMIN"]))
):
    from app.domains.users.repository import user_repository
    target_user = user_repository.get_user_by_employee_id(session, employee_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if current_user.role != "MASTER":
        if target_user.role == "MASTER" or req.role == "MASTER":
            raise HTTPException(status_code=403, detail="ADMIN cannot manage MASTER roles.")
            
    user = user_service.update_user_role(session, employee_id, req.role)
    return {"employee_id": user.employee_id, "role": user.role}

@router.delete("/admin/{employee_id}")
def delete_employee(
    employee_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker(["MASTER", "ADMIN"]))
):
    from app.domains.users.repository import user_repository
    target_user = user_repository.get_user_by_employee_id(session, employee_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if current_user.role != "MASTER" and target_user.role == "MASTER":
        raise HTTPException(status_code=403, detail="ADMIN cannot delete MASTER accounts.")
        
    try:
        success = user_service.delete_user(session, employee_id)
        if not success:
            raise HTTPException(status_code=404, detail="User not found")
        return {"message": "User deleted successfully", "employee_id": employee_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
