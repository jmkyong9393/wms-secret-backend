from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from uuid import UUID
import json
import uuid
import datetime
from app.db.session import get_db
from app.core.security import get_current_user
from app.models.wms import now_kst, InboundJob, Book, ReturnJob, InventoryUsedItem, JobStatusEnum, ubci_grade_from_score
from app.domains.inbound.service import (
    generate_signed_cookie,
    lookup_book_by_isbn,
    lookup_book_by_isbn_with_status,
    book_row_to_lookup_payload,
    is_placeholder_book,
    LOOKUP_UNAVAILABLE,
)
import base64
import os
from app.core.stream_auth import require_stream_access
from app.models.wms import User


class UploadCookieRequest(BaseModel):
    filename: str

class EvaluateRequest(BaseModel):
    lpn: str
    images: List[str]
    book_metadata: Optional[Dict[str, Any]] = None
    # 입고 촬영을 수행한 작업자 사번. 재고 상세/보증서의 "입고 처리 담당자" 표기가
    # 하드코딩 상수가 아니라 실제 담당자를 가리키도록 하기 위해 받는다 (미전달 시 AI 자동 판정으로 표기).
    worker_id: Optional[str] = None

# Inbound 도메인 라우터: 협력사(B2B) 또는 일반 사용자의 입고 요청 및 처리 이력을 담당합니다.
# 라우터 전체에 인증을 건다. 엔드포인트마다 붙이면 새 경로를 추가할 때 또 빠뜨린다 -
# 실제로 재고·피킹지시서·발주제안이 무인증으로 조회되던 것을 전수 점검에서 발견했다.
# 입고 검수는 로그인 필수
router = APIRouter(prefix="/inbound", tags=["Inbound"],
                   dependencies=[Depends(get_current_user)])

@router.get("/history")
async def get_inbound_history(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """
    최근 처리된 입고 작업(NEW_STOCK, CUSTOMER_RETURN 등)의 이력을 반환합니다.
    (InboundJob 테이블에서 최신 50건을 조회하여 반환)
    """
    statement = select(InboundJob).order_by(InboundJob.created_at.desc()).limit(50)
    jobs = db.exec(statement).all()
    
    result = []
    for job in jobs:
        result.append({
            "inbound_id": str(job.id),
            "inbound_type": job.inbound_type,
            "supplier_name": job.supplier_name or "N/A",
            "status": job.status,
            "date": job.created_at.isoformat()
        })
    return result

@router.get("/book-lookup", summary="ISBN 바코드 스캔 시 도서 정보/택배 규격 조회 (원장 우선)")
async def get_book_lookup(isbn: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    프론트 입고 화면(ISBN 바코드 스캔)이 호출하는 도서 메타데이터 조회 API.
    제목/저자/출판사/표지/가격/설명뿐 아니라 택배 송장 산정용 가로/세로/두께/무게도 함께 반환한다.

    조회 순서: ① 원장(books) → ② 알라딘 → ③ 원장 자리표시자 행.
    원장을 먼저 보므로 이미 입고된 적 있는 도서는 알라딘 가용성과 무관하게 조회된다.
    """
    if not isbn:
        raise HTTPException(status_code=400, detail="ISBN이 필요합니다.")

    book = db.exec(select(Book).where(Book.isbn == isbn)).first()
    if book and not is_placeholder_book(book):
        return book_row_to_lookup_payload(book)

    result, error = await lookup_book_by_isbn_with_status(isbn)
    if result:
        return result

    # 알라딘 실패 시 자리표시자 행이라도 내려준다. ISBN이 살아 있어야 이후 evaluate 요청이
    # book_metadata.isbn 없이 나가 500이 나는 것을 막는다.
    if book:
        return book_row_to_lookup_payload(book)

    # 조회 불가(503)와 없는 책(404)을 구분해 응답한다.
    if error == LOOKUP_UNAVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="도서 정보 서버(알라딘)에 연결하지 못했습니다. 잠시 후 다시 스캔해주세요.",
        )
    raise HTTPException(status_code=404, detail="등록되지 않은 ISBN입니다. 도서 정보를 찾을 수 없습니다.")


class FasttrackRequest(BaseModel):
    isbn: str
    title: Optional[str] = None
    imageUrl: Optional[str] = None
    qty: int = 1
    # 입고를 수행한 작업자 사번. 신품은 ReturnJob(중고 검수 원장)을 타지 않아
    # 종전에는 작업자가 어디에도 남지 않았고, 그 결과 "나의 검수 내역"이 신품 입고분을
    # 걸러낼 기준값 자체를 갖지 못했다. InventoryLog(입고 1건 = 로그 1행)에 기록한다.
    worker_id: Optional[str] = None


@router.post("/fasttrack", summary="신품 도서 Fast-Track 0초 입고 (사진/UBCI 판정 스킵)")
async def fasttrack_inbound(request: FasttrackRequest, db: Session = Depends(get_db)):
    """
    신품 도서를 ISBN만으로 즉시 재고에 편입한다. 사진 촬영·AI 검수(UBCI 등급 판정)를 100%
    건너뛰는 것이 설계 의도이며, LangGraph 파이프라인/ReturnJob을 아예 타지 않는다.
    신품은 개별 LPN 없이 Inventory(묶음 재고) 테이블에 수량으로 관리된다.

    프론트(src/app/inbound/page.tsx)가 처음부터 이 경로를 호출하고
    있었으나 백엔드에 라우트가 존재하지 않아 Fast-Track 입고가 100% 404로 실패하던 것을 구현.
    """
    isbn = (request.isbn or "").strip()
    if not isbn or len(isbn) < 4:
        raise HTTPException(status_code=400, detail="유효한 ISBN이 필요합니다.")
    qty = max(1, int(request.qty or 1))

    # 1) Book 조회, 없으면 생성 (알라딘 실조회로 저자/출판사/정가/택배 규격까지 보강)
    book = db.exec(select(Book).where(Book.isbn == isbn)).first()
    if not book:
        meta = await lookup_book_by_isbn(isbn) or {}
        category_name = meta.get("categoryName", "")
        parts = [p.strip() for p in category_name.split(">") if p.strip()]
        parsed_category = parts[1] if len(parts) > 1 else (parts[0] if parts else "GENERAL")

        book_kwargs: Dict[str, Any] = dict(
            isbn=isbn,
            title=meta.get("title") or request.title or "신품 도서",
            author=meta.get("author"),
            publisher=meta.get("publisher"),
            published_date=meta.get("pubDate"),
            base_price=float(meta.get("price", 0.0) or 0.0),
            description=meta.get("description"),
            cover_image_url=meta.get("imageUrl") or request.imageUrl,
            category_type=parsed_category,
        )
        for field in ("width_mm", "depth_mm", "thickness_mm", "weight_g", "page_count"):
            if meta.get(field) is not None:
                book_kwargs[field] = meta[field]

        book = Book(**book_kwargs)
        db.add(book)
        try:
            db.commit()
            db.refresh(book)
        except IntegrityError:
            # select→insert 사이에 다른 요청이 같은 ISBN을 먼저 넣은 경우.
            # unique 제약(ix_books_isbn)이 이미 중복을 막고 있으므로, 여기서는
            # 롤백하고 그 요청이 만든 행을 다시 읽어 쓰면 된다.
            # (부하 테스트 실측: 같은 신규 ISBN 20건 동시 입고 시 14건이 500으로 실패했다)
            db.rollback()
            book = db.exec(select(Book).where(Book.isbn == isbn)).first()
            if not book:
                raise HTTPException(status_code=409,
                                    detail="도서 등록이 동시 요청과 충돌했습니다. 다시 시도해 주세요.")

    # 2) Zone A(신품존) 묶음 재고 upsert + virtual_stock 가산 + INBOUND 원장 기록.
    #    자동 발주(OrderProposal) 승인 입고와 동일한 공용 관문(fasttrack_new_stock_inbound)을 사용한다.
    from app.domains.inventory.service import fasttrack_new_stock_inbound

    inv, location = fasttrack_new_stock_inbound(db, book, qty, worker_id=request.worker_id)
    db.commit()
    db.refresh(inv)

    return {
        "status": "SUCCESS",
        "message": f"⚡ [신품 Fast-Track] '{book.title}' {qty}권이 Zone A 신품존 재고로 즉시 입고되었습니다. (AI 검수 스킵)",
        "book_id": str(book.id),
        "isbn": book.isbn,
        "title": book.title,
        "added_qty": qty,
        "total_qty": inv.quantity,
        "zone": f"{location.zone}-{location.rack}-{location.shelf}",
    }


@router.post("/upload-cookie")
async def get_upload_cookie(request: UploadCookieRequest) -> Dict[str, Any]:
    """
    모바일/웹 클라이언트에서 S3로 다이렉트 업로드를 하기 위한 CloudFront Signed Cookie를 발급합니다.
    """
    if not request.filename:
        raise HTTPException(status_code=400, detail="Filename is required")
        
    cookie_data = generate_signed_cookie(request.filename)
    return cookie_data

@router.post("/evaluate")
async def start_evaluation(request: EvaluateRequest, db: Session = Depends(get_db)):
    """
    AI 판독 작업 생성 API. 모바일 렌즈에서 촬영된 이미지를 ReturnJob DB row로 적재하고
    Celery 태스크(app.worker.tasks.process_inspection - Redlock+DLQ 백엔드)로 위임합니다.
    """
    if len(request.images) < 2:
        raise HTTPException(status_code=400, detail="At least 2 images (front, back) are required.")

    # Save book to DB if it doesn't exist
    book = None
    parsed_category = "GENERAL"
    if request.book_metadata:
        isbn = request.book_metadata.get('isbn')
        if isbn:
            statement = select(Book).where(Book.isbn == isbn)
            book = db.exec(statement).first()

            category_name = request.book_metadata.get('categoryName', '')
            if category_name:
                parts = category_name.split('>')
                if len(parts) > 1:
                    parsed_category = parts[1].strip()
                else:
                    parsed_category = parts[0].strip()

            if not book:
                book_kwargs = dict(
                    barcode=isbn, # Usually we use isbn as barcode for new books
                    isbn=isbn,
                    title=request.book_metadata.get('title', 'Unknown Title'),
                    author=request.book_metadata.get('author'),
                    publisher=request.book_metadata.get('publisher'),
                    published_date=request.book_metadata.get('pubDate'),
                    base_price=float(request.book_metadata.get('price', 0.0) or 0.0),
                    description=request.book_metadata.get('description'),
                    cover_image_url=request.book_metadata.get('imageUrl'),
                    category_type=parsed_category
                )
                # 택배 송장 산정용 알라딘 실측 규격(GET /inbound/book-lookup에서 이미 조회되어
                # 프론트가 book_metadata에 실어 보낸 값) - 없으면 Book 모델 기본값(신국판 표준)을 그대로 둔다.
                for field in ("width_mm", "depth_mm", "thickness_mm", "weight_g", "page_count"):
                    value = request.book_metadata.get(field)
                    if value is not None:
                        book_kwargs[field] = value
                if any(request.book_metadata.get(f) is not None for f in ("width_mm", "depth_mm", "thickness_mm", "weight_g")):
                    book_kwargs["calc_source"] = "ALADIN_REAL_SPEC"

                book = Book(**book_kwargs)
                db.add(book)
                db.commit()
                db.refresh(book)

    # LPN 재촬영(재검수)은 촬영 당시 ISBN 바코드 없이 LPN QR만으로 진행될 수 있다
    # (book_metadata에 isbn이 아예 없음). 이 경우 위 경로로는 book을 찾지 못하지만,
    # 그 LPN은 최초 입고 시 이미 book_id가 연결돼 있으므로 그 연결을 재사용한다.
    # 여기서도 book을 못 찾으면 return_jobs.book_id NOT NULL 위반으로 500이 난다.
    if not book and request.lpn:
        existing_item = db.exec(
            select(InventoryUsedItem).where(InventoryUsedItem.lpn_barcode == request.lpn)
        ).first()
        if existing_item:
            book = db.get(Book, existing_item.book_id)

    if not book:
        raise HTTPException(
            status_code=422,
            detail="도서 정보를 확인할 수 없습니다. ISBN을 다시 스캔하거나 LPN 재촬영으로 진행해주세요."
        )

    job_id = f"job-{uuid.uuid4().hex[:8]}"

    # [수정 이력] 예전에는 base64 이미지를 로컬 디스크에만 저장하고 컨테이너 절대경로
    # (/app/app/experiment_data/job-xxx/raw_0.jpg)를 그대로 image_urls에 넣었다. 프론트는 이
    # 문자열을 <img src>에 그대로 꽂으므로 http://localhost:3000/app/app/... 으로 해석되어
    # 100% 404였다 - 상세페이지에서 검수 이미지가 한 장도 안 뜨던 직접적 원인.
    # app/core/s3_service.upload_base64_to_s3()는 정의만 되어 있고 호출부가 단 한 곳도 없는
    # dead code였다. 이제 실제로 S3에 올리고 브라우저가 열 수 있는 CloudFront URL을 적재한다.
    #
    # 로컬 사본도 계속 남긴다: Vision Agent의 WBF YOLO 추론은 로컬 파일 경로를 요구하는데,
    # 원격 URL만 있으면 매 검수마다 CloudFront에서 재다운로드해야 해 불필요한 지연이 생긴다.
    # 따라서 image_urls = 브라우저용 공개 URL, agent_logs.local_image_paths = 워커 추론용 경로로
    # 역할을 분리한다.
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    experiment_dir = os.path.join(base_dir, "experiment_data", job_id)
    os.makedirs(experiment_dir, exist_ok=True)

    from app.core.s3_service import upload_bytes_to_s3

    s3_prefix = f"inbound/{now_kst().strftime('%Y%m%d')}/{job_id}"

    local_image_paths: List[str] = []
    public_image_urls: List[str] = []
    for idx, b64_img in enumerate(request.images):
        if b64_img.startswith("data:image"):
            b64_img = b64_img.split(",")[1]

        try:
            raw_bytes = base64.b64decode(b64_img)
        except Exception as e:
            print(f"[Inbound] 이미지 base64 디코딩 실패 (idx={idx}): {e}")
            continue

        img_path = os.path.join(experiment_dir, f"raw_{idx}.jpg")
        try:
            with open(img_path, "wb") as f:
                f.write(raw_bytes)
            local_image_paths.append(img_path)
        except Exception as e:
            print(f"[Inbound] 로컬 이미지 저장 실패 (idx={idx}): {e}")

        # S3 업로드 실패 시에도 입고 자체는 막지 않는다. 공개 URL을 못 얻으면 백엔드
        # StaticFiles 마운트(/experiment_data)로 폴백해 최소한 사내망에서는 보이게 한다.
        cdn_url = upload_bytes_to_s3(raw_bytes, f"{s3_prefix}/raw_{idx}.jpg")
        public_image_urls.append(cdn_url or f"/experiment_data/{job_id}/raw_{idx}.jpg")

    # 입고 촬영을 수행한 담당자. 하드코딩된 "WM2608001" 대신 요청자의 사번을 그대로 보존해
    # 이후 재고 상세/보증서 화면이 실제 담당자를 표시할 수 있게 한다.
    inbound_worker_id = (request.worker_id or "").strip() or None

    # ReturnJob DB row 생성 - lpn/book_category/book_metadata는 agent_logs(JSONB)에 보존
    new_job = ReturnJob(
        book_id=book.id,
        image_urls=public_image_urls,
        status=JobStatusEnum.PENDING.value,
        agent_logs={
            "lpn_barcode": request.lpn,
            "book_category": parsed_category,
            "book_metadata": request.book_metadata,
            "local_image_paths": local_image_paths,
            "inbound_worker_id": inbound_worker_id,
        },
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    # 태스크 발행: 브로커 순단 대비 3회 재시도. 그래도 실패하면 조용한 인프로세스
    # 폴백(추적 불가·유실 위험) 대신 워커의 기동 시 스위퍼(requeue_stale_pending_jobs)가
    # 원장 기반으로 재큐잉하도록 PENDING 상태 그대로 둔다 — "브로커는 잃어도 원장은 잃지 않는다".
    from app.worker.tasks import process_inspection
    import time as _time
    for attempt in range(3):
        try:
            process_inspection.delay(str(new_job.id))
            break
        except Exception as e:
            if attempt == 2:
                print(f"[Dispatch] 태스크 발행 3회 실패 - 스위퍼 복구 대상으로 남김: {e}")
            else:
                _time.sleep(0.5 * (attempt + 1))

    return {"job_id": str(new_job.id), "lpn": request.lpn, "message": "Evaluation job queued successfully"}


@router.get("/stream/{job_id}")
async def stream_evaluation_progress(job_id: str, _user: User = Depends(require_stream_access)):
    """
    [SSE] AI 작업 상태 실시간 푸시 API. Celery 워커가 Redis Pub/Sub 채널(return_job:{job_id})에
    발행하는 진행 이벤트를 구독해, 프론트엔드가 기대하는 필드 형태(job_id/progress/message/
    grade/ubci_score/defect_description)로 변환하여 그대로 중계합니다.
    """
    async def event_generator():
        import redis.asyncio as aioredis
        from app.core.redis_pubsub import get_return_job_channel, REDIS_URL

        r = aioredis.Redis.from_url(REDIS_URL, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(get_return_job_channel(job_id))

        try:
            yield f"data: {json.dumps({'job_id': job_id, 'progress': 0, 'message': 'AI 검수 초기화 중...', 'grade': None})}\n\n"

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    raw = json.loads(message["data"])
                except (TypeError, json.JSONDecodeError):
                    continue

                payload = {
                    "job_id": job_id,
                    "progress": raw.get("progress", 0),
                    "message": raw.get("message") or raw.get("status") or "처리 중...",
                    "grade": raw.get("grade"),
                    "ubci_score": raw.get("ubci_score"),
                    "defect_description": raw.get("defect_description"),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

                if raw.get("progress") == 100:
                    break
        finally:
            await pubsub.unsubscribe()
            await r.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/result/{job_id}")
async def get_evaluation_result(job_id: str, db: Session = Depends(get_db)):
    """
    AI 분석이 완료된 후, 프론트엔드 모달에서 상세 내역과 사진 BBox 좌표를 조회하기 위한 API.
    (로컬 result.json 파일 대신 ReturnJob DB row에서 조회)
    """
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Job data not found")

    job = db.get(ReturnJob, job_uuid)
    if not job:
        raise HTTPException(status_code=404, detail="Job data not found")

    agent_logs = job.agent_logs or {}
    defects = agent_logs.get("defects") or []
    defect_types = sorted({d.get("type", "") for d in defects if isinstance(d, dict) and d.get("type")})

    # [수정 이력] job.status가 아직 PENDING/PROCESSING(진행 중)인데도 ubci_grade_from_score(None)이
    # "NORMAL"을 반환해, 폴링하는 클라이언트가 "정상 등급으로 완료됨"과 "아직 처리 중"을 구분할
    # 수 없던 버그를 수정 - 완료 상태(APPROVED/REJECTED/HITL_REQUIRED/FAILED)가 아니면 grade를
    # null로 반환하고 별도 status 필드로 진행 여부를 명시한다.
    still_processing = job.status in (JobStatusEnum.PENDING.value, JobStatusEnum.PROCESSING.value)
    if still_processing:
        grade = None
    elif job.status == JobStatusEnum.HITL_REQUIRED.value:
        grade = "HITL_REQUIRED"
    else:
        grade = ubci_grade_from_score(job.ubci_score)

    return {
        "job_id": job_id,
        "status": job.status,
        "images": job.image_urls or [],
        "result": {
            "job_id": job_id,
            "grade": grade,
            "ubci_score": job.ubci_score,
            "defect_description": ", ".join(defect_types) if defect_types else ("정상" if not still_processing else None),
            "defect_coordinates": [d.get("bbox") for d in defects if isinstance(d, dict) and d.get("bbox")],
            "timestamp": (job.updated_at or job.created_at).isoformat(),
        }
    }


@router.post("/retry/{job_id}")
async def retry_evaluation(job_id: str, db: Session = Depends(get_db)):
    """
    기존에 저장된 이미지를 기반으로 AI 검수를 다시 실행합니다.
    Celery 태스크로 재큐잉(비동기)하며, 재검수 완료 여부는 SSE(/inbound/stream/{job_id})로 확인합니다.
    """
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Images not found for retry")

    job = db.get(ReturnJob, job_uuid)
    if not job or not job.image_urls:
        raise HTTPException(status_code=404, detail="Images not found for retry")

    job.status = JobStatusEnum.PENDING.value
    job.retry_count = (job.retry_count or 0) + 1
    job.updated_at = now_kst()
    db.add(job)
    db.commit()

    from app.worker.tasks import process_inspection
    try:
        process_inspection.delay(str(job.id))
    except Exception as e:
        print(f"[Celery/Docker Offline Fallback] Direct in-process execution: {e}")
        import threading
        threading.Thread(target=process_inspection, args=(str(job.id),), daemon=True).start()

    return {"status": "queued", "job_id": job_id, "message": "재검수 작업이 큐에 등록되었습니다."}



@router.post("/putaway", summary="현장 적치(Putaway) 랙 배치 위치 확정")
def confirm_putaway_placement(
    payload: dict,
    db: Session = Depends(get_db)
):
    """
    검수 완료 도서를 지정된 Zone-Bin 랙 로케이션에 물리적 적치 완료 처리
    """
    lpn_barcode = payload.get("lpn_barcode", "")
    location_id = payload.get("location_id", "Zone B-1-4")
    
    print(f"Confirmed Putaway for LPN {lpn_barcode} -> Location {location_id}")
    return {
        "status": "SUCCESS",
        "message": f"LPN {lpn_barcode}가 성공적으로 {location_id} 랙에 적치되었습니다.",
        "location_id": location_id,
        "updated_at": now_kst().isoformat()
    }
