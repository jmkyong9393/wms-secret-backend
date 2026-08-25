from fastapi import APIRouter, Depends, status, Request, HTTPException
from sqlmodel import Session, select

from app.db.session import get_db
from app.core.config import settings
from app.domains.users.schemas import (
    UserCreate,
    UserResponse,
    EmployeeListResponse,
    BulkCreateEmployeeRequest,
    BulkCreateEmployeeResponse,
    UpdateEmployeeStatusRequest,
    UpdateEmployeeStatusResponse,
    UpdateEmployeeRoleRequest,
    UpdateEmployeeRoleResponse,
)
from app.domains.users.service import user_service
from app.core.limiter import limiter
from app.core.security import RoleChecker
from app.models.wms import User

# 로그인/로그아웃/내 정보 조회·수정/비밀번호 변경은 app/domains/auth/router.py
# (/api/v1/auth/*)로 이관되었다. 이 라우터는 관리자용 사원 리소스 관리(CRUD)만 다룬다.

from app.models.wms import UserRoleEnum

# 계정 발급용 관리자 가드. 이 라우터는 init-master/reset-all-passwords가 무인증이어야
# 하므로 라우터 단위로 걸 수 없다 — 해당 엔드포인트에만 붙인다.
_users_admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])

router = APIRouter()


def _reject_in_prod() -> None:
    # init-master/reset-all-passwords는 인증 가드가 없는 개발/시연 전용 엔드포인트다.
    # (init-master는 최초 부팅 시 관리자 계정이 아직 없는 chicken-and-egg 상황을 풀기 위한 것이고,
    # reset-all-passwords는 데모 중 잠긴 계정을 즉시 복구하기 위한 것 - 둘 다 인증을 요구하면
    # 존재 목적 자체가 성립하지 않는다) 대신 운영 환경에서는 호출 자체를 차단한다.
    if settings.APP_ENV == "prod":
        raise HTTPException(
            status_code=403, detail="This endpoint is disabled in production."
        )


@router.post("/init-master", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def init_master(request: Request, session: Session = Depends(get_db)):
    """
    (초기 셋업 전용, 운영 환경에서는 비활성화) DB에 유저가 존재하지 않을 때
    최초의 MASTER 계정(WM2608001 / 장문경)을 발급합니다.
    """
    _reject_in_prod()
    existing_user = session.exec(select(User)).first()
    if existing_user:
        raise HTTPException(
            status_code=400, detail="Database is already initialized with users."
        )

    user_in = UserCreate(company_prefix="WM", name="장문경", role="MASTER")
    user, temp_password = user_service.register_user(session=session, user_in=user_in)

    # 개발/시연 편의를 위한 1234 해시 자동 세팅
    user.password_hash = user_service.get_password_hash("1234")
    user.must_change_password = False
    session.add(user)
    session.commit()
    session.refresh(user)

    return {
        "message": "Initial Master account created successfully.",
        "employee_id": user.employee_id,
        "name": user.name,
        "role": user.role,
        "password": "1234",
        "note": f"Initial Master account initialized with employee_id '{user.employee_id}' and password '1234'.",
    }


@router.post("/reset-all-passwords")
def reset_all_passwords(session: Session = Depends(get_db)):
    """
    개발/시연 편의 (운영 환경에서는 비활성화): DB 내 모든 계정 (WM2608001, WM2608002 등)의
    비밀번호를 '1234'로 100% 일괄 재설정합니다.
    """
    _reject_in_prod()
    users = session.exec(select(User)).all()
    new_hash = user_service.get_password_hash("1234")
    updated_ids = []
    for u in users:
        u.password_hash = new_hash
        u.must_change_password = False
        session.add(u)
        updated_ids.append(u.employee_id)
    session.commit()
    return {
        "status": "success",
        "message": f"Successfully reset passwords for {len(updated_ids)} accounts to '1234'.",
        "updated_employee_ids": updated_ids,
        "standard_password": "1234",
    }


@router.post("/issue", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
def issue_account(
    request: Request,
    user_in: UserCreate,
    session: Session = Depends(get_db),
    # docstring에는 "(Admin 전용)"이라 적혀 있었으나 실제 인가가 없어
    # 인증 없이 계정을 만들 수 있었다. 라우터 단위로 걸 수 없는 것은
    # 같은 라우터의 init-master/reset-all-passwords가 무인증이어야 하기 때문이다.
    current_admin=Depends(_users_admin_only),
):
    """
    (Admin 전용) 사번 발급 및 계정 생성
    - company_prefix 기반 사번 자동 채번
    - 임시 비밀번호는 응답 헤더(또는 로깅)를 통해 Admin에게 전달될 수 있으나 현재는 개발 편의를 위해 임시로 로깅 생략
    """
    user, temp_password = user_service.register_user(session=session, user_in=user_in)
    # 실제 운영에서는 메일 발송이나 Admin에게만 리턴하는 구조 추가 가능
    return user


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
    _: User = Depends(RoleChecker(["MASTER", "ADMIN"])),
):
    skip = (page - 1) * size
    items, total = user_service.get_all_users_for_admin(
        session, keyword, role, status, sort_by, sort_order, skip, size
    )

    items_out = []
    for u in items:
        items_out.append(
            {
                "employee_id": u.employee_id,
                "name": u.name,
                "role": u.role,
                "status": u.status,
                "created_at": u.created_at.isoformat() if u.created_at else "",
            }
        )
    return {"items": items_out, "total": total, "page": page, "size": size}


@router.get("/admin/next-employee-id")
def get_next_employee_id(
    prefix: str = "WM",
    session: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker(["MASTER", "ADMIN"])),
):
    next_id = user_service.get_next_employee_id(session, prefix)
    return {"next_employee_id": next_id}


@router.post("/admin/create-accounts", response_model=BulkCreateEmployeeResponse)
def bulk_create_employees(
    req: BulkCreateEmployeeRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker(["MASTER", "ADMIN"])),
):
    users_in = [u.model_dump() for u in req.employees]
    results = user_service.bulk_create_users(session, users_in)
    return {"results": results}


@router.patch(
    "/admin/{employee_id}/status", response_model=UpdateEmployeeStatusResponse
)
def update_employee_status(
    employee_id: str,
    req: UpdateEmployeeStatusRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker(["MASTER", "ADMIN"])),
):
    target_user = user_service.get_user_by_employee_id(session, employee_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if current_user.role != "MASTER" and target_user.role == "MASTER":
        raise HTTPException(
            status_code=403, detail="ADMIN cannot modify MASTER accounts."
        )

    user = user_service.update_user_status(session, employee_id, req.status)
    return {"employee_id": user.employee_id, "status": user.status}


@router.patch("/admin/{employee_id}/role", response_model=UpdateEmployeeRoleResponse)
def update_employee_role(
    employee_id: str,
    req: UpdateEmployeeRoleRequest,
    session: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker(["MASTER", "ADMIN"])),
):
    target_user = user_service.get_user_by_employee_id(session, employee_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if current_user.role != "MASTER":
        if target_user.role == "MASTER" or req.role == "MASTER":
            raise HTTPException(
                status_code=403, detail="ADMIN cannot manage MASTER roles."
            )

    user = user_service.update_user_role(session, employee_id, req.role)
    return {"employee_id": user.employee_id, "role": user.role}


@router.delete("/admin/{employee_id}")
def delete_employee(
    employee_id: str,
    session: Session = Depends(get_db),
    current_user: User = Depends(RoleChecker(["MASTER", "ADMIN"])),
):
    target_user = user_service.get_user_by_employee_id(session, employee_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if current_user.role != "MASTER" and target_user.role == "MASTER":
        raise HTTPException(
            status_code=403, detail="ADMIN cannot delete MASTER accounts."
        )

    try:
        success = user_service.delete_user(session, employee_id)
        if not success:
            raise HTTPException(status_code=404, detail="User not found")
        return {"message": "User deleted successfully", "employee_id": employee_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
