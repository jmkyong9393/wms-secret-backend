from sqlmodel import Session, select
from typing import Optional
from app.models.wms import User

class UserRepository:
    def get_user_by_employee_id(self, session: Session, employee_id: str) -> Optional[User]:
        statement = select(User).where(User.employee_id == employee_id)
        return session.exec(statement).first()

    def get_user_by_email(self, session: Session, email: str) -> Optional[User]:
        statement = select(User).where(User.email == email)
        return session.exec(statement).first()

    def get_user_by_id(self, session: Session, user_id: str) -> Optional[User]:
        return session.get(User, user_id)

    def create_user(self, session: Session, user: User) -> User:
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

user_repository = UserRepository()
