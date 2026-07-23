from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from uuid import UUID

class UserCreate(BaseModel):
    company_prefix: str = Field(..., min_length=2, max_length=2, description="고객사 이니셜 (예: KT, LG)")
    name: str
    role: str = Field(default="WORKER")

class OnboardingRequest(BaseModel):
    new_password: str = Field(..., min_length=4)

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    address: Optional[str] = None
    password: Optional[str] = None

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

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class LoginRequest(BaseModel):
    employee_id: str
    password: str

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
