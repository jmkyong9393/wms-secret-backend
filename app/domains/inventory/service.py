from sqlalchemy.orm import Session
from app.models.wms import InventoryUsedItem, Book
from uuid import uuid4
from datetime import datetime
from fastapi import HTTPException
import random

def generate_lpn(db: Session, book_id: str) -> InventoryUsedItem:
    """
    고유한 LPN(License Plate Number)을 발급하고 DB에 저장합니다.
    형식: LPN-YYYYMMDD-XXXX
    """
    # 1. 도서 존재 여부 확인
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
        
    # 2. 고유 LPN 바코드 생성 로직
    date_str = datetime.utcnow().strftime("%Y%m%d")
    unique_suffix = str(uuid4())[:8].upper()
    lpn_code = f"LPN-{date_str}-{unique_suffix}"
    
    # 3. InventoryUsedItem 테이블에 가적재 (상태: PENDING_INSPECTION)
    new_item = InventoryUsedItem(
        book_id=book.id,
        location_id=None, # 미지정
        lpn_barcode=lpn_code,
        condition_grade="NORMAL", # 검수 전 임시값
        item_status="ALLOCATED" # 입고 대기(할당) 상태
    )
    
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    
    return new_item

def get_all_lpn(db: Session, skip: int = 0, limit: int = 100):
    """발급된 모든 LPN 조회 (대시보드용)"""
    return db.query(InventoryUsedItem).offset(skip).limit(limit).all()
