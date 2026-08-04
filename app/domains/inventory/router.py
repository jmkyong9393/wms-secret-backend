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
        dt = datetime.now()
    if dt.tzinfo is not None:
        return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def to_browser_image_urls(raw_urls: Optional[List[str]]) -> List[str]:
    """
    DB에 적재된 이미지 경로를 브라우저가 실제로 열 수 있는 URL로 정규화한다.

    [수정 이력] 과거 입고 건은 image_urls에 컨테이너 절대경로
    (/app/app/experiment_data/job-xxx/raw_0.jpg)가 그대로 들어가 있다. 프론트가 이 값을
    <img src>에 꽂으면 http://localhost:3000/app/app/... 으로 해석되어 100% 404가 났고,
    상세페이지 검수 이미지가 한 장도 뜨지 않던 직접 원인이었다. 신규 건은 인바운드
    라우터가 CloudFront URL을 넣지만, 기존 데이터를 위해 여기서도 방어적으로 변환한다.
    """
    if not raw_urls:
        return []

    from app.core.config import settings

    api_base = (getattr(settings, "PUBLIC_API_BASE_URL", "") or "http://localhost:8000").rstrip("/")

    normalized: List[str] = []
    for url in raw_urls:
        if not url or not isinstance(url, str):
            continue
        if url.startswith("http://") or url.startswith("https://"):
            normalized.append(url)
            continue
        # 컨테이너/로컬 파일 경로 -> StaticFiles 마운트(/experiment_data) 기준 공개 URL로 환원
        marker = "experiment_data"
        if marker in url.replace("\\", "/"):
            tail = url.replace("\\", "/").split(marker, 1)[1].lstrip("/")
            normalized.append(f"{api_base}/{marker}/{tail}")
        elif url.startswith("/"):
            normalized.append(f"{api_base}{url}")
        else:
            normalized.append(f"{api_base}/{url}")
    return normalized


def resolve_inspector(item: Optional[Any], job: Optional[Any]) -> Dict[str, Any]:
    """
    이 품목의 등급을 최종 확정한 주체를 반환한다.

    [수정 이력] 이전에는 라우터가 "WM2608001" / "HITL - WM2608001 (장문경)" 문자열을
    응답에 하드코딩해, 어떤 건이든 항상 같은 담당자로 표시됐다. 이제 실제 확정 주체
    (AI 자동 판정 / HITL 결재 관리자 / 현장 수기)를 DB 기록에서 읽어 내려준다.
    """
    source = getattr(item, "inspection_source", None) or "AI_AUTO"
    inspected_by = getattr(item, "inspected_by", None)
    inbound_worker = ((job.agent_logs or {}) if job else {}).get("inbound_worker_id")

    if source == "HITL":
        label = f"HITL - {inspected_by}" if inspected_by else "HITL - 관리자 결재"
    elif source == "PENDING_HITL":
        label = "HITL 결재 대기"
    elif source == "MANUAL":
        label = inspected_by or "현장 수기 검수"
    else:
        label = inspected_by or "AI 자동 판정 (Nexus Vision AI)"

    return {
        "inspection_source": source,
        "inspected_by": inspected_by,
        "inspected_at": to_kst_str(getattr(item, "inspected_at", None)) if getattr(item, "inspected_at", None) else None,
        # 입고 촬영을 실제로 수행한 작업자 (등급 확정 주체와 다를 수 있어 별도 노출)
        "inbound_worker_id": inbound_worker,
        "label": label,
    }

@router.get("/")
def get_inventory(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """
    프론트엔드 DataGrid에 출력하기 위한 DB 실재 재고(중고 + 패스트트랙 신품) 통합 목록을 조회합니다 (KST 변환).
    """
    from app.models.wms import Inventory

    output = []

    # 1. 패스트트랙 신품 재고 (Inventory 테이블) 조회
    new_inv_stmt = (
        select(Inventory, Book, Location)
        .outerjoin(Book, Inventory.book_id == Book.id)
        .outerjoin(Location, Inventory.location_id == Location.id)
    )
    new_results = db.exec(new_inv_stmt).all()
    for inv, book, loc in new_results:
        zone_str = f"{loc.zone}-{loc.rack}-{loc.shelf}" if loc else "Zone-A-4-2 (신품존)"
        cover_url = book.cover_image_url if (book and book.cover_image_url) else ""
        output.append({
            "id": str(inv.id),
            "lpn_barcode": "LPN 미발급 (신품)",
            "cover_image_url": cover_url,
            "book": {
                "title": book.title if book else "도서 정보 없음",
                "author": book.author if book else "-",
                "publisher": book.publisher if book else "-",
                "isbn": book.isbn if book else "-",
                "base_price": book.base_price if book else 0.0,
                "cover_image_url": cover_url,
            },
            "grade": "NEW_FASTTRACK",
            "ubci_score": None,
            "zone": zone_str,
            "quantity": inv.quantity,
            # 신품 Fast-track은 AI 비전 검수를 타지 않는다(출판사 직송 무검수 입고).
            "worker_id": "신품 Fast-track (무검수 입고)",
            "date": to_kst_str(inv.updated_at or inv.created_at)
        })

    # 2. 중고/반품 검수 LPN 품목 (InventoryUsedItem 테이블) 조회
    used_stmt = (
        select(InventoryUsedItem, Book, Location)
        .outerjoin(Book, InventoryUsedItem.book_id == Book.id)
        .outerjoin(Location, InventoryUsedItem.location_id == Location.id)
    )
    used_results = db.exec(used_stmt).all()
    for item, book, loc in used_results:
        zone_str = f"{loc.zone}-{loc.rack}-{loc.shelf}" if loc else "검수대기 (미할당)"
        cover_url = book.cover_image_url if (book and book.cover_image_url) else ""
        output.append({
            "id": str(item.id),
            "lpn_barcode": item.lpn_barcode,
            "cover_image_url": cover_url,
            "book": {
                "title": book.title if book else "도서 정보 없음",
                "author": book.author if book else "-",
                "publisher": book.publisher if book else "-",
                "isbn": book.isbn if book else "-",
                "base_price": book.base_price if book else 0.0,
                "cover_image_url": cover_url,
            },
            "grade": item.condition_grade if item.condition_grade else ("MINT" if (item.ubci_score or 85) >= 95 else "GOOD"),
            "ubci_score": item.ubci_score if item.ubci_score is not None else 85,
            "zone": zone_str,
            "quantity": 1,
            # 하드코딩 상수 대신 실제 등급 확정 주체(AI 자동 판정 / HITL 결재자)를 내려준다.
            "worker_id": resolve_inspector(item, None)["label"],
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
    재고 개별 상세 정보 (신품 Inventory 또는 중고 InventoryUsedItem) 조회
    """
    from uuid import UUID
    from app.models.wms import ReturnJob, Inventory, Book, Location

    # 1. 신품 Inventory 테이블 검색 (UUID, string ID, ISBN 순차 검색)
    new_inv = None
    try:
        parsed_id = UUID(item_id)
        new_inv = db.query(Inventory).filter(Inventory.id == parsed_id).first()
    except Exception:
        pass

    if not new_inv:
        # String ID or ISBN matching
        new_invs = db.query(Inventory).all()
        for inv in new_invs:
            if str(inv.id) == item_id:
                new_inv = inv
                break
            b = db.query(Book).filter(Book.id == inv.book_id).first()
            if b and b.isbn == item_id:
                new_inv = inv
                break

    if new_inv:
        book = db.query(Book).filter(Book.id == new_inv.book_id).first()
        loc = db.query(Location).filter(Location.id == new_inv.location_id).first()
        zone_str = f"{loc.zone}-{loc.rack}-{loc.shelf}" if loc else "Zone-A-4-2 (신품존)"
        return {
            "id": str(new_inv.id),
            "lpn_barcode": "LPN 미발급 (신품)",
            "book": {
                "title": book.title if book else "신품 도서",
                "author": book.author if book else "출판사 직송",
                "publisher": book.publisher if book else "-",
                "isbn": book.isbn if book else "9791185553658",
                "base_price": book.base_price if book else 22000.0,
                "cover_image_url": (book.cover_image_url if (book and book.cover_image_url) else f"https://contents.kyobobook.co.kr/s3mh/BJCMD/B000000000000_{book.isbn if book else '9791185553658'}.jpg"),
            },
            "grade": "NEW_FASTTRACK",
            "ubci_score": None,
            "zone": zone_str,
            "quantity": new_inv.quantity or 1,
            "worker_id": "신품 Fast-track (무검수 입고)",
            "inspector": {
                "inspection_source": "NEW_FASTTRACK",
                "inspected_by": None,
                "inspected_at": None,
                "inbound_worker_id": None,
                "label": "신품 Fast-track (무검수 입고)",
            },
            "date": to_kst_str(new_inv.updated_at or new_inv.created_at),
            "image_urls": [],
            "agent_logs": {},
            "final_report": None,
            "certificate": None,
        }

    # 2. 중고 InventoryUsedItem 테이블 검색
    # [수정 이력] item을 미리 None으로 초기화하지 않아, item_id가 UUID가 아닌 경우(= LPN
    # 바코드로 조회하는 경우, 즉 QR 보증서 페이지의 기본 경로) UUID() 파싱이 예외를 던지면서
    # item이 아예 정의되지 않았고, 바로 다음 `if not item:`에서 UnboundLocalError가 발생해
    # 500이 났다. LPN으로 조회하는 모든 요청이 100% 실패하던 버그.
    item = None
    try:
        parsed_id = UUID(item_id)
        item = db.query(InventoryUsedItem).filter(InventoryUsedItem.id == parsed_id).first()
    except Exception:
        pass

    if not item:
        item = db.query(InventoryUsedItem).filter(InventoryUsedItem.lpn_barcode == item_id).first()

    if not item:
        # [수정 이력] ReturnJob에는 lpn_barcode 컬럼이 없다(LPN은 agent_logs JSONB 안에 있음).
        # 기존 코드는 존재하지 않는 ReturnJob.lpn_barcode로 필터링해 AttributeError를 냈다.
        # JSONB 내부 키로 조회하도록 교정.
        job = None
        try:
            parsed_id = UUID(item_id)
            job = db.query(ReturnJob).filter(ReturnJob.id == parsed_id).first()
        except Exception:
            pass

        if not job:
            job = (
                db.query(ReturnJob)
                .filter(ReturnJob.agent_logs["lpn_barcode"].astext == item_id)
                .first()
            )

        if job:
            book = db.query(Book).filter(Book.id == job.book_id).first()
            return {
                "id": str(job.id),
                "lpn_barcode": (job.agent_logs or {}).get("lpn_barcode") or "LPN-PENDING",
                "book": {
                    "title": book.title if book else "알 수 없는 도서",
                    "author": book.author if book else "-",
                    "publisher": book.publisher if book else "-",
                    "isbn": book.isbn if book else "-",
                    "base_price": book.base_price if book else 0.0,
                    "cover_image_url": book.cover_image_url if book else "",
                },
                "grade": job.agent_logs.get("suggested_grade") if (job.agent_logs and job.agent_logs.get("suggested_grade")) else "NORMAL",
                "ubci_score": job.ubci_score or 75,
                "zone": "Zone Z (임시적재)",
                "quantity": 1,
                "worker_id": "HITL 결재 대기",
                "inspector": {
                    "inspection_source": "PENDING_HITL",
                    "inspected_by": None,
                    "inspected_at": None,
                    "inbound_worker_id": (job.agent_logs or {}).get("inbound_worker_id"),
                    "label": "HITL 결재 대기",
                },
                "date": to_kst_str(job.created_at),
                "image_urls": to_browser_image_urls(job.image_urls),
                "agent_logs": job.agent_logs or {},
                "final_report": job.final_report,
                "certificate": (job.agent_logs or {}).get("certificate"),
            }

        # [수정 이력] 여기서 `item = db.query(InventoryUsedItem).first()`로 폴백하고 있었다.
        # 존재하지 않는 LPN/ID를 조회해도 404가 아니라 "DB의 아무 재고 한 건"을 200으로
        # 반환해버려서, 전혀 다른 도서의 이미지/등급/에이전트 로그가 남의 상세페이지에
        # 표시됐다. 데이터 정합성을 깨는 폴백이므로 정직하게 404를 낸다.
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"재고를 찾을 수 없습니다: {item_id}")

    book = db.query(Book).filter(Book.id == item.book_id).first()
    loc = db.query(Location).filter(Location.id == item.location_id).first() if item.location_id else None

    # [수정 이력] source_job_id가 없을 때 `db.query(ReturnJob).first()`로 아무 검수 작업이나
    # 끌어와 그 이미지와 agent_logs를 이 품목의 것인 양 붙여주고 있었다. 남의 검수 결과가
    # 표시되는 원인이므로, 연결된 작업이 없으면 비워둔다.
    job = db.query(ReturnJob).filter(ReturnJob.id == item.source_job_id).first() if item.source_job_id else None

    zone_str = f"{loc.zone}-{loc.rack}-{loc.shelf}" if loc else "검수대기 (미할당)"
    agent_logs = (job.agent_logs if job else {}) or {}
    inspector = resolve_inspector(item, job)

    # [수정 이력] 가격을 프론트가 `정가 × UBCI/100`으로 직접 계산하고 있었다. UBCI 100점(MINT)
    # 이면 계수가 1.0이라 **중고 판매가가 신품 정가와 완전히 동일**하게 표시됐고(정가 20,000원 /
    # 중고 판매가 20,000원), 카테고리별 차등도 전혀 반영되지 않았다.
    # 산정 책임을 백엔드 단일 엔진(orders/pricing.py)으로 옮기고 근거까지 함께 내려준다.
    from app.domains.orders.pricing import build_pricing_breakdown

    days_in_inventory = 0
    if item.created_at:
        days_in_inventory = max(0, (datetime.now() - item.created_at).days)

    pricing = build_pricing_breakdown(
        list_price=book.base_price if book else 0.0,
        category=book.category_type if book else None,
        ubci_score=item.ubci_score,
        days_in_inventory=days_in_inventory,
        # 절판/한정판 등 희소성 프리미엄 판정에 쓰인다 (명세 §3 description_premium)
        description=book.description if book else None,
        title=book.title if book else None,
    )

    return {
        "id": str(item.id),
        "lpn_barcode": item.lpn_barcode,
        "book": {
            "title": book.title if book else "도서 정보 없음",
            "author": book.author if book else "-",
            "publisher": book.publisher if book else "-",
            "isbn": book.isbn if book else "-",
            "base_price": book.base_price if book else 0.0,
            "cover_image_url": book.cover_image_url if book else "",
        },
        "grade": item.condition_grade,
        "ubci_score": item.ubci_score,
        "zone": zone_str,
        "quantity": 1,
        "worker_id": inspector["label"],
        "inspector": inspector,
        # 카테고리별 차등이 적용된 가격 산정 내역 (프론트는 렌더만 한다)
        "pricing": pricing,
        "date": to_kst_str(item.created_at),
        # 컨테이너 절대경로가 아니라 브라우저가 실제로 열 수 있는 URL로 정규화해 내려준다.
        "image_urls": to_browser_image_urls(job.image_urls if job else []),
        "agent_logs": agent_logs,
        # Report Agent가 생성한 고객 공개용 보증서 본문. 프론트가 등급별 문장을
        # 하드코딩하지 않고 이 값을 그대로 렌더한다.
        "final_report": job.final_report if job else None,
        "certificate": agent_logs.get("certificate"),
    }
