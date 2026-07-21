from fastapi import APIRouter, Depends
from typing import List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.domains.inventory.service import generate_lpn, get_all_lpn

# Inventory 도메인 라우터: 새 상품 및 중고/반품 도서들의 통합 재고 관리를 담당합니다.
router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.get("/")
async def get_inventory() -> List[Dict[str, Any]]:
    """
    프론트엔드 DataGrid에 출력하기 위한 전체 통합 재고 목록을 조회합니다.
    기존의 복잡했던 DB 필드명을 프론트엔드 인터페이스에 맞춰 가공(Mapping)하여 반환합니다.
    (예: inventory_id -> id, location_id -> zone 등)
    """
    return [
        {
            "id": "uuid-inv-1",
            "book": {
                "title": "사피엔스",
                "isbn": "9788912345678"
            },
            "grade": "MINT",
            "zone": "A-1-3",
            "quantity": 100,
            "date": datetime.utcnow().isoformat()
        },
        {
            "id": "uuid-inv-2",
            "book": {
                "title": "이기적 유전자",
                "isbn": "9788912345679"
            },
            "grade": "GOOD",
            "zone": "B-2-1",
            "quantity": 15,
            "date": datetime.utcnow().isoformat()
    ]

@router.post("/lpn")
async def create_lpn(book_id: str, db: Session = Depends(get_db)):
    """새로운 LPN 바코드를 발급하고 DB에 등록합니다."""
    # TODO: Pydantic response model 적용
    new_lpn = generate_lpn(db, book_id)
    return {"status": "success", "lpn_barcode": new_lpn.lpn_barcode}

@router.get("/lpn")
async def get_lpn_list(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """발급된 모든 LPN 내역을 조회합니다 (프론트 대시보드 연동용)."""
    lpns = get_all_lpn(db, skip=skip, limit=limit)
    return [{"lpn_barcode": l.lpn_barcode, "book_id": l.book_id, "status": l.item_status} for l in lpns]
