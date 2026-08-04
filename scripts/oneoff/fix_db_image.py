import json
import urllib.request
from sqlmodel import Session, select
from app.db.session import engine
from app.models.wms import Book

with Session(engine) as session:
    books = session.exec(select(Book).where(Book.cover_image_url == 'https://contents.kyobobook.co.kr/s_pers/welcome/welcome_default.jpg')).all()
    print(f"Found {len(books)} books with fallback image.")
    
    # Also get the specific book 9788970509693 in case it has it or has something else
    book_comp = session.exec(select(Book).where(Book.isbn == '9788970509693')).first()
    if book_comp and book_comp not in books:
        books.append(book_comp)

    for book in books:
        print(f"Updating book: {book.title} (ISBN: {book.isbn})")
        ttb_key = "ttbjmkyong20022330001"
        aladin_url = f"http://www.aladin.co.kr/ttb/api/ItemLookUp.aspx?ttbkey={ttb_key}&itemIdType=ISBN13&ItemId={book.isbn}&output=js&Version=20131101&Cover=Big"
        try:
            req = urllib.request.Request(aladin_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw_data = resp.read().decode("utf-8").strip().rstrip(";")
                data = json.loads(raw_data)
                if "item" in data and len(data["item"]) > 0:
                    item = data["item"][0]
                    new_img = item.get("cover", "")
                    if new_img:
                        book.cover_image_url = new_img
                        print(f"Updated cover to {new_img}")
        except Exception as e:
            print(f"Error fetching for {book.isbn}: {e}")
            
    session.commit()
    print("Database update complete.")
