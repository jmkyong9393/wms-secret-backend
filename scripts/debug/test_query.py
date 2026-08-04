import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlmodel import Session, select, col, or_, func
from app.models.wms import User
from app.db.session import engine

with Session(engine) as session:
    try:
        statement = select(User)
        keyword = "test"
        statement = statement.where(
            or_(
                col(User.name).contains(keyword),
                col(User.employee_id).contains(keyword)
            )
        )
        count_statement = select(func.count()).select_from(statement.subquery())
        print("Query built successfully:", count_statement)
    except Exception as e:
        print("Error:", e)
