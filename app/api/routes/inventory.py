from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from app.core.database import get_session
from app.models.wms import Inventory, Book

router = APIRouter(prefix="/inventory", tags=["Inventory"])

@router.get("/")
def get_inventory(session: Session = Depends(get_session)):
    """다중 등급 재고 조회 (Mock)"""
    statement = select(Inventory, Book).join(Book, Inventory.book_id == Book.id)
    results = session.exec(statement).all()
    
    response = []
    for inv, book in results:
        response.append({
            "inventory_id": inv.id,
            "book_title": book.title,
            "isbn": book.isbn,
            "location_id": inv.location_id,
            "ubci_grade": inv.ubci_grade,
            "quantity": inv.quantity
        })
    return response
