"""Admin HITL 요청·응답 스키마 (router.py에서 분리, 2026-09-01 계층 정리)."""

from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class HitlOverrideRequest(BaseModel):
    ticketId: str = Field(..., description="Job Task ID or ID")
    decision: str = Field(
        ...,
        description="APPROVE_DOWNGRADE, REJECT_RETURN, REJECT_DISCARD, APPROVE_NORMAL",
    )
    targetGrade: Optional[str] = Field(None, description="A, B, C, S 등")
    primaryReasonCode: str = Field(..., description="DMG_EXT_CRUSH 등 단일 사유")
    reasonComment: Optional[str] = Field(None)
    # 더 이상 AdminAuditLog에 그대로 쓰이지 않는다 - 서버가 exclude/adopt/
    # editedBboxes 반영 후 job.agent_logs.defects로부터 감사 로그용 좌표를 직접 재조립한다
    # (프론트가 보내는 이 값은 모달을 연 시점의 스냅샷이라 편집분을 반영하지 못했다).
    # 구버전 프론트와의 호환을 위해 필드는 유지하되 무시한다.
    defectCoordinates: Optional[List[Any]] = Field(default_factory=list)
    reviewDurationMs: Optional[int] = Field(0, description="관리자 체류 시간")
    # --- BBox 채택/제외 ---
    # 검수자가 화면에서 결함을 눌러 판정을 고친 결과. 인덱스는 agent_logs.defects /
    # agent_logs.yolo_candidates 배열 기준이다.
    excludedDefectIndexes: Optional[List[int]] = Field(
        default_factory=list, description="오탐으로 판단해 감점에서 제외할 결함 인덱스"
    )
    adoptedCandidateIndexes: Optional[List[int]] = Field(
        default_factory=list,
        description="AI가 놓쳤으나 실제 결함으로 채택할 YOLO 후보 인덱스",
    )
    # --- BBox 좌표 직접 수정 ---
    # 검수자가 화면에서 확정 결함의 박스를 드래그해 위치/크기를 고친 결과. index는
    # agent_logs.defects 배열 기준(excludedDefectIndexes와 동일 인덱스 공간)이며,
    # xmin/ymin/xmax/ymax는 Vision Agent와 동일한 0~1000 상대좌표다.
    editedBboxes: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list,
        description="검수자가 직접 고친 결함 좌표. [{index, xmin, ymin, xmax, ymax}]",
    )
    # --- BBox 신규 추가 ---
    # AI가 아예 놓친 결함을 검수자가 직접 그려 넣은 것. defects 배열에 새 항목으로 추가된다.
    addedBboxes: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list,
        description="검수자가 직접 그린 신규 결함. [{type, xmin, ymin, xmax, ymax, imageIndex}]",
    )


class BulkOverridePayload(BaseModel):
    items: List[HitlOverrideRequest]


class HitlTaskResponse(BaseModel):
    id: UUID
    book_id: UUID
    book_title: Optional[str] = None
    isbn: Optional[str] = None
    cover_image_url: Optional[str] = None
    image_urls: List[str] = Field(default_factory=list)
    status: str
    ubci_score: Optional[int] = None
    agent_logs: Optional[Dict[str, Any]] = None
    created_at: str
    # 처리(상태 변경) 시각. 목록 정렬 기준이자 "언제 회수/이관됐는가" 표시용.
    updated_at: Optional[str] = None
    # 관리자가 남긴 최신 메모. 프론트가 이 필드를 읽는데 종전에는 서버가 채우지 않아
    # 회수 사유·결재 코멘트는 DB 값을 그대로 노출한다 — 고정 문구로 덮지 않는다.
    human_issue_notes: Optional[str] = None


class ScorePreviewRequest(BaseModel):
    """BBox 편집분으로 점수만 미리 계산한다 (저장하지 않음)."""

    excludedDefectIndexes: Optional[List[int]] = Field(default=None)
    adoptedCandidateIndexes: Optional[List[int]] = Field(default=None)
    editedBboxes: Optional[List[Dict[str, Any]]] = Field(default=None)
    addedBboxes: Optional[List[Dict[str, Any]]] = Field(default=None)


class RecallToHitlRequest(BaseModel):
    reason: Optional[str] = Field(
        default=None, description="회수 사유 (감사 추적에 남는다)"
    )
