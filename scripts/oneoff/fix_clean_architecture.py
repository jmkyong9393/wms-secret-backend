# -*- coding: utf-8 -*-
import json
import urllib.request
from sqlmodel import Session, select
from app.db.session import engine
from app.models.wms import Book

with Session(engine) as session:
    statement = select(Book).where((Book.cover_image_url == 'https://contents.kyobobook.co.kr/s_pers/welcome/welcome_default.jpg') | (Book.cover_image_url == None) | (Book.cover_image_url == ''))
    books = session.exec(statement).all()
    
    clean_arch = session.exec(select(Book).where(Book.title.contains('클린 아키텍처'))).all()
    for c in clean_arch:
        if c not in books:
            books.append(c)

    for book in books:
        if not book.isbn: continue
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
                    if new_img and new_img != book.cover_image_url:
                        book.cover_image_url = new_img
                        print(f"Updated cover to {new_img}")
        except Exception as e:
            print(f"Error fetching for {book.isbn}: {e}")
            
    session.commit()
    print("Database update complete.")
