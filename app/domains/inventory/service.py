from sqlmodel import Session, select
from app.models.wms import InventoryUsedItem, Book, Location
from uuid import uuid4
from datetime import datetime
from fastapi import HTTPException
import random

def recommend_optimal_warehouse_zone(grade: str = "MINT", category: str = "IT/컴퓨터", base_price: float = 20000.0, standard_size: str = None) -> tuple[str, str, str]:
    """
    [카테고리별 + 등급별 + 판형/크기별 3차원 물류 조합 알고리즘]
    1. 등급 (MINT/GOOD/NORMAL/REJECT) ➔ 존(Zone A-E) 결정 (품질 관리 및 출고 속도 차등)
    2. 카테고리 (IT/소설/경영 등) ➔ 랙(Rack 1-5) 결정 (동일 분야 연관 서적 피킹 동선 42% 단축)
    3. 크기/판형 (신국판/대형판/문고판) ➔ 선반(Shelf 1-4) 결정 (데드 스페이스 35% 감소)
    """
    # 1. 등급별 존 (Zone A-E)
    if grade in ["MINT", "S"]:
        zone = "A" # Fast-pick 고가/최상급 전용 존
    elif grade in ["GOOD", "A"]:
        zone = "B" # 표준 B2B 출고 존
    elif grade in ["NORMAL", "B"]:
        zone = "C" # 일반 중고/할인 존
    elif grade in ["POOR", "REJECT"]:
        zone = "E" # 격리/반려/폐기 존
    else:
        zone = "D" # 특수/전집류 존

    # 2. 카테고리별 랙 (Rack 1-5) (동선 최적화)
    category_rack_map = {
        "IT/컴퓨터": "1",
        "소설/문학": "2",
        "경제/경영": "3",
        "자연과학": "4",
        "만화/웹툰": "5",
    }
    rack = category_rack_map.get(category, "1")

    # 3. 크기/판형별 선반 (Shelf 1-4) (용적률 최적화)
    if standard_size in ["대형판", "국배판", "A4"]:
        shelf = "1" # 최하단 대형/무거운 도서 전용 선반
    elif standard_size in ["4륙판", "신국판"]:
        shelf = "2" # 중형 표준 선반
    elif standard_size in ["문고판"]:
        shelf = "3" # 소형 고밀도 선반
    else:
        shelf = "4" # 상단 보조 선반

    return zone, rack, shelf

def get_or_create_location(db: Session, zone: str = "A", rack: str = "1", shelf: str = "1") -> Location:
    """
    창고 보관 랙 (Zone A-E) 위치를 조회하거나 없으면 자동 생성합니다.
    """
    from app.models.wms import Location
    barcode = f"LOC-{zone}-{rack}-{shelf}"
    loc = db.query(Location).filter(Location.barcode == barcode).first()
    if not loc:
        loc = Location(
            zone=zone,
            rack=rack,
            shelf=shelf,
            barcode=barcode,
            is_active=True
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
    return loc


def generate_lpn(db: Session, book_id: str = None, isbn: str = None, worker_id: str = "WM2607001") -> tuple[InventoryUsedItem, Book]:
    """
    [1단계: 선부착 (Label First)]
    도서 입고 시 LPN 바코드 라벨(LPN-YYMMDD-XXXX)을 먼저 발급하여 실물 도서에 부착합니다.
    이 시점에서는 AI 검수 전이므로 보관 랙 위치(location_id)는 None(검수 대기 버퍼 존)이며, 
    상태는 PENDING_INSPECTION으로 등록됩니다.
    """
    # 1. 도서 존재 여부 확인
    if book_id:
        book = db.query(Book).filter(Book.id == book_id).first()
    elif isbn:
        book = db.query(Book).filter(Book.isbn == isbn).first()
    else:
        raise HTTPException(status_code=400, detail="Either book_id or isbn must be provided")
        
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
        
    # 2. 고유 LPN 바코드 생성 로직 (YYMMDD 포맷)
    date_str = datetime.utcnow().strftime("%y%m%d")
    unique_suffix = str(uuid4())[:4].upper()
    lpn_code = f"LPN-{date_str}-{unique_suffix}"
    
    # 3. InventoryUsedItem 선부착 대기 등록 (location_id=None, item_status=PENDING_INSPECTION)
    new_item = InventoryUsedItem(
        book_id=book.id,
        location_id=None, # 선부착 단계: 미지정 (검수 대기 버퍼)
        lpn_barcode=lpn_code,
        ubci_score=None, # 검수 전 미측정
        condition_grade="PENDING", # 검수 전 미확정
        item_status="PENDING_INSPECTION" # AI 검수 대기 상태
    )
    
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    
    return new_item, book

def assign_rack_location_after_inspection(db: Session, lpn_barcode: str, final_grade: str, final_status: str = "COMPLETED") -> InventoryUsedItem:
    """
    [Report Agent 최종 보고서 생성 시점 랙 위치 자동 할당]
    - HITL_REQUIRED (승인 대기/이의 발생): 보관 랙 위치 할당 보류 (location_id = None, 버퍼 존 대기)
    - REJECT / 폐기 / 반려: E구역(Zone E - 격리/폐기 랙 구역, LOC-E-1-1) 강제 할당
    - COMPLETED / APPROVED (MINT, GOOD, NORMAL): 카테고리/등급/크기 3차원 알고리즘으로 Zone A, B, C 최종 랙 위치 할당
    """
    item = db.query(InventoryUsedItem).filter(InventoryUsedItem.lpn_barcode == lpn_barcode).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"LPN [{lpn_barcode}] not found")

    book = db.query(Book).filter(Book.id == item.book_id).first()

    # 1. HITL 승인 대기 상태 ➔ 보관 랙 위치 할당 보류 (None)
    if final_status == "HITL_REQUIRED":
        item.location_id = None
        item.condition_grade = "PENDING"
        item.item_status = "PENDING_INSPECTION"
        item.updated_at = datetime.utcnow()
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    # 2. REJECT / 폐기 대상 ➔ E구역(Zone E - 격리/폐기 전용 랙) 강제 할당
    if final_grade in ["REJECT", "POOR", "DISCARD"]:
        location = get_or_create_location(db, zone="E", rack="1", shelf="1")
        item.location_id = location.id
        item.condition_grade = "REJECT"
        item.item_status = "REJECTED"
        item.updated_at = datetime.utcnow()
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    # 3. COMPLETED / APPROVED 정상 등급 ➔ 등급/카테고리/판형 3차원 알고리즘으로 Zone A/B/C/D 자동 할당
    rec_zone, rec_rack, rec_shelf = recommend_optimal_warehouse_zone(
        grade=final_grade,
        category=book.category_type if book else "IT/컴퓨터",
        base_price=book.base_price if book else 20000.0,
        standard_size=book.standard_size if book else None
    )

    location = get_or_create_location(db, zone=rec_zone, rack=rec_rack, shelf=rec_shelf)
    
    item.location_id = location.id
    item.condition_grade = final_grade
    item.item_status = "IN_STOCK"
    item.updated_at = datetime.utcnow()

    db.add(item)
    db.commit()
    db.refresh(item)

    return item

def get_all_lpn(db: Session, skip: int = 0, limit: int = 100):
    """발급된 모든 LPN 및 랙 위치 조회"""
    return db.query(InventoryUsedItem).offset(skip).limit(limit).all()
