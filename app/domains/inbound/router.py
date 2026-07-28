from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import asyncio
import json
import uuid
import datetime
from app.db.session import get_db
from app.models.wms import InboundJob, Book
from app.domains.inbound.service import generate_signed_cookie
import base64
import os
import tempfile

job_store = {}


class UploadCookieRequest(BaseModel):
    filename: str

class EvaluateRequest(BaseModel):
    lpn: str
    images: List[str]
    book_metadata: Optional[Dict[str, Any]] = None

# Inbound 도메인 라우터: 협력사(B2B) 또는 일반 사용자의 입고 요청 및 처리 이력을 담당합니다.
router = APIRouter(prefix="/inbound", tags=["Inbound"])

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
    [UX 렌더링 최적화] AI 판독 작업 생성 API
    모바일 렌즈에서 촬영된 이미지들을 AI 에이전트 파이프라인으로 넘기기 위해 Job을 큐에 적재합니다.
    (현재는 실제 워커(Celery) 대신 SSE 테스트를 위한 모의 job_id를 반환합니다.)
    """
    if len(request.images) < 2:
        raise HTTPException(status_code=400, detail="At least 2 images (front, back) are required.")
    
    # Save book to DB if it doesn't exist
    if request.book_metadata:
        isbn = request.book_metadata.get('isbn')
        if isbn:
            statement = select(Book).where(Book.isbn == isbn)
            book = db.exec(statement).first()
            
            category_name = request.book_metadata.get('categoryName', '')
            parsed_category = "GENERAL"
            if category_name:
                parts = category_name.split('>')
                if len(parts) > 1:
                    parsed_category = parts[1].strip()
                else:
                    parsed_category = parts[0].strip()

            if not book:
                book = Book(
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
                db.add(book)
                db.commit()
                db.refresh(book)

    # 향후 Celery Task ID로 대체될 고유 작업 식별자
    job_id = f"job-{uuid.uuid4().hex[:8]}"

    # Save base64 images to persistent folder (mapped via docker volume to ./app/experiment_data)
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    experiment_dir = os.path.join(base_dir, "experiment_data", job_id)
    os.makedirs(experiment_dir, exist_ok=True)
    
    image_paths = []
    for idx, b64_img in enumerate(request.images):
        if b64_img.startswith("data:image"):
            b64_img = b64_img.split(",")[1]
            
        img_path = os.path.join(experiment_dir, f"raw_{idx}.jpg")
        try:
            with open(img_path, "wb") as f:
                f.write(base64.b64decode(b64_img))
            image_paths.append(img_path)
        except Exception as e:
            print(f"Error saving image: {e}")
            
    job_store[job_id] = {
        "lpn": request.lpn,
        "image_paths": image_paths,
        "category": parsed_category,
        "book_metadata": request.book_metadata
    }

    return {"job_id": job_id, "lpn": request.lpn, "message": "Evaluation job queued successfully"}

async def real_ai_worker(job_id: str):
    job_data = job_store.get(job_id, {})
    image_paths = job_data.get("image_paths", [])
    book_category = job_data.get("category", "GENERAL")
    lpn_code = job_data.get("lpn")
    book_meta = job_data.get("book_metadata") or {}
    
    if not image_paths:
        yield f"data: {json.dumps({'job_id': job_id, 'progress': 100, 'message': '에러: 이미지 없음', 'grade': None, 'ubci_score': None})}\n\n"
        return

    yield f"data: {json.dumps({'job_id': job_id, 'progress': 10, 'message': 'AI 검수 초기화 중...', 'grade': None})}\n\n"

    try:
        from app.ai.graph import build_wms_graph
        app = build_wms_graph()
        
        initial_state = {
            "job_id": job_id,
            "image_paths": image_paths,
            "book_category": book_category,
            "messages": [],
            "retry_count": 0,
            "needs_hitl": False
        }
        
        def run_graph_sync():
            updates = []
            for out in app.stream(initial_state, stream_mode="updates"):
                updates.append(out)
            return updates
            
        updates = await asyncio.to_thread(run_graph_sync)
        
        grade = "NORMAL"
        ubci_score = 75
        defect_coordinates = []
        defect_description = "정상"
        for out in updates:
            if "vision_agent" in out:
                yield f"data: {json.dumps({'job_id': job_id, 'progress': 40, 'message': 'Vision VLM 분석 완료'})}\n\n"
                if "defect_coordinates" in out["vision_agent"]:
                    defect_coordinates = out["vision_agent"]["defect_coordinates"]
                if "defect_description" in out["vision_agent"]:
                    defect_description = out["vision_agent"]["defect_description"]
                await asyncio.sleep(0.5)
            if "policy_agent" in out:
                yield f"data: {json.dumps({'job_id': job_id, 'progress': 70, 'message': '사내 규정(Policy) 매칭 완료'})}\n\n"
                grade = out["policy_agent"].get("ubci_grade") or out["policy_agent"].get("grade") or grade
                ubci_score = out["policy_agent"].get("ubci_score", ubci_score)
                if "matched_rule" in out["policy_agent"]:
                    defect_description = out["policy_agent"]["matched_rule"]
                await asyncio.sleep(0.5)
            if "critic_agent" in out:
                yield f"data: {json.dumps({'job_id': job_id, 'progress': 90, 'message': '교차 검증 완료'})}\n\n"
                await asyncio.sleep(0.5)
            if "hitl_node" in out:
                yield f"data: {json.dumps({'job_id': job_id, 'progress': 95, 'message': '수동 검수 필요'})}\n\n"
                grade = "HITL_REQUIRED"
                await asyncio.sleep(0.5)
                
        # [실제 DB 저장 연동] AI 검수 최종 판정 결과를 InventoryUsedItem DB 테이블에 동기화
        if lpn_code:
            try:
                from app.db.session import engine
                from app.domains.inventory.service import assign_rack_location_after_inspection
                isbn = book_meta.get("isbn")
                
                with Session(engine) as session:
                    book_obj = None
                    if isbn:
                        book_obj = session.exec(select(Book).where(Book.isbn == isbn)).first()
                    
                    item = session.exec(select(InventoryUsedItem).where(InventoryUsedItem.lpn_barcode == lpn_code)).first()
                    if not item:
                        item = InventoryUsedItem(
                            book_id=book_obj.id if book_obj else None,
                            lpn_barcode=lpn_code,
                            ubci_score=ubci_score,
                            condition_grade=grade,
                            item_status="IN_STOCK" if grade != "REJECT" else "REJECTED"
                        )
                        session.add(item)
                    else:
                        item.ubci_score = ubci_score
                        item.condition_grade = grade
                        item.item_status = "IN_STOCK" if grade != "REJECT" else "REJECTED"
                        if book_obj and not item.book_id:
                            item.book_id = book_obj.id
                        session.add(item)
                    session.commit()
                    session.refresh(item)
                    
                    # 창고 랙 위치 (Zone A-E) 배치
                    assign_rack_location_after_inspection(session, lpn_code, grade)
            except Exception as db_err:
                print(f"DB InventoryUsedItem Save Error: {db_err}")

        # Save result to experiment_data
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        experiment_dir = os.path.join(base_dir, "experiment_data", job_id)
        if os.path.exists(experiment_dir):
            with open(os.path.join(experiment_dir, "result.json"), "w", encoding="utf-8") as f:
                json.dump({
                    'job_id': job_id, 
                    'grade': grade, 
                    'ubci_score': ubci_score,
                    'timestamp': datetime.datetime.now().isoformat(),
                    'defect_coordinates': defect_coordinates,
                    'defect_description': defect_description
                }, f, ensure_ascii=False, indent=2)
                
            # Save raw AI trace log for prompt debugging
            def sanitize_for_json(obj):
                if hasattr(obj, 'content'):
                    return {"type": obj.__class__.__name__, "content": obj.content}
                return str(obj)
                
            with open(os.path.join(experiment_dir, "ai_trace_log.json"), "w", encoding="utf-8") as f:
                json.dump(updates, f, ensure_ascii=False, indent=2, default=sanitize_for_json)
                
        yield f"data: {json.dumps({'job_id': job_id, 'progress': 100, 'message': '완료', 'grade': grade, 'ubci_score': ubci_score, 'defect_description': defect_description})}\n\n"
        
    except Exception as e:
        print(f"AI Error: {e}")
        yield f"data: {json.dumps({'job_id': job_id, 'progress': 100, 'message': f'에러: {str(e)}', 'grade': 'ERROR', 'ubci_score': 0})}\n\n"

@router.get("/stream/{job_id}")
async def stream_evaluation_progress(job_id: str):
    """
    [UX 렌더링 최적화] AI 작업 상태 실시간 푸시(SSE) API
    StreamingResponse를 사용하여, 지정된 job_id의 진행률 데이터를 연결이 끊기지 않은 채로
    클라이언트에게 실시간(Event-driven) 푸시합니다.
    """
    return StreamingResponse(real_ai_worker(job_id), media_type="text/event-stream")

@router.get("/result/{job_id}")
async def get_evaluation_result(job_id: str):
    """
    AI 분석이 완료된 후, 프론트엔드 모달에서 상세 내역과 사진 BBox 좌표를 조회하기 위한 API
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    experiment_dir = os.path.join(base_dir, "experiment_data", job_id)
    
    if not os.path.exists(experiment_dir):
        raise HTTPException(status_code=404, detail="Job data not found")
        
    result_data = {}
    result_path = os.path.join(experiment_dir, "result.json")
    if os.path.exists(result_path):
        with open(result_path, "r", encoding="utf-8") as f:
            result_data = json.load(f)
            
    images = []
    for filename in os.listdir(experiment_dir):
        if filename.endswith(".png") or filename.endswith(".jpg") or filename.endswith(".jpeg"):
            images.append(f"http://localhost:8000/experiment_data/{job_id}/{filename}")
            
    return {
        "job_id": job_id,
        "images": images,
        "result": result_data
    }

@router.post("/retry/{job_id}")
async def retry_evaluation(job_id: str):
    """
    기존에 저장된 이미지를 기반으로 AI 검수를 다시 실행합니다.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    experiment_dir = os.path.join(base_dir, "experiment_data", job_id)
    
    import glob
    image_paths = sorted(glob.glob(os.path.join(experiment_dir, "raw_*.jpg")))
    
    if not image_paths:
        raise HTTPException(status_code=404, detail="Images not found for retry")
        
    try:
        from app.ai.graph import build_wms_graph
        app = build_wms_graph()
        
        initial_state = {
            "job_id": job_id,
            "image_paths": image_paths,
            "messages": [],
            "retry_count": 0,
            "needs_hitl": False
        }
        
        def run_graph_sync():
            updates = []
            for out in app.stream(initial_state, stream_mode="updates"):
                updates.append(out)
            return updates
            
        updates = await asyncio.to_thread(run_graph_sync)
        
        grade = "MINT"
        ubci_score = 100
        defect_description = "정상"
        defect_coordinates = []
        for out in updates:
            if "vision_agent" in out and "defect_coordinates" in out["vision_agent"]:
                defect_coordinates = out["vision_agent"]["defect_coordinates"]
                if "defect_description" in out["vision_agent"]:
                    defect_description = out["vision_agent"]["defect_description"]
            if "policy_agent" in out:
                grade = out["policy_agent"].get("ubci_grade", "MINT")
                ubci_score = out["policy_agent"].get("ubci_score", 100)
                if "matched_rule" in out["policy_agent"]:
                    defect_description = out["policy_agent"]["matched_rule"]
            if "hitl_node" in out:
                grade = "HITL_REQUIRED"
                
        # Save result
        with open(os.path.join(experiment_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump({
                'job_id': job_id, 
                'grade': grade, 
                'ubci_score': ubci_score,
                'defect_description': defect_description,
                'timestamp': datetime.datetime.now().isoformat(),
                'defect_coordinates': defect_coordinates
            }, f, ensure_ascii=False, indent=2)
            
        # Save raw AI trace log for prompt debugging
        def sanitize_for_json(obj):
            if hasattr(obj, 'content'):
                return {"type": obj.__class__.__name__, "content": obj.content}
            return str(obj)
            
        with open(os.path.join(experiment_dir, "ai_trace_log.json"), "w", encoding="utf-8") as f:
            json.dump(updates, f, ensure_ascii=False, indent=2, default=sanitize_for_json)
            
        return {
            "job_id": job_id, 
            "grade": grade, 
            "ubci_score": ubci_score,
            "defect_description": defect_description,
            "defect_coordinates": defect_coordinates, 
            "message": "Retry completed"
        }
    except Exception as e:
        print(f"Retry AI Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

