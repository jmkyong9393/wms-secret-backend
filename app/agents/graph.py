from typing import TypedDict, Annotated, List, Optional
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, END, START
from langchain_core.messages import HumanMessage
from app.agents.vision_agent import VisionAgent
from app.agents.policy_agent import run_policy_node
from app.agents.critic_agent import run_critic_node

class AgentState(TypedDict):
    """Role-based LLMOps 아키텍처가 적용된 LangGraph State"""
    # LLM 대화 기록 추적용
    messages: Annotated[List, add_messages]
    
    job_id: str
    image_path: str
    
    # 1. Vision Agent의 출력값
    has_defect: Optional[bool]
    defect_description: Optional[str]
    
    # 2. Policy Agent의 출력값 (RAG 기반)
    matched_rule: Optional[str]
    ubci_grade: Optional[str]
    ubci_score: Optional[int]
    
    # 3. Critic Agent의 출력값 및 루프 제어 변수
    critique: Optional[str]
    needs_hitl: Optional[bool]
    retry_count: int

def run_vision_node(state: AgentState):
    print("--- [Node: Vision Agent] 가동 ---")
    vision = VisionAgent()
    report = vision.analyze_image(state["image_path"])
    
    # LLM이 읽을 수 있도록 결과를 HumanMessage로 변환하여 저장
    msg_content = f"Vision 분석 결과: 훼손 감지={report.has_defect}, 훼손 상세={report.defect_description}"
    return {
        "messages": [HumanMessage(content=msg_content)],
        "has_defect": report.has_defect,
        "defect_description": report.defect_description,
        "retry_count": 0,
        "needs_hitl": False
    }

def hitl_node(state: AgentState):
    """최대 재시도 횟수를 초과했을 때 관리자 수동 검수로 빠지는 노드"""
    print("--- [Node: HITL] 관리자 수동 검수 (Human-in-the-loop) 큐 대기 ---")
    return {"needs_hitl": True}

def critic_router(state: AgentState):
    """Critic의 결과에 따라 조건부 루프를 제어하는 라우터"""
    critique = state.get("critique", "")
    retry_count = state.get("retry_count", 0)
    
    if "APPROVED" in critique.upper():
        print("--- [Router] Critic 판정: APPROVED. 워크플로우 종료 ---")
        return "END"
    else:
        if retry_count < 1:
            print("--- [Router] Critic 판정: REJECTED. Policy Agent 1회 재시도 (재생각 기회 부여) ---")
            return "policy_agent"
        else:
            print("--- [Router] 재시도 한도 초과. HITL 프로세스로 안전하게 전환 ---")
            return "hitl_node"

def build_wms_graph():
    """WMS 멀티 에이전트 LangGraph 컴파일"""
    workflow = StateGraph(AgentState)
    
    # 노드 추가
    workflow.add_node("vision_agent", run_vision_node)
    workflow.add_node("policy_agent", run_policy_node)
    workflow.add_node("critic_agent", run_critic_node)
    workflow.add_node("hitl_node", hitl_node)
    
    # 엣지 연결 (조건부 엣지 포함)
    workflow.add_edge(START, "vision_agent")
    workflow.add_edge("vision_agent", "policy_agent")
    workflow.add_edge("policy_agent", "critic_agent")
    
    # Critic의 결과물에 따른 분기 (APPROVED / REJECTED)
    workflow.add_conditional_edges(
        "critic_agent",
        critic_router,
        {
            "END": END,
            "policy_agent": "policy_agent",
            "hitl_node": "hitl_node"
        }
    )
    workflow.add_edge("hitl_node", END)
    
    app = workflow.compile()
    return app
