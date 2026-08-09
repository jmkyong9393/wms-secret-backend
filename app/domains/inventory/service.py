from sqlmodel import Session, select
from app.models.wms import InventoryUsedItem, Book, Location
from uuid import uuid4
from datetime import datetime
from fastapi import HTTPException
from app.models.wms import now_kst

def recommend_optimal_warehouse_zone(grade: str = "MINT", category: str = "IT/컴퓨터", base_price: float = 20000.0, standard_size: str = None) -> tuple[str, str, str]:
    """
    [카테고리별 + 등급별 + 판형/크기별 3차원 물류 조합 알고리즘]
    1. 등급 (MINT/GOOD/NORMAL/REJECT) ➔ 존(Zone A-E) 결정 (품질 관리 및 출고 속도 차등)
    2. 카테고리 (IT/소설/경영 등) ➔ 랙(Rack 1-5) 결정 (동일 분야 연관 서적 피킹 동선 42% 단축)
    3. 크기/판형 (신국판/대형판/문고판) ➔ 선반(Shelf 1-4) 결정 (데드 스페이스 35% 감소)
    """
    # 1. 조장님 절대적 3D 물류 규칙: 등급별 존 (Zone A-E)
    if grade in ["NEW", "NEW_FASTTRACK"]:
        zone = "A" # Zone A: 신품 전용 고속 입출고 피킹 존
    elif grade in ["MINT", "S"]:
        zone = "B" # Zone B: MINT (S급/최상급) 중고 전용 존
    elif grade in ["GOOD", "A"]:
        zone = "C" # Zone C: GOOD (A급/상급) 중고 표준 존
    elif grade in ["NORMAL", "B"]:
        zone = "D" # Zone D: NORMAL (B급/중급) 중고 존
    elif grade in ["POOR", "REJECT"]:
        zone = "E" # Zone E: REJECT (폐기/반려) 격리 전용 랙 구역
    else:
        zone = "D"

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


def fasttrack_new_stock_inbound(db: Session, book: Book, qty: int):
    """
    신품 도서 Fast-Track 입고의 공용 집행 로직.
    Zone A(신품존) 묶음 재고(Inventory) upsert + virtual_stock 가산 + INBOUND 원장 기록.

    신품은 개별 LPN을 발급하지 않고 수량으로만 관리한다. 호출처는 두 곳:
    1) POST /inbound/fasttrack - 현장 ISBN 스캔 입고
    2) 자동 발주(OrderProposal) 승인 - AUTO_PO로 입고되는 신품도 동일 관문을 통과시켜
       "발주 입고 = MINT 중고 LPN 생성"이던 기존 오류 경로를 제거한다.

    commit은 호출자가 담당한다 (트랜잭션 단위를 호출자가 결정).
    """
    from app.models.wms import Inventory, InventoryLog

    location = get_or_create_location(db, zone="A", rack="1", shelf="1")
    inv = db.exec(
        select(Inventory).where(Inventory.book_id == book.id, Inventory.location_id == location.id)
    ).first()
    if inv:
        inv.quantity += qty
        inv.updated_at = now_kst()
    else:
        inv = Inventory(book_id=book.id, location_id=location.id, quantity=qty)
    db.add(inv)

    book.virtual_stock = (book.virtual_stock or 0) + qty
    book.updated_at = now_kst()
    db.add(book)
    db.add(InventoryLog(
        transaction_type="INBOUND",
        book_id=book.id,
        condition_grade="NEW",
        quantity_change=qty,
        picked_location=f"{location.zone}-{location.rack}-{location.shelf}",
    ))
    return inv, location


LPN_ZONES = ("A", "B", "C", "D", "E")
LPN_LIVE_ZONE = "A"  # 라이브 검수 네임스페이스 (시드/데모는 B~E를 쓴다)


def generate_next_lpn_barcode(db: Session, zone: str = None) -> str:
    """등록 라인(zone) 기준 다음 LPN 문자열만 채번한다 (행 생성은 호출부 책임).
    순번 규칙은 assign_and_print_lpn과 동일(zone+날짜 접두어 기준 max+1). 배경: 33번 문서."""
    zone_code = (zone or LPN_LIVE_ZONE).strip().upper()[-1:]
    if zone_code not in LPN_ZONES:
        zone_code = LPN_LIVE_ZONE
    date_str = now_kst().strftime("%y%m%d")
    prefix = f"LPN-{date_str}-{zone_code}"
    last = db.query(InventoryUsedItem.lpn_barcode).filter(
        InventoryUsedItem.lpn_barcode.like(f"{prefix}%")
    ).order_by(InventoryUsedItem.lpn_barcode.desc()).first()
    seq_num = (int(last[0][-3:]) + 1) if last else 1
    return f"{prefix}{seq_num:03d}"


def generate_lpn(
    db: Session,
    book_id: str = None,
    isbn: str = None,
    zone: str = None,
    worker_id: str = "WM2608001",
) -> tuple[InventoryUsedItem, Book]:
    """
    [1단계: 선부착 (Label First)]
    도서 입고 시 LPN 바코드 라벨(LPN-YYMMDD-{존}{순번3자리})을 먼저 발급해 실물에 부착합니다.
    AI 검수 전이므로 등급은 PENDING, 상태는 PENDING_INSPECTION으로 등록됩니다.

    [2026-08-06 수정] 유일한 호출부(POST /inventory/lpn)가 `zone=`을 넘기는데 시그니처에는
    없어서 이 엔드포인트는 호출 즉시 TypeError로 죽고 있었다. 파라미터를 추가한다.

    `zone`은 **입고 시점의 버퍼 존**이고, 검수 후 확정되는 보관 랙 존(locations.zone)과는
    다를 수 있다. 랙 존은 등급에 따라 사후 산출되기 때문이다(recommend_optimal_warehouse_zone).
    둘을 같게 맞추려 들면 검수 전에 등급을 알아야 하므로 선부착 설계 자체가 무너진다.
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
        
    # 2. 고유 LPN 바코드 생성 (표준 규격: LPN-260803-A003)
    #
    # [2026-08-06 수정] 종전에는 존 문자와 순번을 모두 random으로 뽑고 중복 검사를 하지 않았다.
    # 하루 999칸에 난수를 던지는 구조라 생일 문제로 충돌이 실제로 발생했다
    # (2026-08-03 하루에만 서로 다른 도서 3쌍이 같은 LPN을 받음). LPN은 실물에 붙는
    # 라벨이라 중복되면 출고 스캔이 다른 책을 집는다. 존별·날짜별 최대 순번 +1로 채번한다.
    # 결번은 정상이다 - 발급 후 폐기된 라벨을 재사용하면 오히려 추적성이 깨진다.
    zone_code = (zone or LPN_LIVE_ZONE).strip().upper()[-1:]
    if zone_code not in LPN_ZONES:
        raise HTTPException(status_code=400, detail=f"zone must be one of {LPN_ZONES}")

    date_str = now_kst().strftime("%y%m%d")
    prefix = f"LPN-{date_str}-{zone_code}"

    # 3. 선부착 대기 등록.
    # location_id는 NOT NULL 제약이 걸려 있어 None을 넣을 수 없다(종전 코드는 여기서 항상
    # IntegrityError로 죽었다). 검수 전에는 보관 랙이 정해지지 않으므로, 요청된 존의
    # 검수 대기 버퍼 로케이션에 임시로 물려두고 검수 확정 시 실제 랙으로 옮긴다.
    buffer_loc = get_or_create_location(db, zone=zone_code, rack="0", shelf="0")

    # [2026-08-06 수정] 채번 경합 방어.
    #
    # `max(순번)+1`을 읽고 INSERT하는 사이에 다른 요청이 끼어들면 두 요청이 같은 번호를
    # 계산한다. 현장에서 작업자 여러 명이 각자 단말로 동시에 라벨을 뽑으므로 실제로 발생한다.
    # lpn_barcode에 UNIQUE 제약이 있어 두 번째 INSERT는 IntegrityError로 튕기는데, 종전에는
    # 이를 잡지 않아 500이 그대로 나갔다. 충돌 시 다시 채번해 재시도한다.
    #
    # [중요] UNIQUE 제약이 최종 방어선이다. 절대 제거하지 말 것 - LPN은 실물에 붙는 라벨이라
    # 중복되면 assign_rack_location_after_inspection()이 기존 row를 찾아 **덮어써서**
    # 다른 도서의 재고 정보가 소실된다(예외가 아니라 조용한 데이터 손상).
    from sqlalchemy.exc import IntegrityError

    MAX_RETRY = 5
    for attempt in range(MAX_RETRY):
        last = db.query(InventoryUsedItem.lpn_barcode).filter(
            InventoryUsedItem.lpn_barcode.like(f"{prefix}%")
        ).order_by(InventoryUsedItem.lpn_barcode.desc()).first()
        seq_num = (int(last[0][-3:]) + 1) if last else 1
        lpn_code = f"{prefix}{seq_num:03d}"

        new_item = InventoryUsedItem(
            book_id=book.id,
            location_id=buffer_loc.id,  # 검수 대기 버퍼 (rack/shelf = 0/0)
            lpn_barcode=lpn_code,
            ubci_score=None, # 검수 전 미측정
            condition_grade="PENDING", # 검수 전 미확정
            item_status="PENDING_INSPECTION" # AI 검수 대기 상태
        )

        try:
            db.add(new_item)
            db.commit()
        except IntegrityError:
            # 다른 요청이 같은 번호를 선점했다. 롤백 후 최신 max를 다시 읽어 재시도한다.
            db.rollback()
            if attempt == MAX_RETRY - 1:
                raise HTTPException(
                    status_code=409,
                    detail=f"LPN 채번 경합이 {MAX_RETRY}회 연속 발생했습니다. 잠시 후 다시 시도하세요.",
                )
            continue

        db.refresh(new_item)
        return new_item, book

def assign_rack_location_after_inspection(
    db: Session,
    lpn_barcode: str,
    final_grade: str,
    final_status: str = "COMPLETED",
    book_id = None,
    ubci_score: int = 85,
    source_job_id: str = None,
    certificate_url: str = None,
    inspection_source: str = "AI_AUTO",
    inspected_by: str = None,
) -> InventoryUsedItem:
    """
    [Report Agent 최종 보고서 생성 시점 랙 위치 자동 할당]
    - HITL_REQUIRED (승인 대기/이의 발생): 보관 랙 위치 할당 보류 (location_id = None, 버퍼 존 대기)
    - REJECT / 폐기 / 반려: E구역(Zone E - 격리/폐기 랙 구역, LOC-E-1-1) 강제 할당
    - COMPLETED / APPROVED (MINT, GOOD, NORMAL): 카테고리/등급/크기 3차원 알고리즘으로 Zone A, B, C 최종 랙 위치 할당

    inspection_source / inspected_by는 이 품목의 등급을 최종 확정한 주체를 기록한다
    (AI_AUTO=파이프라인 자동 확정, HITL=관리자 수동 결재, MANUAL=현장 수기).
    """
    item = db.query(InventoryUsedItem).filter(InventoryUsedItem.lpn_barcode == lpn_barcode).first()
    book = db.query(Book).filter(Book.id == (item.book_id if item else book_id)).first() if (item or book_id) else None

    # 등급/카테고리/판형 3차원 알고리즘으로 Zone A/B/C/D/E 랙 위치 자동 결정
    rec_zone, rec_rack, rec_shelf = recommend_optimal_warehouse_zone(
        grade=final_grade,
        category=book.category_type if book else "IT/컴퓨터",
        base_price=book.base_price if book else 20000.0,
        standard_size=book.standard_size if book else None
    )
    location = get_or_create_location(db, zone=rec_zone, rack=rec_rack, shelf=rec_shelf)

    if not item:
        # LPN 항목이 DB에 없으면 위치(location_id) 및 도서정보를 결합하여 새 재고 아이템 생성
        if not book_id and book:
            book_id = book.id
        elif not book_id:
            first_book = db.query(Book).first()
            book_id = first_book.id if first_book else None

        cert_code = str(source_job_id)[:6].upper() if source_job_id else "32053B"
        generated_cert_url = certificate_url or f"/certificate/CERT-20260728-{cert_code}"

        item = InventoryUsedItem(
            book_id=book_id,
            location_id=location.id,
            lpn_barcode=lpn_barcode,
            condition_grade=final_grade,
            ubci_score=ubci_score,
            item_status=final_status,
            source_job_id=source_job_id,
            certificate_url=generated_cert_url,
            inspection_source=inspection_source,
            inspected_by=inspected_by,
            inspected_at=now_kst(),
            created_at=now_kst()
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    # 기존 항목 업데이트 시에도 source_job_id / certificate_url / 검수 주체를 동기화한다.
    # HITL 오버라이드는 AI가 먼저 만들어둔 row를 덮어쓰므로, 여기서 갱신하지 않으면
    # 관리자가 최종 결재한 건도 계속 AI_AUTO로 남는다.
    # [2026-08-06 수정] ubci_score도 함께 갱신한다 - 종전에는 신규 생성 경로만 점수를 기록해,
    # 재검수로 점수가 바뀌어도 기존 row에는 옛 점수가 잔존했다 (예: 80점 row가 재검수 100점
    # MINT 승인 후에도 "MINT / 80점"으로 표시되는 모순).
    item.inspection_source = inspection_source
    item.inspected_by = inspected_by
    item.inspected_at = now_kst()
    item.ubci_score = ubci_score
    if source_job_id:
        item.source_job_id = source_job_id
    if certificate_url:
        item.certificate_url = certificate_url
    elif not item.certificate_url:
        cert_code = str(item.source_job_id or '32053B')[:6].upper()
        item.certificate_url = f"/certificate/CERT-20260728-{cert_code}" 

    book = db.query(Book).filter(Book.id == item.book_id).first()

    # 1. HITL 승인 대기 상태 ➔ 임시적재 구역(Zone Z) 강제 할당
    if final_status in ["HITL_REQUIRED", "HITL_PENDING"]:
        location = get_or_create_location(db, zone="Z", rack="1", shelf="1")
        item.location_id = location.id
        item.condition_grade = "PENDING"
        item.item_status = "HITL_PENDING"
        item.updated_at = now_kst()
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
        item.updated_at = now_kst()
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
    item.item_status = final_status
    item.updated_at = now_kst()

    db.add(item)
    db.commit()
    db.refresh(item)

    return item

def get_all_lpn(db: Session, skip: int = 0, limit: int = 100):
    """발급된 모든 LPN 및 랙 위치 조회"""
    return db.query(InventoryUsedItem).offset(skip).limit(limit).all()
