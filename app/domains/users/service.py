from passlib.context import CryptContext
from datetime import datetime, timedelta
import jwt
import random
import string
from sqlmodel import Session
from app.core.config import settings
from app.models.wms import User
from app.domains.users.schemas import UserCreate, OnboardingRequest, UserUpdate
from app.domains.users.repository import user_repository
from app.core.exceptions import (
    UserAlreadyExistsException,
    EmailAlreadyExistsException,
    InvalidInvitationCodeException
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class UserService:
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        try:
            return pwd_context.verify(plain_password, hashed_password)
        except Exception:
            return False

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

    def _generate_employee_id(self, session: Session, prefix: str) -> str:
        prefix = prefix.upper()
        yymm = datetime.utcnow().strftime("%y%m")
        base_prefix = f"{prefix}{yymm}"
        
        last_id = user_repository.get_last_employee_id_by_prefix(session, base_prefix)
        if last_id:
            try:
                seq = int(last_id[-3:]) + 1
            except ValueError:
                seq = 1
        else:
            seq = 1
            
        return f"{base_prefix}{seq:03d}"

    def get_next_employee_id(self, session: Session, prefix: str = "WM") -> str:
        return self._generate_employee_id(session, prefix)

    def register_user(self, session: Session, user_in: UserCreate) -> tuple[User, str]:
        employee_id = self._generate_employee_id(session, user_in.company_prefix)
        
        # 임시 비밀번호 생성 (8자리 영문+숫자)
        characters = string.ascii_letters + string.digits
        temp_password = ''.join(random.choice(characters) for i in range(8))
        
        user = User(
            employee_id=employee_id,
            name=user_in.name,
            password_hash=self.get_password_hash(temp_password),
            role=user_in.role.upper(),
            status="ACTIVE",
            must_change_password=True
        )
        created_user = user_repository.create_user(session, user)
        return created_user, temp_password

    def onboarding_user(self, session: Session, user: User, onboarding_in: OnboardingRequest) -> User:
        user.password_hash = self.get_password_hash(onboarding_in.new_password)
        user.must_change_password = False
        user.updated_at = datetime.utcnow()
        return user_repository.update_user(session, user)
        
    def update_user_profile(self, session: Session, user: User, update_in: UserUpdate) -> User:
        if update_in.name is not None:
            user.name = update_in.name
        if update_in.email is not None:
            user.email = update_in.email
        if update_in.phone_number is not None:
            user.phone_number = update_in.phone_number
        if update_in.address is not None:
            user.address = update_in.address
        if update_in.password is not None:
            user.password_hash = self.get_password_hash(update_in.password)
            user.must_change_password = False
            
        user.updated_at = datetime.utcnow()
        return user_repository.update_user(session, user)

    def authenticate_user(self, session: Session, employee_id: str, password: str) -> User | None:
        """
        PostgreSQL DB의 users 테이블 레코드를 100% 순수 SQL Query 및 bcrypt 패스워드 검증으로 인증합니다.
        """
        user = user_repository.get_user_by_employee_id(session, employee_id)
        if not user:
            return None

        if not self.verify_password(password, user.password_hash):
            return None

        return user

    def get_all_users_for_admin(self, session: Session, keyword: str | None = None, role: str | None = None, status: str | None = None, sort_by: str = "role", sort_order: str = "asc", skip: int = 0, limit: int = 20) -> tuple[list[User], int]:
        return user_repository.get_users_with_filters(session, keyword, role, status, sort_by, sort_order, skip, limit)

    def bulk_create_users(self, session: Session, users_in: list[dict]) -> list[dict]:
        results = []
        for user_data in users_in:
            employee_id = user_data["employee_id"]
            name = user_data["name"]
            role = user_data["role"]
            password = user_data["password"]

            existing = user_repository.get_user_by_employee_id(session, employee_id)
            if existing:
                results.append({"employee_id": employee_id, "success": False, "reason": "이미 존재하는 사번입니다."})
                continue

            try:
                user = User(
                    employee_id=employee_id,
                    name=name,
                    password_hash=self.get_password_hash(password),
                    role=role.upper(),
                    status="ACTIVE",
                    must_change_password=True
                )
                user_repository.create_user(session, user)
                results.append({"employee_id": employee_id, "success": True, "reason": None})
            except Exception as e:
                session.rollback()
                results.append({"employee_id": employee_id, "success": False, "reason": str(e)})
        
        return results

    def update_user_status(self, session: Session, employee_id: str, status: str) -> User | None:
        user = user_repository.get_user_by_employee_id(session, employee_id)
        if not user:
            return None
        user.status = status
        user.updated_at = datetime.utcnow()
        return user_repository.update_user(session, user)

    def update_user_role(self, session: Session, employee_id: str, role: str) -> User | None:
        user = user_repository.get_user_by_employee_id(session, employee_id)
        if not user:
            return None
        user.role = role
        user.updated_at = datetime.utcnow()
        return user_repository.update_user(session, user)

    def delete_user(self, session: Session, employee_id: str) -> bool:
        user = user_repository.get_user_by_employee_id(session, employee_id)
        if not user:
            return False
            
        from sqlalchemy.exc import IntegrityError
        try:
            user_repository.delete_user(session, user)
            return True
        except IntegrityError:
            session.rollback()
            raise ValueError("해당 사용자는 이미 작업 내역(게시글 등)이 존재하여 삭제할 수 없습니다. 대신 계정 상태를 '비활성'으로 변경해주세요.")

user_service = UserService()
