import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlmodel import select

from app.core.security import RoleChecker, get_current_user
from app.db.session import get_db
from app.domains.inventory import read_service
from app.domains.inventory.service import generate_lpn, get_all_lpn
from app.models.wms import InventoryUsedItem, UserRoleEnum

logger = logging.getLogger(__name__)

# Inventory 도메인 라우터: 새 상품 및 중고/반품 도서들의 통합 재고 관리를 담당합니다.
# 조회 응답 조립(통합 목록·상세)은 read_service, LPN 채번·랙 배정은 service가 담당한다
# (2026-09-01 거대 모듈 분리 - 893줄 중 조회 2종을 이동).
# 라우터 전체에 인증을 건다. 엔드포인트마다 붙이면 새 경로를 추가할 때 또 빠뜨린다 -
# 실제로 재고·피킹지시서·발주제안이 무인증으로 조회되던 것을 전수 점검에서 발견했다.
router = APIRouter(
    prefix="/inventory", tags=["Inventory"], dependencies=[Depends(get_current_user)]
)

# 하드 삭제는 관리자 전용 (MASTER/ADMIN)
_admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])


# 끝 슬래시 유무를 모두 직접 받는다 (리다이렉트 금지).
#
# 이 엔드포인트는 `/inventory/` 하나만 등록되어 있어서, 슬래시 없이
# 들어온 요청에 FastAPI가 307 리다이렉트를 응답했다. 문제는 그 Location이
# **절대 URL(`http://localhost:8000/...`)** 이라는 점이다.
#
# 프론트는 `/api/*`를 Next rewrites로 프록시하는데 이 과정에서 끝 슬래시가 탈락한다.
# 그 결과 외부(터널/다른 기기)에서 접속한 브라우저는 307을 따라 **접속자 본인 PC의
# 8000 포트**로 향하게 되고(게다가 https 페이지에서 http라 mixed content 차단),
# 요청이 조용히 실패해 **재고 목록이 0건으로 표시**됐다. DB에는 데이터가 멀쩡히 있었다.
#
# 프록시가 슬래시를 어떻게 다루든 무관하도록 두 경로를 모두 직접 서빙한다.
@router.get("")
@router.get("/")
def get_inventory(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """DB 실재 재고(중고 + 패스트트랙 신품) 통합 목록 (KST 변환). 조립은 read_service."""
    return read_service.get_inventory(db)


class CreateLpnRequest(BaseModel):
    book_id: Optional[str] = None
    isbn: Optional[str] = None
    worker_id: Optional[str] = None
    zone: Optional[str] = None  # Zone A, B, C, D, E


@router.post("/lpn")
async def create_lpn(req: CreateLpnRequest, db: Session = Depends(get_db)):
    """
    새로운 LPN 바코드를 발급하고 검수 대기 버퍼 로케이션에 선부착 등록합니다.

    LPN 채번을 프론트에서 백엔드로 이관하면서, 이 엔드포인트가 **바코드
    스캔 직후** 호출되게 되었다. 그 시점에는 아직 books 행이 없어(입고 확정 전) 종전
    구현은 404 "Book not found"로 죽는다. LPN 발급은 선부착 설계상 검수보다 먼저여야
    하므로, 도서가 없으면 알라딘 조회로 최소 메타데이터를 만들어 등록한다.
    (`/inbound/fasttrack`이 이미 쓰는 것과 동일한 패턴)
    """
    from app.domains.inbound.service import (
        UNKNOWN_BOOK_TITLE,
        book_row_to_lookup_payload,
        is_placeholder_book,
        lookup_book_by_isbn,
    )
    from app.models.wms import Book

    isbn = (req.isbn or "").strip()

    if not req.book_id and isbn:
        book = db.exec(select(Book).where(Book.isbn == isbn)).first()

        # 신규 ISBN이거나 자리표시자("미확인 도서") 행이면 알라딘을 조회해 생성·보강한다.
        # 자리표시자를 그대로 두면 한 번의 조회 실패가 그 ISBN에 영구히 남는다.
        if book is None or is_placeholder_book(book):
            meta = await lookup_book_by_isbn(isbn) or {}
            category_name = meta.get("categoryName", "")
            parts = [p.strip() for p in category_name.split(">") if p.strip()]
            parsed_category = (
                parts[1] if len(parts) > 1 else (parts[0] if parts else "GENERAL")
            )

            book_kwargs: Dict[str, Any] = dict(
                isbn=isbn,
                title=meta.get("title") or UNKNOWN_BOOK_TITLE,
                author=meta.get("author"),
                publisher=meta.get("publisher"),
                published_date=meta.get("pubDate"),
                base_price=float(meta.get("price", 0.0) or 0.0),
                description=meta.get("description"),
                cover_image_url=meta.get("imageUrl"),
                category_type=parsed_category,
            )
            for field in (
                "width_mm",
                "depth_mm",
                "thickness_mm",
                "weight_g",
                "page_count",
            ):
                if meta.get(field) is not None:
                    book_kwargs[field] = meta[field]

            if book is None:
                db.add(Book(**book_kwargs))
                db.commit()
            elif meta:
                # 조회가 또 실패했으면(meta 비어 있음) 기존 값을 지우지 않도록 건드리지 않는다.
                for key, value in book_kwargs.items():
                    if key != "isbn":
                        setattr(book, key, value)
                db.add(book)
                db.commit()

    # 종전에는 req.worker_id를 응답으로 되돌려줄 뿐 저장하지 않아,
    # 검수 전(PENDING_INSPECTION) 품목의 작업자가 어디에도 남지 않았다.
    new_lpn, book = generate_lpn(
        db, book_id=req.book_id, isbn=req.isbn, zone=req.zone, worker_id=req.worker_id
    )

    # 표지·정가·규격까지 포함한 전체 메타를 함께 내려, 프론트가 별도 도서 조회 없이
    # 채번 응답만으로 화면을 채울 수 있게 한다.
    return {
        "status": "success",
        "lpn_barcode": new_lpn.lpn_barcode,
        "book": book_row_to_lookup_payload(book),
        "location_id": str(new_lpn.location_id),
        "worker_id": req.worker_id,
    }


@router.delete("/lpn/{lpn_barcode}")
async def cancel_lpn(lpn_barcode: str, db: Session = Depends(get_db)):
    """
    라벨 인쇄 전에 되돌아간 미부착 LPN 채번을 취소(회수)한다. 유령 LPN 적재를 막는 경로다.

    아래를 모두 만족할 때만 삭제하고, 하나라도 어긋나면 409로 거절한다.
      - item_status == PENDING_INSPECTION  (검수·입고가 진행된 적 없음)
      - source_job_id is None              (검수 원장에 연결된 적 없음)
      - 해당 LPN으로 접수된 ReturnJob 없음 (촬영본이 큐에 들어가 있지 않음)

    삭제분이 그날 마지막 번호였다면 다음 채번이 같은 번호를 재발급한다. 라벨이 인쇄되지
    않은 경우로 한정되므로 안전하다. 채번 규칙(max+1) 자체는 바꾸지 않는다 - 인쇄·부착된
    라벨의 결번을 재사용하면 서로 다른 실물이 같은 번호를 갖는다.
    """
    from app.models.wms import ReturnJob

    item = db.exec(
        select(InventoryUsedItem).where(InventoryUsedItem.lpn_barcode == lpn_barcode)
    ).first()
    if not item:
        # 뒤로가기가 중복 호출될 수 있어 멱등 처리한다.
        return {"status": "not_found", "lpn_barcode": lpn_barcode, "deleted": False}

    if item.item_status != "PENDING_INSPECTION" or item.source_job_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"이미 처리된 LPN이라 취소할 수 없습니다 (상태: {item.item_status}).",
        )

    linked_job = db.exec(
        select(ReturnJob).where(
            ReturnJob.agent_logs["lpn_barcode"].astext == lpn_barcode
        )
    ).first()
    if linked_job:
        raise HTTPException(
            status_code=409,
            detail="이미 AI 검수 큐에 접수된 LPN이라 취소할 수 없습니다.",
        )

    db.delete(item)
    db.commit()
    logger.info(f"[LPN] 미부착 채번 취소: {lpn_barcode}")
    return {"status": "success", "lpn_barcode": lpn_barcode, "deleted": True}


@router.delete("/items/{row_id}")
def hard_delete_inventory_row(
    row_id: UUID, db: Session = Depends(get_db), current_admin=Depends(_admin_only)
):
    """
    재고 행 하드 삭제 (관리자 전용) - 시연·오입고 건을 흔적 없이 리셋하는 캐스케이드.

    검수 이력(ReturnJob)·원장(InventoryLog)·알림까지 함께 지운다. 테이블 4개를 순서 맞춰
    지워야 해서 DBeaver 수작업이 고통스럽던 것을 API 한 번으로 대체한다 (조장 결정:
    시연 리셋이 지배 시나리오이므로 소프트 삭제 대신 하드 삭제).

    단 하나의 가드: 피킹·출고에 물린 건(ALLOCATED/SHIPPED)은 지시서 스냅샷이 깨지므로 409.

    row_id는 재고 목록 행의 id를 그대로 받는다 - 중고는 InventoryUsedItem.id,
    신품은 Inventory.id (프론트가 구분 없이 넘길 수 있게 양쪽을 순서대로 조회).
    """
    from app.models.wms import Inventory, InventoryLog, Notification, ReturnJob

    deleted = {
        "used_item": 0,
        "new_inventory": 0,
        "jobs": 0,
        "logs": 0,
        "notifications": 0,
    }

    # 1) 중고 LPN 낱권
    item = db.get(InventoryUsedItem, row_id)
    if item:
        if item.item_status in ("ALLOCATED", "SHIPPED"):
            raise HTTPException(
                status_code=409,
                detail=f"피킹/출고에 연결된 재고라 삭제할 수 없습니다 (상태: {item.item_status}). "
                f"해당 지시서를 먼저 취소해 주세요.",
            )
        lpn = item.lpn_barcode

        jobs = db.exec(
            select(ReturnJob).where(ReturnJob.agent_logs["lpn_barcode"].astext == lpn)
        ).all()
        if item.source_job_id:
            src = db.get(ReturnJob, item.source_job_id)
            if src and src not in jobs:
                jobs.append(src)

        job_ids = [str(j.id) for j in jobs]
        if job_ids:
            for n in db.exec(
                select(Notification).where(
                    Notification.ref_type == "RETURN_JOB",
                    Notification.ref_id.in_(job_ids),
                )
            ).all():
                db.delete(n)
                deleted["notifications"] += 1

        for lg in db.exec(
            select(InventoryLog).where(InventoryLog.target_lpn == lpn)
        ).all():
            db.delete(lg)
            deleted["logs"] += 1

        db.delete(item)  # source_job_id FK 때문에 job보다 먼저 지운다
        deleted["used_item"] = 1
        for j in jobs:
            db.delete(j)
            deleted["jobs"] += 1

        db.commit()
        logger.info(
            f"[재고 하드삭제] {lpn}: {deleted} (by {getattr(current_admin, 'employee_id', '?')})"
        )
        return {"status": "success", "lpn_barcode": lpn, "deleted": deleted}

    # 2) 신품 Fast-track 묶음 재고
    inv = db.get(Inventory, row_id)
    if inv:
        book_id = inv.book_id
        db.delete(inv)
        deleted["new_inventory"] = 1

        # 이 책의 신품 재고가 0이 되면 입고 원장도 함께 지워 검수 내역 목록에서 사라지게 한다.
        # 다른 위치에 재고가 남아 있으면 원장은 이력이므로 보존한다.
        remaining = db.exec(
            select(Inventory).where(
                Inventory.book_id == book_id, Inventory.id != row_id
            )
        ).all()
        if not remaining:
            for lg in db.exec(
                select(InventoryLog).where(
                    InventoryLog.book_id == book_id,
                    InventoryLog.transaction_type == "INBOUND",
                    InventoryLog.condition_grade == "NEW",
                )
            ).all():
                db.delete(lg)
                deleted["logs"] += 1

        db.commit()
        logger.info(
            f"[재고 하드삭제] 신품 book={book_id}: {deleted} (by {getattr(current_admin, 'employee_id', '?')})"
        )
        return {"status": "success", "book_id": str(book_id), "deleted": deleted}

    raise HTTPException(status_code=404, detail=f"재고 행을 찾을 수 없습니다: {row_id}")


@router.get("/lpn")
async def get_lpn_list(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """발급된 모든 LPN 내역을 조회합니다 (프론트 대시보드 연동용)."""
    lpns = get_all_lpn(db, skip=skip, limit=limit)
    return [
        {"lpn_barcode": l.lpn_barcode, "book_id": l.book_id, "status": l.item_status}
        for l in lpns
    ]


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
        "message": f"3D 패킹 연산 결과 최적 포장 상자: [{recommended_box}] 추천 완료",
    }


@router.get("/{item_id}")
def get_inventory_detail(item_id: str, db: Session = Depends(get_db)):
    """재고 개별 상세 (신품 Inventory 또는 중고 InventoryUsedItem). 조립은 read_service."""
    return read_service.get_inventory_detail(db, item_id)
