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

from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

def to_kst_str(dt: Optional[datetime]) -> str:
    if not dt:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")

@router.get("/")
def get_inventory(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """
    프론트엔드 DataGrid에 출력하기 위한 실재 DB 조인 재고 목록을 조회합니다 (하드코딩 폴백 전면 제거, KST 변환).
    """
    statement = (
        select(InventoryUsedItem, Book, Location)
        .outerjoin(Book, InventoryUsedItem.book_id == Book.id)
        .outerjoin(Location, InventoryUsedItem.location_id == Location.id)
    )
    results = db.exec(statement).all()
    
    output = []
    for item, book, loc in results:
        zone_str = f"Zone {loc.zone}-{loc.rack}-{loc.shelf}" if loc else "검수대기 (미할당)"
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
            "grade": item.condition_grade,  # 미지정 시 None 반환
            "ubci_score": item.ubci_score,  # 미지정 시 None 반환
            "zone": zone_str,
            "quantity": 1,
            "worker_id": "WM2607001",
            "date": to_kst_str(item.created_at)
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

class BinPackRequest(BaseModel):
    books: List[Dict[str, Any]]

@router.post("/bin-pack")
def recommend_box_packing(req: BinPackRequest):
    """주문 도서들의 판정 체적 기반 3D Bin Packing 최적 박스 추천 알고리즘 API입니다."""
    from app.domains.inventory.bin_packing import recommend_optimal_box
    recommended_box = recommend_optimal_box(req.books)
    return {
        "recommended_box": recommended_box,
        "item_count": len(req.books),
        "message": f"3D 패킹 연산 결과 최적 포장 상자: [{recommended_box}] 추천 완료"
    }
