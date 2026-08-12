from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any, Optional
from datetime import datetime
from uuid import UUID
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.domains.inventory.service import generate_lpn, get_all_lpn

# Inventory 도메인 라우터: 새 상품 및 중고/반품 도서들의 통합 재고 관리를 담당합니다.
router = APIRouter(prefix="/inventory", tags=["Inventory"])

# 하드 삭제는 관리자 전용 (MASTER/ADMIN)
from app.core.security import RoleChecker
from app.models.wms import UserRoleEnum

_admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])


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


# 등급 확정 경로(트랙) 표기. 목록에서는 이 짧은 값만 쓰고, 근거 서술은 상세 화면이 맡는다.
# [2026-08-10 신설] 종전 목록은 "Nexus Vision AI (LangGraph 4-Agent)" 같은 긴 문자열 하나로
# 작업자 칸을 채워, **실제 검수를 수행한 사람이 화면 어디에도 나오지 않았다**. 사람(작업자)과
# 확정 경로(트랙)는 서로 다른 정보이므로 분리해 내려준다.
_TRACK_BY_SOURCE = {
    "HITL": "HITL",
    "PENDING_HITL": "HITL 대기",
    "MANUAL": "수기",
    "AI_AUTO": "AI",
}


def resolve_track(inspection_source: Optional[str]) -> str:
    return _TRACK_BY_SOURCE.get((inspection_source or "AI_AUTO").upper(), "AI")


# 작업자 표기 정본은 app/core/constants.py 한 곳에서만 만든다 (형식이 갈리는 것을 막는다).
from app.core.constants import format_worker_label


def resolve_inspector(item: Optional[Any], job: Optional[Any]) -> Dict[str, Any]:
    """
    이 품목의 등급을 최종 확정한 주체를 반환한다.

    [수정 이력] 이전에는 라우터가 "WM2608001" / "HITL - WM2608001 (장문경)" 문자열을
    응답에 하드코딩해, 어떤 건이든 항상 같은 담당자로 표시됐다. 이제 실제 확정 주체
    (AI 자동 판정 / HITL 결재 관리자 / 현장 수기)를 DB 기록에서 읽어 내려준다.
    """
    source = getattr(item, "inspection_source", None) or "AI_AUTO"
    inspected_by = getattr(item, "inspected_by", None)
    # 검수 접수 기록(job)이 없거나 기록 도입 전 레거시면 선부착 작업자로 폴백한다.
    inbound_worker = ((job.agent_logs or {}) if job else {}).get(
        "inbound_worker_id"
    ) or getattr(item, "prelabel_worker_id", None)

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

# 끝 슬래시 유무를 모두 직접 받는다 (리다이렉트 금지).
#
# [수정 이력 2026-08-06] 이 엔드포인트는 `/inventory/` 하나만 등록되어 있어서, 슬래시 없이
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

    # 신품 입고 작업자. 신품은 ReturnJob을 타지 않으므로 입고 원장(InventoryLog)에서 읽는다.
    # 같은 책이 여러 번 입고되면 가장 최근 입고를 그 재고 행의 작업자로 본다.
    from app.models.wms import InventoryLog, User as _User

    new_worker_by_book: Dict[Any, str] = {}
    new_book_ids = [inv.book_id for inv, _b, _l in new_results if inv.book_id]
    if new_book_ids:
        log_rows = db.exec(
            select(InventoryLog)
            .where(
                InventoryLog.transaction_type == "INBOUND",
                InventoryLog.condition_grade == "NEW",
                InventoryLog.book_id.in_(set(new_book_ids)),
                InventoryLog.worker_id.is_not(None),
            )
            .order_by(InventoryLog.created_at.desc())
        ).all()
        for lg in log_rows:
            new_worker_by_book.setdefault(lg.book_id, lg.worker_id)  # 최신순이라 첫 값이 최근

    new_name_by_emp: Dict[str, str] = {}
    if new_worker_by_book:
        for u in db.exec(
            select(_User).where(_User.employee_id.in_(set(new_worker_by_book.values())))
        ).all():
            new_name_by_emp[u.employee_id] = u.name

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
            # 입고 원장(InventoryLog.worker_id)에서 읽은 실제 입고 작업자.
            # 마이그레이션 이전에 입고된 행은 기록이 없어 미기록으로 표기된다.
            "worker_label": format_worker_label(
                new_worker_by_book.get(inv.book_id),
                new_name_by_emp.get(new_worker_by_book.get(inv.book_id) or ""),
            ),
            "track": "신품",
            "worker_id": "신품 Fast-track (무검수 입고)",
            "date": to_kst_str(inv.updated_at or inv.created_at)
        })

    # 2. 중고/반품 검수 LPN 품목 (InventoryUsedItem 테이블) 조회
    #
    # 검수가 끝나 랙에 적재된 품목만 재고 현황에 넣는다. 아래 상태는 실물 추적용 row는
    # 있지만 아직 등급이 확정되지 않았으므로 제외한다.
    #   - PENDING_INSPECTION : LPN 라벨만 선부착된 상태 (버퍼 로케이션, 등급 PENDING)
    #   - HITL_PENDING / HITL_REQUIRED : 사람 결재 대기. HITL 대시보드가 전담한다.
    # item_status가 NULL인 레거시 row는 노출을 유지한다 (NOT IN의 NULL 삼단논리 주의).
    from sqlalchemy import or_
    NOT_YET_STOCKED = ["HITL_PENDING", "HITL_REQUIRED", "PENDING_INSPECTION"]
    used_stmt = (
        select(InventoryUsedItem, Book, Location)
        .outerjoin(Book, InventoryUsedItem.book_id == Book.id)
        .outerjoin(Location, InventoryUsedItem.location_id == Location.id)
        .where(
            or_(
                InventoryUsedItem.item_status.is_(None),
                InventoryUsedItem.item_status.notin_(NOT_YET_STOCKED),
            )
        )
    )
    used_results = db.exec(used_stmt).all()

    # 실제 검수를 수행한 작업자를 한 번에 모아 온다.
    #
    # 촬영 담당자 사번은 ReturnJob.agent_logs(JSONB)에 있어 재고 행만으로는 알 수 없다.
    # 행마다 조회하면 N+1이 되므로 source_job_id를 모아 일괄 조회하고, 사번→이름도
    # users를 한 번만 읽어 매핑한다.
    from app.models.wms import ReturnJob, User

    job_uuids = []
    for it, _b, _l in used_results:
        if not it.source_job_id:
            continue
        try:
            job_uuids.append(UUID(str(it.source_job_id)))
        except (ValueError, AttributeError, TypeError):
            continue  # 레거시 행의 비UUID 값은 건너뛴다

    worker_by_job: Dict[str, str] = {}
    if job_uuids:
        for j in db.exec(select(ReturnJob).where(ReturnJob.id.in_(job_uuids))).all():
            emp = ((j.agent_logs or {}).get("inbound_worker_id") or "").strip()
            if emp:
                worker_by_job[str(j.id)] = emp

    # 검수 접수 작업자 + 선부착 작업자 사번을 한 번에 모아 이름을 조회한다.
    all_emp_ids = set(worker_by_job.values())
    all_emp_ids.update(
        it.prelabel_worker_id for it, _b, _l in used_results if getattr(it, "prelabel_worker_id", None)
    )
    name_by_emp: Dict[str, str] = {}
    if all_emp_ids:
        for u in db.exec(select(User).where(User.employee_id.in_(all_emp_ids))).all():
            name_by_emp[u.employee_id] = u.name

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
            # UBCI 점수는 매입가를 결정하는 규정 산식의 출력이므로 기본값으로 채우지 않는다.
            # 검수 전이라 값이 없으면 None을 그대로 내려보내고 화면이 "미산출"로 표기한다.
            "grade": item.condition_grade or None,
            "ubci_score": item.ubci_score,
            "zone": zone_str,
            "quantity": 1,
            # [2026-08-10] 작업자(사람)와 확정 트랙(AI/HITL)을 분리해 내려준다.
            # 종전에는 "Nexus Vision AI (LangGraph 4-Agent)" 한 문자열이 작업자 칸을 채워
            # 실제 검수를 수행한 사람이 화면에 전혀 나오지 않았다.
            # [2026-08-12] 검수 기록이 없으면 선부착(라벨 발급) 작업자로 폴백.
            "worker_label": format_worker_label(
                worker_by_job.get(str(item.source_job_id or ""))
                or getattr(item, "prelabel_worker_id", None),
                name_by_emp.get(
                    worker_by_job.get(str(item.source_job_id or ""))
                    or getattr(item, "prelabel_worker_id", None)
                    or ""
                ),
            ),
            "track": resolve_track(getattr(item, "inspection_source", None)),
            # 구 필드는 화면 호환을 위해 유지한다 (등급 확정 주체 서술).
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
async def create_lpn(req: CreateLpnRequest, db: Session = Depends(get_db)):
    """
    새로운 LPN 바코드를 발급하고 검수 대기 버퍼 로케이션에 선부착 등록합니다.

    [2026-08-06 수정] LPN 채번을 프론트에서 백엔드로 이관하면서, 이 엔드포인트가 **바코드
    스캔 직후** 호출되게 되었다. 그 시점에는 아직 books 행이 없어(입고 확정 전) 종전
    구현은 404 "Book not found"로 죽는다. LPN 발급은 선부착 설계상 검수보다 먼저여야
    하므로, 도서가 없으면 알라딘 조회로 최소 메타데이터를 만들어 등록한다.
    (`/inbound/fasttrack`이 이미 쓰는 것과 동일한 패턴)
    """
    from app.models.wms import Book
    from app.domains.inbound.service import (
        lookup_book_by_isbn,
        book_row_to_lookup_payload,
        is_placeholder_book,
        UNKNOWN_BOOK_TITLE,
    )

    isbn = (req.isbn or "").strip()

    if not req.book_id and isbn:
        book = db.exec(select(Book).where(Book.isbn == isbn)).first()

        # 신규 ISBN이거나 자리표시자("미확인 도서") 행이면 알라딘을 조회해 생성·보강한다.
        # 자리표시자를 그대로 두면 한 번의 조회 실패가 그 ISBN에 영구히 남는다.
        if book is None or is_placeholder_book(book):
            meta = await lookup_book_by_isbn(isbn) or {}
            category_name = meta.get("categoryName", "")
            parts = [p.strip() for p in category_name.split(">") if p.strip()]
            parsed_category = parts[1] if len(parts) > 1 else (parts[0] if parts else "GENERAL")

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
            for field in ("width_mm", "depth_mm", "thickness_mm", "weight_g", "page_count"):
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

    # [2026-08-12] 종전에는 req.worker_id를 응답으로 되돌려줄 뿐 저장하지 않아,
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
        "worker_id": req.worker_id
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
        select(ReturnJob).where(ReturnJob.agent_logs["lpn_barcode"].astext == lpn_barcode)
    ).first()
    if linked_job:
        raise HTTPException(
            status_code=409,
            detail="이미 AI 검수 큐에 접수된 LPN이라 취소할 수 없습니다.",
        )

    db.delete(item)
    db.commit()
    print(f"[LPN] 미부착 채번 취소: {lpn_barcode}")
    return {"status": "success", "lpn_barcode": lpn_barcode, "deleted": True}


@router.delete("/items/{row_id}")
def hard_delete_inventory_row(row_id: UUID, db: Session = Depends(get_db), current_admin=Depends(_admin_only)):
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

    deleted = {"used_item": 0, "new_inventory": 0, "jobs": 0, "logs": 0, "notifications": 0}

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
                    Notification.ref_type == "RETURN_JOB", Notification.ref_id.in_(job_ids)
                )
            ).all():
                db.delete(n)
                deleted["notifications"] += 1

        for lg in db.exec(select(InventoryLog).where(InventoryLog.target_lpn == lpn)).all():
            db.delete(lg)
            deleted["logs"] += 1

        db.delete(item)  # source_job_id FK 때문에 job보다 먼저 지운다
        deleted["used_item"] = 1
        for j in jobs:
            db.delete(j)
            deleted["jobs"] += 1

        db.commit()
        print(f"[재고 하드삭제] {lpn}: {deleted} (by {getattr(current_admin, 'employee_id', '?')})")
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
            select(Inventory).where(Inventory.book_id == book_id, Inventory.id != row_id)
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
        print(f"[재고 하드삭제] 신품 book={book_id}: {deleted} (by {getattr(current_admin, 'employee_id', '?')})")
        return {"status": "success", "book_id": str(book_id), "deleted": deleted}

    raise HTTPException(status_code=404, detail=f"재고 행을 찾을 수 없습니다: {row_id}")


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

    # 목록(GET /inventory)과 같은 표기 정본을 쓴다 - "작업자"는 AI/HITL 판정 주체가 아니라
    # 실제 입고 처리한 사람이어야 한다(라벨 인쇄가 이 값을 그대로 쓴다).
    from app.models.wms import User as _User
    inbound_worker_name = None
    if inspector.get("inbound_worker_id"):
        _u = db.query(_User).filter(_User.employee_id == inspector["inbound_worker_id"]).first()
        inbound_worker_name = _u.name if _u else None
    worker_label = format_worker_label(inspector.get("inbound_worker_id"), inbound_worker_name)

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
        "worker_label": worker_label,
        "inspector": inspector,
        # 카테고리별 차등이 적용된 가격 산정 내역 (프론트는 렌더만 한다)
        "pricing": pricing,
        # [2026-08-06 수정] 상세/진단 타임라인의 기준 시각을 최초 입고(created_at)가 아니라
        # 최종 검수 확정(inspected_at)으로 변경 - 재검수를 돌려도 화면 시각이 옛 입고 시각에
        # 머물던 문제 교정 (inspected_at 미기록 레거시 row는 created_at 폴백).
        "date": to_kst_str(item.inspected_at or item.created_at),
        # 컨테이너 절대경로가 아니라 브라우저가 실제로 열 수 있는 URL로 정규화해 내려준다.
        "image_urls": to_browser_image_urls(job.image_urls if job else []),
        "agent_logs": agent_logs,
        # Report Agent가 생성한 고객 공개용 보증서 본문. 프론트가 등급별 문장을
        # 하드코딩하지 않고 이 값을 그대로 렌더한다.
        "final_report": job.final_report if job else None,
        "certificate": agent_logs.get("certificate"),
    }
