import asyncio
from typing import List
from celery import shared_task
from app.agents.graph import build_wms_graph
from app.core.database import engine
from sqlmodel import Session, select
from app.models.wms import ReturnJob, JobStatusEnum
import redis
import json

# LangGraph 인스턴스 컴파일 (워커 프로세스 로드 시 1회만 수행하여 성능 최적화)
wms_agent_graph = build_wms_graph()

@shared_task(name="app.worker.tasks.process_inspection")
def process_inspection(job_id: str, image_urls: List[str]):
    """
    FastAPI로부터 넘겨받은 비동기 이미지 검수 작업을 수행합니다.
    (홍경표 님의 LangGraph 4-Agent 파이프라인 가동)
    """
    print(f"[{job_id}] 비동기 검수 파이프라인 가동 (이미지 {len(image_urls)}장)")
    
    # 1. 초기 상태(State) 주입
    target_image = image_urls[0] if image_urls else ""
    
    initial_state = {
        "job_id": job_id,
        "image_path": target_image,
        "has_defect": None,
        "defect_description": None,
        "matched_rule": None,
        "ubci_grade": None,
        "ubci_score": None,
        "needs_hitl": None
    }
    
    # 2. LangGraph 실행
    try:
        final_state = wms_agent_graph.invoke(initial_state)
    except Exception as e:
        print(f"[{job_id}] 파이프라인 에러 발생: {e}")
        _update_job_status(job_id, JobStatusEnum.REJECTED.value, final_report=f"Error: {e}")
        return {"status": "error", "error": str(e)}

    # 3. 결과 분석 및 DB 업데이트 (PostgreSQL JSONB)
    needs_hitl = final_state.get("needs_hitl", False)
    final_status = JobStatusEnum.REVIEW.value if needs_hitl else JobStatusEnum.APPROVED.value
    
    report = (
        f"[판정 등급]: {final_state.get('ubci_grade')} ({final_state.get('ubci_score')}점)\n"
        f"[적용 규정]: {final_state.get('matched_rule')}\n"
        f"[훼손 요약]: {final_state.get('defect_description')}"
    )
    
    _update_job_status(
        job_id=job_id,
        status=final_status,
        score=final_state.get("ubci_score"),
        logs=final_state,
        final_report=report
    )
    
    print(f"[{job_id}] 검수 완료 -> {final_status}")
    return {"status": final_status, "score": final_state.get("ubci_score")}

def _update_job_status(job_id: str, status: str, score: int = None, logs: dict = None, final_report: str = None):
    """DB 상태 업데이트 유틸리티 (비동기 워커 격리 환경)"""
    with Session(engine) as session:
        statement = select(ReturnJob).where(ReturnJob.id == job_id)
        job = session.exec(statement).first()
        if job:
            job.status = status
            if score is not None:
                job.ubci_score = score
            if logs:
                job.agent_logs = logs # AI 추론 과정 전체를 JSONB로 저장
            if final_report:
                job.final_report = final_report
            session.add(job)
            session.commit()
            
    # Redis Pub/Sub로 이벤트 발행 (SSE 연동)
    try:
        r = redis.Redis(host='localhost', port=6379, db=0)
        message = json.dumps({"job_id": job_id, "status": status, "score": score})
        r.publish(f"job_status:{job_id}", message)
    except Exception as e:
        print(f"Redis Publish Error: {e}")
