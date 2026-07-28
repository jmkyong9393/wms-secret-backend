import pytest
from pathlib import Path
from app.ai.agents import YOLO_HIGH_RECALL_MODEL_PATH, YOLO_HIGH_PRECISION_BASE_MODEL_PATH, DefectDetail, VisionResult
from app.models.wms import ReturnJob

def test_yolo_best_model_path_exists():
    """
    [단체 검증] YOLOv8 앙상블 가중치 파일 존재 및 용량 검증
    - Model 1 (yolov8_high_recall_best.pt) 및 Model 2 (yolov8_high_precision_base.pt) 파일 수신 확인
    """
    assert YOLO_HIGH_RECALL_MODEL_PATH.exists()
    assert YOLO_HIGH_PRECISION_BASE_MODEL_PATH.exists()
    assert YOLO_HIGH_RECALL_MODEL_PATH.stat().st_size > 40 * 1024 * 1024
    assert YOLO_HIGH_PRECISION_BASE_MODEL_PATH.stat().st_size > 40 * 1024 * 1024
  # 40MB 이상의 최신 가중치 파일

def test_vision_result_bbox_and_jsonb_serialization():
    """
    [기능 검증] YOLO BBox 좌표 및 DefectDetail의 PostgreSQL JSONB 직렬화 검증
    - 비전 추론 결과로 반환된 BBox 좌표 [x1, y1, x2, y2] 및 confidence 점수가 
      JSONB 딕셔너리로 깨짐 없이 올바르게 변환되는지 확인합니다.
    """
    # 1. 규격화된 결함 객체 생성 (COVER_TEAR: 찢김 결함)
    defect = DefectDetail(
        code="COVER_TEAR",
        description="도서 우측 하단 표지 찢김 관찰",
        ratio=15,
        bbox=[142.5, 88.0, 320.1, 245.6], # BBox [x1, y1, x2, y2]
        confidence=0.955
    )
    
    # 2. 비전 분석 최종 결과 구조체 포장
    result = VisionResult(
        is_mint=False,
        defects=[defect],
        special_notes="도서관 소장 도장 포착"
    )
    
    # 3. PostgreSQL JSONB 컬럼 저장을 위한 딕셔너리 직렬화
    defects_jsonb = [d.model_dump() for d in result.defects]
    
    # 4. 필드 검증
    assert len(defects_jsonb) == 1
    assert defects_jsonb[0]["code"] == "COVER_TEAR"
    assert defects_jsonb[0]["bbox"] == [142.5, 88.0, 320.1, 245.6]
    assert defects_jsonb[0]["confidence"] == 0.955
    assert result.special_notes == "도서관 소장 도장 포착"

def test_return_job_model_fields():
    """
    [기능 검증] ReturnJob DB 모델의 JSONB 및 메모 필드 맵핑 검증
    - PostgreSQL return_jobs 테이블의 defect_details (JSONB), special_notes (AI 특이사항),
      human_issue_notes (HITL 수동 메모) 필드가 정상적으로 매핑되는지 확인합니다.
    """
    job = ReturnJob(
        book_id="00000000-0000-0000-0000-000000000001",
        status="APPROVED",
        defect_details=[{"code": "COVER_TEAR", "bbox": [10, 20, 30, 40]}],
        special_notes="도서관 도장 찍힘",
        human_issue_notes="관리자 수동 정산 승인"
    )
    
    assert job.defect_details[0]["code"] == "COVER_TEAR"
    assert job.special_notes == "도서관 도장 찍힘"
    assert job.human_issue_notes == "관리자 수동 정산 승인"

