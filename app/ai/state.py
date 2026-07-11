from typing import TypedDict, List, Annotated, Literal, Optional
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

# Critic 에이전트가 뱉어낼 수 있는 명시적 에러 코드 (Reason Code)
ReasonCode = Literal["UBCI_POLICY_VIOLATION", "BBOX_MISMATCH", "QUALITY_ERROR", "OK"]

class WMSInspectionState(TypedDict):
    """
    LangGraph 에이전트 간 메모리를 공유하기 위한 전역 상태(State) 객체.
    각 에이전트 노드들이 수행한 결과(Vision 판독 결과, UBCI 산정 점수 등)를 순차적으로 누적하고, 
    Supervisor 라우팅의 조건부 판단을 위한 핵심 파라미터로 사용됩니다.
    """
    messages: Annotated[List[BaseMessage], add_messages]
    
    # 1. Vision Agent (1차 판독)
    is_mint: Optional[bool]           # 정상품(새 책) 여부 -> True일 경우 Fast-track 트리거
    defects: Optional[list]           # 결함 내역 (BBox 등)
    
    # 2. Policy Agent (UBCI 대조)
    ubci_score: Optional[int]         # 훼손도 기반 차감 점수 (100점 만점)
    
    # 3. Critic Agent (교차 검증 및 환각 방어)
    reason_code: Optional[ReasonCode] # 검증 결과 코드 (OK면 통과)
    repair_directive: Optional[str]   # Policy가 다시 계산해야 할 때 주는 수정 지시서
    revision_count: int               # 무한 루프(핑퐁) 방지용 카운터
    
    # 4. Human-In-The-Loop (수동 개입용 - MemorySaver 연동)
    human_feedback: Optional[str]     # 관리자 입력값 ("approve", "reject", "recalculate" 등)
    
    # 5. Output
    final_report: Optional[str]       # 소비자 제공용 사유서 (JSON 문자열)
