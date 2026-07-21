from app.db.session import engine
from sqlalchemy import text

def patch():
    with engine.connect() as conn:
        conn.execute(text('ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT FALSE;'))
        conn.commit()
    print("Patched successfully")

if __name__ == "__main__":
    patch()
