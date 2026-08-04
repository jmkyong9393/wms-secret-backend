from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from uuid import UUID

class UserCreate(BaseModel):
    company_prefix: str = Field(..., min_length=2, max_length=2, description="고객사 이니셜 (예: KT, LG)")
    name: str
    role: str = Field(default="WORKER")

class UserUpdate(BaseModel):
    # 비밀번호는 이 스키마로 다루지 않는다 - PATCH /api/v1/auth/password 단일 엔드포인트로만 변경한다
    # (과거 이 필드로 프로필 수정에 비밀번호 변경을 얹었던 것이 old-password 검증 없이 통과되는 구멍이었음)
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None

class UserResponse(BaseModel):
    id: UUID
    employee_id: str
    name: str
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None
    role: str
    status: str
    must_change_password: bool

# --- Admin / Employee Management Schemas ---

class BulkCreateEmployeeRow(BaseModel):
    employee_id: str
    name: str
    role: str
    password: str

class BulkCreateEmployeeRequest(BaseModel):
    employees: list[BulkCreateEmployeeRow]

class BulkCreateEmployeeResult(BaseModel):
    employee_id: str
    success: bool
    reason: Optional[str] = None

class BulkCreateEmployeeResponse(BaseModel):
    results: list[BulkCreateEmployeeResult]

class EmployeeListItem(BaseModel):
    employee_id: str
    name: str
    role: str
    status: str
    created_at: str

class EmployeeListResponse(BaseModel):
    items: list[EmployeeListItem]
    total: int
    page: int
    size: int

class UpdateEmployeeStatusRequest(BaseModel):
    status: str

class UpdateEmployeeStatusResponse(BaseModel):
    employee_id: str
    status: str

class UpdateEmployeeRoleRequest(BaseModel):
    role: str

class UpdateEmployeeRoleResponse(BaseModel):
    employee_id: str
    role: str
