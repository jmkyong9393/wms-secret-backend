"""
LangGraph 검수 파이프라인 에이전트 패키지.

[구조] 그래프의 노드 하나 = 파일 하나. 확장(에이전트 추가·모델 교체·노드별 테스트)이
정확히 이 축에서 일어나므로 노드 경계로 자른다.

    common.py    노드 둘 이상이 쓰는 상수·이미지 유틸
    schemas.py   구조화 출력 스키마 (with_structured_output 강제 대상)
    llm.py       모델 인스턴스 (프리즈 규정: Vision=GPT-4o, 나머지=GPT-4o-mini)
    detector.py  Detector 노드 - WBF 3-YOLO 사전탐지 (LLM 없음)
    vision.py    Vision Agent - VLM 판독 + 증거 대조 검증
    policy.py    Policy Agent - Stage A(결정론 감점) + Stage B(규정 해석)
    critic.py    Critic Agent - Stage A(사실 대조) + Stage B(타당성 심사)
    report.py    Report Agent - 보증서 발행 + human_node(HITL 인계)
    restock.py   Restock Agent - 자동 발주 제안 (검수 그래프와 별개 경로)

[이 파일이 re-export만 하는 이유]
종전에는 위 전부가 __init__.py 한 파일(2,443줄)에 있었다. 노드 파일로 나누면서도 `from app.ai.agents import policy_agent` 형태의 기존 호출부(워커·admin·orders·po·rag_service 등 12곳)를 그대로 두기 위해 여기서 전부 다시 내보낸다. 호출부 수정 0건.

[순환 import 주의]
core/rag_service.py가 `from app.ai.agents import llm_mini`를 함수 안에서 지연 import하고, policy.py는 rag_service를 역시 함수 안에서 지연 import한다. 이 두 지연을 모듈 최상단으로 올리면 순환이 즉시 성립한다. 옮기지 말 것.
"""

from app.ai.agents.common import (
    DEFECT_TRANSLATION_MAP,
    INNER_PAGE_EXCLUDED_TYPES,
    TRACK1_IMAGE_COUNT,
    VLM_MAX_IMAGE_EDGE,
    YOLO_TO_UBCI_TYPE,
    _downscale_for_vlm,
    _ensure_local_path,
    _is_inner_page,
    _load_image_as_base64,
)
from app.ai.agents.schemas import (
    CertificateDocument,
    CriticResult,
    CriticVerdict,
    DefectDetail,
    DefectEvidenceVerdict,
    DefectFinding,
    InnerPageRegion,
    PolicyClauseCitation,
    PolicyResult,
    QualityCertificateResult,
    ReturnPolicyVerdict,
    VisionResult,
)
from app.ai.agents.llm import llm_mini, llm_verify, llm_vlm
from app.ai.agents.detector import build_yolo_hint, detector_node
from app.ai.agents.vision import (
    VERIFY_CROP_EXPAND,
    VERIFY_CROP_MAX_ASPECT,
    VERIFY_CROP_MAX_LONG,
    VERIFY_CROP_MAX_UPSCALE,
    VERIFY_CROP_MIN_SHORT,
    VERIFY_CROP_SIZE,
    VERIFY_EXEMPT_CONF,
    VISION_PROMPT_BASE,
    UNCLEAR_EXCLUDES_DEDUCTION,
    WEAR_AUTO_ADOPT,
    _bbox_iou,
    _crop_around_bbox,
    verify_defects_with_images,
    vision_agent,
)
from app.ai.agents.policy import (
    BINDING_AUTHORITY,
    _edge_wear_profile,
    _effective_ratio,
    evaluate_return_policy,
    policy_agent,
)
from app.ai.agents.critic import critic_agent, critic_stage_a_integrity_check
from app.ai.agents.report import (
    _LIABILITY_WORDS,
    _fallback_certificate,
    _grade_label,
    _sanitize_policy_basis,
    build_certificate_document,
    human_node,
    report_agent,
)

__all__ = [
    # 그래프 노드
    "detector_node",
    "vision_agent",
    "policy_agent",
    "critic_agent",
    "report_agent",
    "human_node",
    # 노드 보조 함수
    "build_yolo_hint",
    "verify_defects_with_images",
    "evaluate_return_policy",
    "critic_stage_a_integrity_check",
    "build_certificate_document",
    # 모델 인스턴스
    "llm_vlm",
    "llm_mini",
    "llm_verify",
    # 스키마
    "VisionResult",
    "PolicyResult",
    "CriticResult",
    "CriticVerdict",
    "PolicyClauseCitation",
    "ReturnPolicyVerdict",
    "DefectEvidenceVerdict",
    "CertificateDocument",
    "DefectFinding",
    "DefectDetail",
    "InnerPageRegion",
    "QualityCertificateResult",
    # 상수
    "DEFECT_TRANSLATION_MAP",
    "YOLO_TO_UBCI_TYPE",
    "INNER_PAGE_EXCLUDED_TYPES",
    "TRACK1_IMAGE_COUNT",
    "VLM_MAX_IMAGE_EDGE",
    "VISION_PROMPT_BASE",
    "BINDING_AUTHORITY",
]
