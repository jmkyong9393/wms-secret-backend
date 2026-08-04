import sys
sys.path.append(r'E:\취업\KT AIVLE School\빅프로젝트\develop\solo_develop\wms-secret-backend')
from app.db.session import engine
from sqlmodel import Session, select
from app.models.wms import Book

with Session(engine) as db:
    books = db.exec(select(Book)).all()
    print(f"Resetting {len(books)} books virtual stock...")
    for idx, b in enumerate(books):
        b.virtual_stock = (idx % 5) + 2  # Set stock between 2~6
        db.add(b)
    db.commit()
    print("Database Books virtual_stock successfully re-seeded!")
