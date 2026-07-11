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
    Supervisor Agent의 동적 라우팅 판단 로직 (Star Topology)
    """
    # 1. 안전장치: 무한 루프 방지 (Max Retries가 2 이상이면 human_node 로 강제 에스컬레이션)
    revision = state.get("revision_count", 0)
    if revision >= 2:
        return "human_node"
        
    # 2. 초기 상태: 결함 판독이 아직 안 되었으면 vision_agent 반환
    if state.get("is_mint") is None and state.get("defects") is None:
        return "vision_agent"
        
    # 3. [Fast-track]: is_mint가 True이면 auto_refund_agent 반환
    if state.get("is_mint") is True:
        return "auto_refund_agent"
        
    # 4. 결함이 있는데 UBCI 산정이 아직 안 되었으면 policy_agent 반환 (reason_code가 OK가 아니면 재진입)
    if state.get("ubci_score") is None:
        return "policy_agent"
        
    # 5. Critic 검증 통과(OK) 확인
    reason = state.get("reason_code")
    if reason is None:
        return "critic_agent" # 점수는 매겼으나 아직 검증을 안 받음
    elif reason == "OK":
        return "report_agent" # 검증 완벽 통과 -> 최종 레포트 작성
    else:
        # 검증 실패 (UBCI_POLICY_VIOLATION 등) -> Policy Agent로 되돌려보냄
        return "policy_agent"

def supervisor_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    Supervisor 노드 자체는 상태를 변경하지 않고 패스스루 역할을 합니다.
    """
    return state

def build_supervisor_graph():
    """
    LangGraph Supervisor 파이프라인 (Star Topology) 구성
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
    
    # 2. 시작 시 무조건 supervisor로 이동 (add_edge)
    builder.add_edge(START, "supervisor")

    # 3. Supervisor 라우팅 엣지 (supervisor -> 각 에이전트)
    builder.add_conditional_edges(
        "supervisor", 
        route_from_supervisor,
        {
            "vision_agent": "vision_agent",
            "policy_agent": "policy_agent",
            "critic_agent": "critic_agent",
            "human_node": "human_node",
            "auto_refund_agent": "auto_refund_agent",
            "report_agent": "report_agent"
        }
    )
    
    # 4. Star Topology: 워커 에이전트 작업 후 다시 supervisor로 반환
    builder.add_edge("vision_agent", "supervisor")
    builder.add_edge("policy_agent", "supervisor")
    builder.add_edge("critic_agent", "supervisor")
    builder.add_edge("human_node", "supervisor") # 관리자 처리 후 다시 중앙으로
    
    # 5. End 엣지 (종료)
    builder.add_edge("auto_refund_agent", END)
    builder.add_edge("report_agent", END)
    
    # 6. MemorySaver 연동 (HITL 중단점)
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory, interrupt_before=["human_node"])
    return graph

# 전역 그래프 인스턴스
app_graph = build_supervisor_graph()
