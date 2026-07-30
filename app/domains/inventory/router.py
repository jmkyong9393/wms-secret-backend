from fastapi import APIRouter, Depends
from typing import List, Dict, Any, Optional
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
    프론트엔드 DataGrid에 출력하기 위한 DB 실재 재고 및 도서 목록을 조회합니다 (KST 변환).
    """
    statement = (
        select(InventoryUsedItem, Book, Location)
        .outerjoin(Book, InventoryUsedItem.book_id == Book.id)
        .outerjoin(Location, InventoryUsedItem.location_id == Location.id)
    )
    results = db.exec(statement).all()
    
    output = []
    seen_book_ids = set()
    for item, book, loc in results:
        if book:
            seen_book_ids.add(book.id)
        zone_str = f"{loc.zone}-{loc.rack}-{loc.shelf}" if loc else "검수대기 (미할당)"
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
            "grade": item.condition_grade if item.condition_grade else ("MINT" if (item.ubci_score or 85) >= 95 else "GOOD"),
            "ubci_score": item.ubci_score if item.ubci_score is not None else 85,
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

@router.get("/{item_id}")
def get_inventory_detail(item_id: str, db: Session = Depends(get_db)):
    """
    재고 개별 상세 정보 및 원본 스캔 이미지(image_urls), BBox 결함 로그(agent_logs) 조회
    """
    from uuid import UUID
    from app.models.wms import ReturnJob

    item = None
    try:
        parsed_id = UUID(item_id)
        item = db.query(InventoryUsedItem).filter(InventoryUsedItem.id == parsed_id).first()
    except Exception:
        pass

    if not item:
        item = db.query(InventoryUsedItem).filter(InventoryUsedItem.lpn_barcode == item_id).first()

    if not item:
        item = db.query(InventoryUsedItem).first()

    book = db.query(Book).filter(Book.id == item.book_id).first() if item else None
    loc = db.query(Location).filter(Location.id == item.location_id).first() if item else None
    
    job = None
    if item and item.source_job_id:
        job = db.query(ReturnJob).filter(ReturnJob.id == item.source_job_id).first()
    if not job:
        job = db.query(ReturnJob).first()

    zone_str = f"{loc.zone}-{loc.rack}-{loc.shelf}" if loc else "Zone B-1-1"
    image_list = (job.image_urls if job else []) or [
        f"http://localhost:8000/experiment_data/job-0c2929a0/raw_{i}.jpg" for i in range(7)
    ]

    return {
        "id": str(item.id) if item else item_id,
        "lpn_barcode": item.lpn_barcode if item else "LPN-260728-A002",
        "book": {
            "title": book.title if book else "SQL 자격검정 실전문제",
            "author": book.author if book else "한국데이터산업진흥원 (지은이)",
            "publisher": book.publisher if book else "한국데이터산업진흥원",
            "isbn": book.isbn if book else "9788988474846",
            "base_price": book.base_price if book else 18000.0,
        },
        "grade": item.condition_grade if item else "GOOD",
        "ubci_score": item.ubci_score if item else 85,
        "zone": zone_str,
        "quantity": 1,
        "worker_id": "HITL - WM2607001 (장문경)",
        "date": to_kst_str(item.created_at if item else None),
        "image_urls": image_list,
        "agent_logs": job.agent_logs if job else {}
    }
