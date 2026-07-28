from fastapi import APIRouter, Depends
from typing import List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.domains.inventory.service import generate_lpn, get_all_lpn

# Inventory 도메인 라우터: 새 상품 및 중고/반품 도서들의 통합 재고 관리를 담당합니다.
router = APIRouter(prefix="/inventory", tags=["Inventory"])


from sqlmodel import select
from app.models.wms import InventoryUsedItem, Book, Location

@router.get("/")
def get_inventory(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """
    프론트엔드 DataGrid에 출력하기 위한 전체 통합 재고 목록을 실시간 DB 조인 조회합니다.
    """
    statement = (
        select(InventoryUsedItem, Book, Location)
        .outerjoin(Book, InventoryUsedItem.book_id == Book.id)
        .outerjoin(Location, InventoryUsedItem.location_id == Location.id)
    )
    results = db.exec(statement).all()
    
    output = []
    for item, book, loc in results:
        zone_str = f"Zone {loc.zone}-{loc.rack}-{loc.shelf}" if loc else "Zone A-1-1"
        output.append({
            "id": str(item.id),
            "lpn_barcode": item.lpn_barcode,
            "book": {
                "title": book.title if book else "도서 정보 없음",
                "author": book.author if book else "-",
                "publisher": book.publisher if book else "-",
                "isbn": book.isbn if book else "-",
                "base_price": book.base_price if book else 0.0,
            },
            "grade": item.condition_grade,
            "ubci_score": item.ubci_score or 90,
            "zone": zone_str,
            "quantity": 1,
            "worker_id": "WM2607001",
            "date": item.created_at.strftime("%Y-%m-%d %H:%M:%S") if item.created_at else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        })
    
    if not output:
        # DB에 저장된 개별 중고 아이템이 없을 경우 전체 Book 기본 목록에서 변환 생성
        books = db.exec(select(Book)).all()
        for b in books:
            output.append({
                "id": str(b.id),
                "lpn_barcode": f"LPN-260727-{str(b.id)[:4].upper()}",
                "book": {
                    "title": b.title,
                    "author": b.author or "-",
                    "publisher": b.publisher or "-",
                    "isbn": b.isbn,
                    "base_price": b.base_price,
                },
                "grade": "MINT",
                "ubci_score": 95,
                "zone": "Zone A-1-1",
                "quantity": 1,
                "worker_id": "WM2607001",
                "date": b.created_at.strftime("%Y-%m-%d %H:%M:%S") if b.created_at else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            })

    return output

from typing import Optional
from pydantic import BaseModel

class CreateLpnRequest(BaseModel):
    book_id: Optional[str] = None
    isbn: Optional[str] = None
    worker_id: Optional[str] = None
    zone: Optional[str] = None # Zone A, B, C, D, E

@router.post("/lpn")
def create_lpn(req: CreateLpnRequest, db: Session = Depends(get_db)):
    """새로운 LPN 바코드를 발급하고 지정된 창고 보관 랙(Zone A-E) 위치와 조인하여 DB에 저장합니다."""
    new_lpn, book = generate_lpn(db, book_id=req.book_id, isbn=req.isbn, zone=req.zone)
    return {
        "status": "success", 
        "lpn_barcode": new_lpn.lpn_barcode,
        "book": {
            "title": book.title,
            "author": book.author,
            "isbn": book.isbn
        },
        "location_id": str(new_lpn.location_id),
        "worker_id": req.worker_id
    }

@router.get("/lpn")
async def get_lpn_list(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """발급된 모든 LPN 내역을 조회합니다 (프론트 대시보드 연동용)."""
    lpns = get_all_lpn(db, skip=skip, limit=limit)
    return [{"lpn_barcode": l.lpn_barcode, "book_id": l.book_id, "status": l.item_status} for l in lpns]
