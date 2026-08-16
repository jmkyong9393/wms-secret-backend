from passlib.context import CryptContext
from datetime import datetime, timedelta
import jwt
import random
import string
from sqlmodel import Session, select, col, or_
from sqlalchemy import func, case
from app.core.config import settings
from app.models.wms import User
from app.domains.users.schemas import UserCreate, UserUpdate
from app.core.exceptions import (
    UserAlreadyExistsException,
    EmailAlreadyExistsException,
    InvalidInvitationCodeException,
    InvalidCredentialsException,
    PasswordPolicyViolationException,
)
from app.core.password_policy import validate_password
from app.models.wms import now_kst

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

class UserService:
    """
    Spring Boot의 @Service 역할. 2-Layer 아키텍처 원칙에 따라 Repository를 두지 않고
    Session을 직접 제어한다 (SQLModel 자체가 이미 Unit-of-Work/Repository 구현체이므로
    한 겹 더 감싸는 건 중복 추상화로 간주).
    """

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
            expire = now_kst() + expires_delta
        else:
            expire = now_kst() + timedelta(minutes=15)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        return encoded_jwt

    # --- 조회 (다른 도메인/보안 계층에서도 이 메서드를 통해서만 User를 조회한다) ---

    def get_user_by_employee_id(self, session: Session, employee_id: str) -> User | None:
        statement = select(User).where(User.employee_id == employee_id)
        return session.exec(statement).first()

    def get_user_by_email(self, session: Session, email: str) -> User | None:
        statement = select(User).where(User.email == email)
        return session.exec(statement).first()

    def get_user_by_id(self, session: Session, user_id: str) -> User | None:
        return session.get(User, user_id)

    def _get_last_employee_id_by_prefix(self, session: Session, prefix: str) -> str | None:
        statement = select(User.employee_id).where(
            col(User.employee_id).startswith(prefix)
        ).order_by(col(User.employee_id).desc())
        return session.exec(statement).first()

    # --- 원시 CRUD (Session 직접 제어) ---

    def _create_user_row(self, session: Session, user: User) -> User:
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    def _update_user_row(self, session: Session, user: User) -> User:
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    def _delete_user_row(self, session: Session, user: User) -> None:
        session.delete(user)
        session.commit()

    # --- 비즈니스 로직 ---

    def _generate_employee_id(self, session: Session, prefix: str) -> str:
        prefix = (prefix or "WM").upper()
        # 동적 현재 연도(2자리) + 생성월(2자리) 추출 (예: 2026년 8월 -> 2608)
        yymm = now_kst().strftime("%y%m")
        base_prefix = f"{prefix}{yymm}"

        last_id = self._get_last_employee_id_by_prefix(session, base_prefix)
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
        created_user = self._create_user_row(session, user)
        return created_user, temp_password

    def update_user_profile(self, session: Session, user: User, update_in: UserUpdate) -> User:
        if update_in.name is not None:
            user.name = update_in.name
        if update_in.email is not None:
            user.email = update_in.email
        if update_in.phone_number is not None:
            user.phone_number = update_in.phone_number
        if update_in.address is not None:
            user.address = update_in.address

        user.updated_at = now_kst()
        return self._update_user_row(session, user)

    def change_password(
        self, session: Session, user: User, current_password: str | None, new_password: str
    ) -> User:
        """
        비밀번호 변경 단일 진입점 (마이페이지 자율 변경 / 온보딩 강제 변경 공용).
        - 이미 온보딩을 마친 계정(must_change_password=False)은 현재 비밀번호 검증을 강제한다.
          쿠키 인증은 SameSite=lax라도 CSRF 여지가 있으므로, 세션이 살아있다는 사실만으로
          비밀번호를 바꿀 수 있게 두지 않는다.
        - 최초 로그인 직후(must_change_password=True) 계정은 발급받은 임시 비밀번호로 이미
          로그인 인증을 통과한 상태이므로 현재 비밀번호 재입력을 요구하지 않는다.
        """
        if not user.must_change_password:
            if not current_password or not self.verify_password(current_password, user.password_hash):
                raise InvalidCredentialsException()

        # [2026-08-06 신설] 비밀번호 작성 규칙 검증 (KISA 기술적·관리적 보호조치 기준).
        # 검증 지점은 "비밀번호를 새로 정하는 순간" 한 곳뿐이다. 로그인 경로에는 걸지 않으므로
        # 이미 발급되어 운영 중인 계정(시연 계정 포함)은 강제로 무효화되지 않는다.
        violations = validate_password(new_password, employee_id=user.employee_id, name=user.name)
        if violations:
            raise PasswordPolicyViolationException(violations)

        if current_password and current_password == new_password:
            raise PasswordPolicyViolationException(["기존 비밀번호와 다른 비밀번호를 사용해야 합니다."])

        user.password_hash = self.get_password_hash(new_password)
        user.must_change_password = False
        user.updated_at = now_kst()
        return self._update_user_row(session, user)

    def record_privacy_consent(self, session: Session, user: User) -> User:
        """
        개인정보 수집·이용 동의 시각을 기록한다.
        이미 동의한 계정은 최초 동의 시각을 보존한다 - 재기록하면 "언제 처음 동의했는가"라는
        증빙 가치가 사라진다.
        """
        if user.privacy_consent_at is None:
            user.privacy_consent_at = now_kst()
            user.updated_at = now_kst()
            return self._update_user_row(session, user)
        return user

    def authenticate_user(self, session: Session, employee_id: str, password: str) -> User | None:
        """
        PostgreSQL DB의 users 테이블 레코드를 100% 순수 SQL Query 및 bcrypt 패스워드 검증으로 인증합니다.
        """
        user = self.get_user_by_employee_id(session, employee_id)
        if not user:
            return None

        if not self.verify_password(password, user.password_hash):
            return None

        return user

    def get_all_users_for_admin(
        self,
        session: Session,
        keyword: str | None = None,
        role: str | None = None,
        status: str | None = None,
        sort_by: str = "role",
        sort_order: str = "asc",
        skip: int = 0,
        limit: int = 20
    ) -> tuple[list[User], int]:
        statement = select(User)

        if keyword:
            statement = statement.where(
                or_(
                    col(User.name).contains(keyword),
                    col(User.employee_id).contains(keyword)
                )
            )
        if role:
            statement = statement.where(User.role == role)
        if status:
            statement = statement.where(User.status == status)

        count_statement = select(func.count()).select_from(statement.subquery())
        total = session.exec(count_statement).one()

        # Sorting logic
        if sort_by == "role":
            role_rank = case(
                (User.role == "MASTER", 1),
                (User.role == "ADMIN", 2),
                (User.role == "WORKER", 3),
                (User.role == "GUEST", 4),
                else_=5
            )
            order_col = role_rank.asc() if sort_order == "asc" else role_rank.desc()
        elif sort_by == "name":
            order_col = col(User.name).asc() if sort_order == "asc" else col(User.name).desc()
        elif sort_by == "created_at":
            order_col = col(User.created_at).asc() if sort_order == "asc" else col(User.created_at).desc()
        else:
            # Default fallback to employee_id
            order_col = col(User.employee_id).asc() if sort_order == "asc" else col(User.employee_id).desc()

        statement = statement.order_by(order_col).offset(skip).limit(limit)
        items = session.exec(statement).all()

        return items, total

    def bulk_create_users(self, session: Session, users_in: list[dict]) -> list[dict]:
        results = []
        for user_data in users_in:
            employee_id = user_data["employee_id"]
            name = user_data["name"]
            role = user_data["role"]
            password = user_data["password"]

            existing = self.get_user_by_employee_id(session, employee_id)
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
                self._create_user_row(session, user)
                results.append({"employee_id": employee_id, "success": True, "reason": None})
            except Exception as e:
                session.rollback()
                results.append({"employee_id": employee_id, "success": False, "reason": str(e)})

        return results

    def update_user_status(self, session: Session, employee_id: str, status: str) -> User | None:
        user = self.get_user_by_employee_id(session, employee_id)
        if not user:
            return None
        user.status = status
        user.updated_at = now_kst()
        return self._update_user_row(session, user)

    def update_user_role(self, session: Session, employee_id: str, role: str) -> User | None:
        user = self.get_user_by_employee_id(session, employee_id)
        if not user:
            return None
        user.role = role
        user.updated_at = now_kst()
        return self._update_user_row(session, user)

    def delete_user(self, session: Session, employee_id: str) -> bool:
        user = self.get_user_by_employee_id(session, employee_id)
        if not user:
            return False

        from sqlalchemy.exc import IntegrityError
        try:
            self._delete_user_row(session, user)
            return True
        except IntegrityError:
            session.rollback()
            raise ValueError("해당 사용자는 이미 작업 내역(게시글 등)이 존재하여 삭제할 수 없습니다. 대신 계정 상태를 '비활성'으로 변경해주세요.")

user_service = UserService()
