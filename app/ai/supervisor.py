import os
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .state import WMSInspectionState
from .agents import (
    vision_agent,
    policy_agent,
    critic_agent,
    human_node,
    auto_refund_agent,
    report_agent
)

# LangSmith Tracing 활성화 (LLMOps)
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_PROJECT"] = "WMS_AI_Project"
os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"

def route_from_supervisor(state: WMSInspectionState) -> str:
    """
    Supervisor Agent 라우팅 판단 로직 (Critic Agent 평가 보고 수령 후 제어)
    - Sequential Pipeline: Vision -> Policy -> Critic -> Supervisor
    - Supervisor 판단 라우팅:
      1) vision_agent (재검수 필요 시 Retry)
      2) human_node (판정 애매 / 경계선 58~66점 / 최대 재시도 초과 시 HITL 이관)
      3) report_agent (정상 검증 완료 OK 시 보증서 발급)
    """
    reason = state.get("reason_code")
    revision = state.get("revision_count", 0)

    # 1. Critic 보고 수령: 판정 결과 애매성(경계선 58~66점) 또는 최대 루프(2회) 초과 시 ➔ human_node(HITL)로 이관
    if reason in ["MAX_RETRIES_AMBIGUOUS_HITL", "BOUNDARY_AMBIGUOUS_HITL", "HUMAN_REQUIRED"] or revision >= 2:
        print(f"[Supervisor Manager] Critic 보고 수령 (사유: {reason}) -> human_node(HITL) 관리자 수동 이관 결정")
        return "human_node"

    # 2. Critic 보고 수령: 재검수 필요 (REJECT) ➔ vision_agent로 retry 전송
    if reason == "REJECT":
        print(f"[Supervisor Manager] Critic 재검수 지시 보고 수령 (재시도 {revision}회) -> Vision Agent로 retry 재전송")
        return "vision_agent"

    # 3. Critic 보고 수령: 타당성 검증 통과 (OK) ➔ report_agent로 바로 보증서 발급 승인
    print("[Supervisor Manager] Critic 무결성 통과 보고 수령 (OK) -> Report Agent로 보증서 발급 승인")
    return "report_agent"

def supervisor_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    Supervisor 중앙 관리자 노드 (Critic Agent의 보고를 수령하여 최종 분기 제어)
    """
    print("[Supervisor Manager] Critic Agent의 검증 보고서를 수령하여 라우팅 판단을 수행합니다.")
    return state

def build_supervisor_graph():
    """
    LangGraph 순차 파이프라인 + Critic 판단 Supervisor 분기 구조
    Vision ➔ Policy ➔ Critic ➔ Supervisor ➔ [Vision(Retry) | HITL | Report]
    """
    builder = StateGraph(WMSInspectionState)

    # 1. 노드 등록 (add_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("vision_agent", vision_agent)
    builder.add_node("policy_agent", policy_agent)
    builder.add_node("critic_agent", critic_agent)
    builder.add_node("human_node", human_node)
    builder.add_node("auto_refund_agent", auto_refund_agent)
    builder.add_node("report_agent", report_agent)
    
    # 2. 파이프라인 진입 (START ➔ Vision Agent)
    builder.add_edge(START, "vision_agent")

    # 3. 순차 파이프라인 에지 (Vision ➔ Policy ➔ Critic ➔ Supervisor)
    builder.add_edge("vision_agent", "policy_agent")
    builder.add_edge("policy_agent", "critic_agent")
    builder.add_edge("critic_agent", "supervisor")

    # 4. Supervisor 분기 에지 (Critic 보고서에 따라 Supervisor가 다음 라우팅 결정)
    builder.add_conditional_edges(
        "supervisor", 
        route_from_supervisor,
        {
            "vision_agent": "vision_agent",
            "human_node": "human_node",
            "report_agent": "report_agent"
        }
    )
    
    # 5. HITL 조치 완료 후 Report Agent로 보증서 발급 연결
    builder.add_edge("human_node", "report_agent")
    
    # 6. End 엣지 (종료)
    builder.add_edge("auto_refund_agent", END)
    builder.add_edge("report_agent", END)
    
    # 7. MemorySaver 연동 (HITL 중단점)
    memory = MemorySaver()
    return builder.compile(checkpointer=memory)

app_graph = build_supervisor_graph()
