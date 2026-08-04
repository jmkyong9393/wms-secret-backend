import uuid

from app.ai.wbf_detector import MODEL_RECALL_PATH, MODEL_PRECISION_PATH, MODEL_DOODLE_PATH
from app.ai.agents import DefectDetail, VisionResult
from app.models.wms import ReturnJob


def test_wbf_ensemble_model_paths_exist():
    """
    [단체 검증] WBF 3-YOLO 앙상블 가중치 파일 존재 및 용량 검증
    (app/ai/wbf_detector.py의 실제 경로 상수 기준 - Model 3 Doodle OCR 포함 3개 전부 확인)
    """
    for path in (MODEL_RECALL_PATH, MODEL_PRECISION_PATH, MODEL_DOODLE_PATH):
        assert path.exists(), f"YOLO 가중치 파일이 없습니다: {path}"
        assert path.stat().st_size > 40 * 1024 * 1024, f"가중치 파일이 비정상적으로 작습니다: {path}"


def test_vision_result_bbox_and_jsonb_serialization():
    """
    [기능 검증] DefectDetail의 bbox/confidence/text_overlap/image_index 및
    VisionResult.special_notes가 PostgreSQL JSONB 저장을 위한 dict로
    깨짐 없이 직렬화되는지 검증 (app.ai.agents의 실제 스키마 기준).
    """
    defect = DefectDetail(
        type="DMG_EXT_TEAR",
        ratio=15,
        preliminary_deduction=15,
        bbox={"xmin": 142, "ymin": 88, "xmax": 320, "ymax": 245},
        confidence=0.955,
        text_overlap=True,
        image_index=1,
    )

    result = VisionResult(
        is_mint=False,
        defects=[defect],
        special_notes="도서관 소장 도장 포착",
    )

    defects_jsonb = [d.model_dump() for d in result.defects]

    assert len(defects_jsonb) == 1
    assert defects_jsonb[0]["type"] == "DMG_EXT_TEAR"
    assert defects_jsonb[0]["bbox"] == {"xmin": 142, "ymin": 88, "xmax": 320, "ymax": 245}
    assert defects_jsonb[0]["confidence"] == 0.955
    assert defects_jsonb[0]["text_overlap"] is True
    assert defects_jsonb[0]["image_index"] == 1
    assert result.special_notes == "도서관 소장 도장 포착"
    assert result.is_mint is False


def test_defect_detail_optional_fields_default_safely():
    """
    [기능 검증] bbox/confidence/image_index를 채우지 않아도(WBF 후보가 없는 경우 등)
    DefectDetail이 예외 없이 생성되는지 - Vision Agent가 GPT-4o 단독 폴백 시에도
    안전하게 동작해야 한다.
    """
    defect = DefectDetail(type="DMG_INT_DOODLE", ratio=8, preliminary_deduction=10)
    assert defect.bbox is None
    assert defect.confidence is None
    assert defect.text_overlap is False
    assert defect.image_index is None


def test_return_job_agent_logs_jsonb_roundtrip():
    """
    [기능 검증] ReturnJob.agent_logs(JSONB)에 Vision Agent 판독 결과(defects, is_mint,
    special_notes)를 중첩 dict로 저장했을 때 속성 접근이 정상 동작하는지 검증.
    (실제 ReturnJob 모델에는 defect_details/special_notes 전용 컬럼이 없고,
    app.ai.langgraph_wrapper.convert_final_state_to_worker_result가 만드는 것과 동일하게
    agent_logs JSONB 하나에 전부 담는 구조 - DB 세션 없이 객체 생성만으로 검증 가능)
    """
    defect = DefectDetail(
        type="DMG_EXT_TEAR", ratio=15, preliminary_deduction=15,
        bbox={"xmin": 10, "ymin": 20, "xmax": 30, "ymax": 40},
    )
    vision_result = VisionResult(is_mint=False, defects=[defect], special_notes="도서관 도장 찍힘")

    job = ReturnJob(
        book_id=uuid.uuid4(),
        status="APPROVED",
        ubci_score=78,
        agent_logs={
            "is_mint": vision_result.is_mint,
            "defects": [d.model_dump() for d in vision_result.defects],
            "special_notes": vision_result.special_notes,
        },
    )

    assert job.agent_logs["is_mint"] is False
    assert job.agent_logs["defects"][0]["type"] == "DMG_EXT_TEAR"
    assert job.agent_logs["defects"][0]["bbox"]["xmax"] == 30
    assert job.agent_logs["special_notes"] == "도서관 도장 찍힘"
    assert job.status == "APPROVED"
