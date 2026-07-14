from pydantic import BaseModel, EmailStr
from uuid import UUID

class UserCreate(BaseModel):
    employee_id: str
    email: EmailStr
    name: str
    password: str
    invitation_code: str

class UserResponse(BaseModel):
    id: UUID
    employee_id: str
    email: EmailStr
    name: str
    role: str
    status: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class LoginRequest(BaseModel):
    employee_id: str
    password: str
