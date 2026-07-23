from sqlmodel import Session, select, col
from sqlalchemy import func, case
from typing import Optional, Tuple, List
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

    def get_last_employee_id_by_prefix(self, session: Session, prefix: str) -> Optional[str]:
        statement = select(User.employee_id).where(
            col(User.employee_id).startswith(prefix)
        ).order_by(col(User.employee_id).desc())
        return session.exec(statement).first()

    def create_user(self, session: Session, user: User) -> User:
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
        
    def update_user(self, session: Session, user: User) -> User:
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    def delete_user(self, session: Session, user: User) -> None:
        session.delete(user)
        session.commit()

    def get_users_with_filters(
        self, 
        session: Session, 
        keyword: Optional[str] = None, 
        role: Optional[str] = None, 
        status: Optional[str] = None, 
        sort_by: str = "role",
        sort_order: str = "asc",
        skip: int = 0, 
        limit: int = 20
    ) -> Tuple[List[User], int]:
        from sqlmodel import or_
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

user_repository = UserRepository()
