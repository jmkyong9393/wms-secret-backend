import operator
from typing import TypedDict, List, Annotated, Literal, Optional
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

# Critic 에이전트가 뱉어낼 수 있는 명시적 에러 코드 (Reason Code)
ReasonCode = Literal[
    "UBCI_POLICY_VIOLATION", "BBOX_MISMATCH", "QUALITY_ERROR", "OK",
    "REJECT", "MAX_RETRIES_AMBIGUOUS_HITL", "BOUNDARY_AMBIGUOUS_HITL", "HUMAN_REQUIRED",
    "AWAITING_HUMAN_REVIEW",  # Supervisor가 HITL 이관을 지시해 관리자 결재를 대기 중인 상태
    # [2026-08-06 프리즈 예외] 촬영 전 컷이 도서 미식별로 제외되어 판독된 컷이 0장인 상태.
    # Critic이 아니라 Supervisor가 세우는 코드다 (판독 커버리지는 전 보고를 종합해야 알 수 있음).
    "NO_VALID_IMAGE_HITL",
]

# Supervisor가 Critic 보고를 수령한 뒤 내리는 최종 지휘 결정.
# Critic은 "무엇이 문제인가"(reason_code)까지만 보고하고, "그래서 어디로 보낼 것인가"는
# 전적으로 Supervisor가 판단한다 - 지휘 책임을 Supervisor 한 곳에 집중시키기 위한 분리.
SupervisorDecision = Literal[
    "RETRY_VISION",     # 재검수 지시 -> Vision Agent로 반려
    "ESCALATE_HUMAN",   # HITL 이관 지시 -> human_node (관리자 수동 결재 대기)
    "ISSUE_REPORT",     # 보증서 발급 승인 -> Report Agent
]

class WMSInspectionState(TypedDict):
    """
    LangGraph 에이전트 간 메모리를 공유하기 위한 전역 상태(State) 객체.
    각 에이전트 노드들이 수행한 결과(Vision 판독 결과, UBCI 산정 점수 등)를 순차적으로 누적하고,
    Supervisor 라우팅의 조건부 판단을 위한 핵심 파라미터로 사용됩니다.
    """
    messages: Annotated[List[BaseMessage], add_messages]

    # 0. 입력 (검수 대상 이미지 경로/URL 목록 - 로컬 파일 경로 또는 S3/HTTP URL 혼용 가능)
    image_paths: Optional[List[str]]
    # 프론트가 실제로 <img>에 물리는 공개 URL(CloudFront). image_paths는 YOLO 추론용 로컬
    # 경로일 수 있으므로, BBox를 화면 이미지에 매핑하려면 표시용 URL을 별도로 들고 있어야 한다.
    display_image_urls: Optional[List[str]]
    # 도서명. Policy Agent의 수험서/문제집 낙서 -15점 단일 Cap 판정(is_workbook)에 쓰인다.
    # [수정 이력] 이 필드가 State에 선언되어 있지 않아 LangGraph가 값을 통째로 버렸고,
    # policy_agent의 state.get("book_title")이 항상 빈 문자열이라 Cap이 한 번도 발동하지 않았다.
    book_title: Optional[str]

    # 1-0. Detector Node (WBF 3-YOLO 앙상블 사전탐지 - LLM 미사용)
    # [분리 배경] 예전에는 vision_agent 하나가 YOLO 앙상블 + GPT-4o VLM + GPT-4o-mini 검증
    # 3단계를 모두 수행하는 내부 모놀리스였다. 그래서 VLM 호출이 실패하면 YOLO가 이미 찾아둔
    # 후보까지 함께 유실되어 "결함 0건 = MINT"로 둔갑했다. 결정론적 탐지(YOLO)와 LLM 판독을
    # 별도 노드로 분리해, VLM이 죽어도 YOLO 결과는 살아남게 한다.
    yolo_candidates: Optional[list]   # WBF 앙상블 사전탐지 후보 (image_index/type/bbox/confidence)
    detector_text: Optional[str]      # Detector 산출 서술

    # 1. Vision Agent (VLM 정밀 판독)
    is_mint: Optional[bool]           # 결함이 전혀 없는 무결점 판정 여부 (판독 실패 시 None)
    # VLM 판독 자체가 실패했는지. "판독했더니 흠이 없다"(is_mint=True)와
    # "판독을 못 했다"(vision_failed=True)를 구분하기 위한 필드. 이 둘을 같은 것으로
    # 취급했다가 OpenAI 키 만료 시 전 건이 MINT 100점으로 자동 승인된 사고가 있었다.
    vision_failed: Optional[bool]
    defects: Optional[list]           # 결함 내역 (BBox 등)
    special_notes: Optional[str]      # UBCI 감점과 무관한 정성적 관찰 (도서관 도장, 부록 누락 등)
    # [2026-08-04 조장 승인 확장] 도서가 식별되지 않는 촬영 컷 인덱스 목록 (작업자 얼굴만
    # 찍힘, 빈 배경 등). HITL/상세 화면이 해당 컷을 "도서 미식별"로 구분·필터링하는 근거.
    invalid_image_indexes: Optional[list]

    # 2. Policy Agent (UBCI 대조)
    ubci_score: Optional[int]         # 훼손도 기반 차감 점수 (100점 만점)
    # 각 감점 항목의 근거 조항 (RAG 검색 결과). 점수 산정에는 일절 관여하지 않고,
    # 이미 확정된 감점의 출처를 규정집에서 찾아 붙인 grounding 정보다.
    # [{defect_type, chunk_id, doc_title, clause_ref, authority_level, excerpt, similarity}]
    deduction_basis: Optional[list]
    # 증거 대조 검증이 결함을 전건 기각해 감점 근거가 남지 않은 상태.
    # "흠이 없다"가 아니라 "판독하지 못했다"이므로 무결점 등급을 주지 않는 근거로 쓴다.
    score_unverified: Optional[bool]

    # 3. Critic Agent (교차 검증 및 환각 방어)
    reason_code: Optional[ReasonCode] # 검증 결과 코드 (OK면 통과)
    repair_directive: Optional[str]   # Policy가 다시 계산해야 할 때 주는 수정 지시서
    revision_count: int               # 무한 루프(핑퐁) 방지용 카운터

    # 4. Supervisor (중앙 지휘 - Critic 보고 수령 후 최종 라우팅 결정)
    supervisor_decision: Optional[SupervisorDecision] # Supervisor가 내린 지휘 결정
    supervisor_rationale: Optional[str]               # 그 결정의 근거 (감사 추적용)

    # 5. Human-In-The-Loop (수동 개입용 - MemorySaver 연동)
    human_feedback: Optional[str]     # 관리자 입력값 ("approve", "reject", "recalculate" 등)

    # 5. Output
    final_report: Optional[str]       # 소비자 제공용 사유서 (JSON 문자열)

    # 6. 감사 추적용 각 Agent의 실제 산출 서술 (Explainer 패널이 렌더하는 원본 데이터)
    #
    # [수정 이력] policy_agent는 예전부터 policy_text를, report_agent는 report_text를 반환하고
    # 있었지만 State TypedDict에 선언이 없어 LangGraph가 해당 키를 전부 폐기했다. 그 결과
    # DB agent_logs에 어떤 Agent 서술도 남지 않았고, 프론트 "Explainer Agent 실시간 진단 기록"
    # 패널은 100% 프론트에서 조립한 가짜 문자열을 "PostgreSQL DB Verified" 뱃지와 함께
    # 표시하고 있었다. 아래 필드들이 그 패널의 실제 데이터 소스가 된다.
    vision_text: Optional[str]        # Vision Agent 판독 요약
    # 등급 산정과 무관한 부가 플래그: MINT 무결점 판정이라 관리자 개입 없이 자동 매입/환불
    # 대상이 되는가. (예전에는 이 값이 그래프 라우팅 분기 자체였으나, 검증 노드를 건너뛰게
    # 만들어 위험했다. 이제는 판정 결과에 붙는 표식일 뿐 경로를 바꾸지 않는다.)
    auto_refund_eligible: Optional[bool]
    policy_text: Optional[str]        # Policy Agent UBCI 감점 산식 서술
    critic_text: Optional[str]        # Critic Agent 교차검증 결과 서술
    report_text: Optional[str]        # Report Agent 보증서 전문
    certificate: Optional[dict]       # Report Agent가 생성한 구조화 보증서 문서

    # 실제 실행된 노드 이름 목록. MINT Fast-track은 Vision -> auto_refund로 직행해
    # Policy/Critic이 아예 실행되지 않으므로, UI가 "4개 다 돌았다"고 거짓 표기하지 않도록
    # 어느 노드가 실제로 돌았는지를 누적 기록한다 (operator.add 리듀서로 노드별 append).
    executed_agents: Annotated[List[str], operator.add]
