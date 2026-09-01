"""Admin HITL 라우터 - 권한과 배선 전용.

결재·회수·재검수 큐잉 등 업무 규칙은 service.py, 요청·응답 스키마는 schemas.py
(2026-09-01 계층 정리).
"""

from typing import List

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.security import RoleChecker
from app.db.session import get_db
from app.domains.admin import service
from app.domains.admin.schemas import (
    BulkOverridePayload,
    HitlTaskResponse,
    RecallToHitlRequest,
    ScorePreviewRequest,
)
from app.models.wms import UserRoleEnum

# Admin 전용 권한 체커
admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])

# HITL 결재는 전부 관리자 전용이다. 엔드포인트별로 붙이면 새 경로에서 또 빠뜨린다 —
# 실제로 재검수 트리거(POST)에 인가가 없어 무인증으로 Celery 재큐잉이 가능했다.
router = APIRouter(
    prefix="/admin/hitl", tags=["Admin HITL"], dependencies=[Depends(admin_only)]
)


@router.post("/{job_id}/score-preview")
def preview_score_with_edits(
    job_id: str,
    req: ScorePreviewRequest,
    session: Session = Depends(get_db),
    current_admin=Depends(admin_only),
):
    """편집한 BBox 기준의 UBCI 점수/등급을 결재 **전에** 돌려준다.

    policy_agent는 LLM을 쓰지 않는 결정론적 산식이므로 이 미리보기는 API 호출 0회다.
    아무것도 저장하지 않는다. 실제 반영은 /override 가 담당한다.
    """
    return service.preview_score_with_edits(session, job_id, req)


@router.post("/recall/{item_id}")
def recall_inventory_to_hitl(
    item_id: str,
    req: RecallToHitlRequest,
    session: Session = Depends(get_db),
    current_admin=Depends(admin_only),
):
    """적재 완료된 재고를 관리자 판단으로 HITL 재검수 대기로 되돌린다.

    되돌리는 것은 판정 상태뿐이다. 점수·결함·이미지는 그대로 두고 재검수 대기로만
    표시한다 (상세 배경: service.recall_inventory_to_hitl).
    """
    return service.recall_inventory_to_hitl(session, current_admin, item_id, req)


@router.get("/pending", response_model=List[HitlTaskResponse])
def get_pending_hitl_tasks(
    session: Session = Depends(get_db), current_admin=Depends(admin_only)
):
    """
    수동 검수(HITL) 대기 중인 모든 건 조회 (MASTER/ADMIN 보안 인증 가드 적용)
    """
    return service.get_pending_hitl_tasks(session)


@router.post("/override")
def submit_hitl_override(
    payload: BulkOverridePayload,
    session: Session = Depends(get_db),
    current_admin=Depends(admin_only),
):
    """
    관리자가 여러 HITL 건을 다중 선택하여 일괄 오버라이드.
    UBCI 감가 등급, 결함 좌표, 리뷰 시간 등을 함께 수집(Audit Log).
    """
    return service.submit_hitl_override(session, current_admin, payload)


@router.post("/{job_id}/re-inspect")
def trigger_ai_reinspection(job_id: str, session: Session = Depends(get_db)):
    """
    [Master AI Re-inspection Engine]
    HITL 관리자가 재검수를 요청하면, 앱 전체가 공유하는 단일 Celery 파이프라인
    (app.worker.tasks.process_inspection)으로 재큐잉한다. was_hitl은 트리거 시점
    상태를 읽어 태스크 인자로 넘긴다 (배경: 01-freeze-zones.md).
    """
    return service.trigger_ai_reinspection(session, job_id)


@router.get("/{job_id}/assist")
def get_hitl_assist_briefing(
    job_id: str,
    session: Session = Depends(get_db),
    current_admin=Depends(admin_only),
):
    """
    [RAG 기능 B] HITL 결재 관리자 보조 브리핑.

    관련 규정 조항(ChromaDB) + 유사 과거 판정(SQL) + 쟁점 정리(GPT-4o-mini)를 모아
    제공한다. LLM은 결재를 대신 결정하지 않는다 - 최종 판단 권한은 관리자에게 있다.
    """
    return service.get_hitl_assist_briefing(session, job_id)


@router.get("/completed", response_model=List[HitlTaskResponse])
def get_completed_hitl_tasks(
    session: Session = Depends(get_db), current_admin=Depends(admin_only)
):
    """
    HITL 검수 및 오버라이드가 완료된(APPROVED, REJECTED) 처리 내역 전체 조회
    """
    return service.get_completed_hitl_tasks(session)
