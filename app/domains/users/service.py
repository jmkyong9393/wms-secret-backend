from passlib.context import CryptContext
from datetime import datetime, timedelta
import jwt
from sqlmodel import Session
from fastapi import status
from app.core.config import settings
from app.models.wms import User
from app.domains.users.schemas import UserCreate
from app.domains.users.repository import user_repository
from app.core.exceptions import (
    UserAlreadyExistsException,
    EmailAlreadyExistsException,
    InvalidInvitationCodeException
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class UserService:
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return pwd_context.verify(plain_password, hashed_password)

    def get_password_hash(self, password: str) -> str:
        return pwd_context.hash(password)

    def create_access_token(self, data: dict, expires_delta: timedelta | None = None) -> str:
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=15)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        return encoded_jwt

    def register_user(self, session: Session, user_in: UserCreate) -> User:
        if user_repository.get_user_by_employee_id(session, user_in.employee_id):
            raise UserAlreadyExistsException()
        if user_repository.get_user_by_email(session, user_in.email):
            raise EmailAlreadyExistsException()

        # Auto-Provisioning Role Based on Invitation Code
        role = "GUEST"
        if user_in.invitation_code == settings.MASTER_INVITATION_CODE:
            role = "MASTER"
        elif user_in.invitation_code == settings.WORKER_INVITATION_CODE:
            role = "WORKER"
        else:
            raise InvalidInvitationCodeException()

        user = User(
            employee_id=user_in.employee_id,
            email=user_in.email,
            name=user_in.name,
            password_hash=self.get_password_hash(user_in.password),
            role=role,
            status="ACTIVE"
        )
        return user_repository.create_user(session, user)

    def authenticate_user(self, session: Session, employee_id: str, password: str) -> User | None:
        user = user_repository.get_user_by_employee_id(session, employee_id)
        if not user:
            return None
        if not self.verify_password(password, user.password_hash):
            return None
        return user

user_service = UserService()
