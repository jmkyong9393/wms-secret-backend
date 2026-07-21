from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select
from typing import List, Dict, Any
from pydantic import BaseModel
import asyncio
import json
import uuid
from app.db.session import get_db
from app.models.wms import InboundJob
from app.domains.inbound.service import generate_signed_cookie

class UploadCookieRequest(BaseModel):
    filename: str

class EvaluateRequest(BaseModel):
    lpn: str
    images: List[str]

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
async def start_evaluation(request: EvaluateRequest):
    """
    [UX 렌더링 최적화] AI 판독 작업 생성 API
    모바일 렌즈에서 촬영된 이미지들을 AI 에이전트 파이프라인으로 넘기기 위해 Job을 큐에 적재합니다.
    (현재는 실제 워커(Celery) 대신 SSE 테스트를 위한 모의 job_id를 반환합니다.)
    """
    if len(request.images) < 2:
        raise HTTPException(status_code=400, detail="At least 2 images (front, back) are required.")
    
    # 향후 Celery Task ID로 대체될 고유 작업 식별자
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    return {"job_id": job_id, "lpn": request.lpn, "message": "Evaluation job queued successfully"}

async def mock_ai_worker(job_id: str):
    """
    [UX 렌더링 최적화] SSE 테스트용 비동기 제너레이터 워커.
    실제 환경에서는 Celery 워커의 진행 상태를 Polling 하거나 Redis Pub/Sub을 통해 이벤트를 수신받아 클라이언트에게 쏴줍니다.
    여기서는 1.2초마다 상태가 변하는 모의 AI 파이프라인을 구현했습니다.
    """
    steps = [
        (20, "이미지 해상도 최적화 및 VLM 디코딩 중..."),
        (40, "사내 규정(Policy) 매칭 - 훼손 기준 분석 중..."),
        (60, "교차 검증(Critic Agent) 수행 중..."),
        (80, "최종 상태 확정 및 Report 작성 중..."),
        (100, "완료")
    ]
    
    for progress, msg in steps:
        await asyncio.sleep(1.2) # 모의 지연 (실제 VLM 통신 지연 시뮬레이션)
        
        data = {
            "job_id": job_id,
            "progress": progress,
            "message": msg,
            "grade": "S등급 (최상)" if progress == 100 else None
        }
        # [SSE 규격 준수] SSE는 "data: {JSON문자열}\n\n" 형식을 엄격히 지켜야만 
        # 브라우저의 EventSource 객체가 이벤트를 정상적으로 인식합니다.
        yield f"data: {json.dumps(data)}\n\n"

@router.get("/stream/{job_id}")
async def stream_evaluation_progress(job_id: str):
    """
    [UX 렌더링 최적화] AI 작업 상태 실시간 푸시(SSE) API
    StreamingResponse를 사용하여, 지정된 job_id의 진행률 데이터를 연결이 끊기지 않은 채로
    클라이언트에게 실시간(Event-driven) 푸시합니다.
    """
    return StreamingResponse(mock_ai_worker(job_id), media_type="text/event-stream")

