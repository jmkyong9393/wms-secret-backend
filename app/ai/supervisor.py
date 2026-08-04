import os
from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .state import WMSInspectionState
from .agents import (
    detector_node,
    vision_agent,
    policy_agent,
    critic_agent,
    human_node,
    report_agent
)

# LangSmith Tracing 활성화 (LLMOps)
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_PROJECT"] = "WMS_AI_Project"
os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"

# Supervisor의 지휘 결정 -> 실제 그래프 노드 매핑
_DECISION_TO_NODE = {
    "RETRY_VISION": "vision_agent",
    "ESCALATE_HUMAN": "human_node",
    "ISSUE_REPORT": "report_agent",
}


def route_from_supervisor(state: WMSInspectionState) -> str:
    """
    Supervisor가 supervisor_node에서 이미 내린 결정(supervisor_decision)을 그대로 따라
    다음 노드로 넘기기만 하는 순수 매핑 함수.

    [수정 이력] 예전에는 이 라우팅 함수가 reason_code를 직접 해석해 분기를 결정하고,
    supervisor_node는 print만 하는 껍데기였다. 그 결과 "Supervisor가 총괄 지휘한다"는
    설계 의도가 코드 어디에도 드러나지 않았고, 판단 근거도 상태에 남지 않아 추적이 불가능했다.
    이제 판단은 전적으로 supervisor_node가 수행하고, 이 함수는 그 결정을 집행만 한다.
    """
    decision = state.get("supervisor_decision")
    return _DECISION_TO_NODE.get(decision, "report_agent")

# [삭제 이력 - 프리즈 예외 승인 (2026-08-04)] route_from_vision() 제거.
#
# 이 함수는 is_mint=True면 Policy/Critic/Supervisor를 모두 건너뛰고 auto_refund_agent로
# 직행시켰다. 명분은 "비용 최적화 Fast-track"이었으나, 측정 결과 우회 대상 3개 노드
# (policy_agent / critic_agent / supervisor_node)는 **LLM을 단 한 번도 호출하지 않는
# 순수 결정론적 if/else 함수**였다. 즉 Fast-track이 절약하는 LLM 호출은 0건이었고,
# 보증서 생성(GPT-4o-mini 1회)은 auto_refund_agent와 report_agent 양쪽 모두에서
# 동일하게 발생했다. 절약 효과가 전혀 없는 대신, 금전적 확정(자동 매입/환불)이
# 환각 방어 담당 Critic의 검증을 건너뛴 채 Vision 단독 판정만으로 내려지는
# 심각한 구조적 위험만 남았다.
#
# 실제 사고: OpenAI 키 만료로 VLM이 401을 반환하자 defects가 빈 배열로 남았고,
# is_mint=True로 해석되어 모든 반품 도서가 검증 없이 UBCI 100점 MINT로 자동 매입 승인됐다.
#
# 이제 MINT도 동일하게 Policy -> Critic -> Supervisor 검증을 통과한다. 결함이 실제로
# 0건이면 Policy가 100점을 산출하고 Critic이 정합성을 확인하므로 판정 결과는 같으며,
# 경로가 하나로 합쳐져 검증 누락 구멍이 사라진다. "MINT 자동 매입"이라는 비즈니스
# 기능 자체는 state.auto_refund_eligible 플래그로 보존되어 워커가 집행한다.

def supervisor_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    Supervisor 중앙 지휘 노드.

    Vision(결함 판독) / Policy(UBCI 점수 산정) / Critic(교차 검증) 세 에이전트의 결과를
    **모두 수령하여 종합 판단**한 뒤, 다음 행동을 최종 결정한다. 각 하위 에이전트는 자기
    영역의 사실만 보고하고, "그래서 이 건을 어떻게 처리할 것인가"라는 지휘 판단은 오직
    이 노드만 내린다.

    [수정 이력] 이전에는 이 노드가 print 한 줄만 찍고 state를 그대로 반환하는 껍데기였고,
    실제 분기 판단은 route_from_supervisor()가 reason_code만 보고 수행했다. Supervisor가
    총괄한다는 설계 의도가 코드에 드러나지 않았고 판단 근거도 남지 않았다.
    """
    # --- 하위 3개 에이전트의 보고 수령 ---
    is_mint = state.get("is_mint")                    # Vision Agent
    defects = state.get("defects") or []              # Vision Agent
    special_notes = state.get("special_notes")        # Vision Agent
    ubci_score = state.get("ubci_score")              # Policy Agent
    reason = state.get("reason_code")                 # Critic Agent
    revision = state.get("revision_count", 0)         # Critic Agent

    print(
        f"[Supervisor Manager] 3-Agent 보고 종합 수령 "
        f"(Vision: 결함 {len(defects)}건/MINT={is_mint} | Policy: UBCI {ubci_score}점 | "
        f"Critic: {reason}, 재검수 {revision}회)"
    )

    # --- 종합 판단 ---
    # 1) HITL 이관: Critic이 애매성을 보고했거나(경계선 58~66점 / 최대 재시도 초과),
    #    재검수 루프가 한계에 도달한 경우. 자동 판정을 강행하지 않고 사람에게 넘긴다.
    if reason in ["MAX_RETRIES_AMBIGUOUS_HITL", "BOUNDARY_AMBIGUOUS_HITL", "HUMAN_REQUIRED"] or revision >= 2:
        decision = "ESCALATE_HUMAN"
        rationale = (
            f"Critic 애매성 보고({reason}) 및 재검수 {revision}회 누적. "
            f"Vision 결함 {len(defects)}건 / Policy UBCI {ubci_score}점으로는 자동 확정이 부적절하여 "
            f"관리자 수동 결재로 이관 결정."
        )

    # 2) 재검수 지시: Critic이 프로세스 비정상(점수 산출 누락 등)을 보고했고 아직 루프 여유가 있는 경우.
    elif reason == "REJECT":
        decision = "RETRY_VISION"
        rationale = (
            f"Critic 재검수 지시 보고(REJECT). Policy 점수 미산출(UBCI={ubci_score}) 상태로 "
            f"Vision Agent 재판독 지시 (재시도 {revision}/2회)."
        )

    # 3) 보증서 발급 승인: 세 에이전트 보고가 모두 정합적이고 Critic이 무결성을 확인한 경우.
    else:
        decision = "ISSUE_REPORT"
        note_str = f" / 특이사항: {special_notes}" if special_notes else ""
        rationale = (
            f"Critic 무결성 통과(OK). Vision 결함 {len(defects)}건 / Policy UBCI {ubci_score}점 "
            f"판정 정합성 확인{note_str}. 보증서 발급 승인."
        )

    # 로그 문자열에는 em-dash 같은 비 CP949 문자를 쓰지 않는다 (Windows 콘솔에서
    # UnicodeEncodeError로 파이프라인 자체가 죽는다 - Docker는 UTF-8이라 무증상)
    print(f"[Supervisor Manager] 지휘 결정: {decision} - {rationale}")

    return {
        "supervisor_decision": decision,
        "supervisor_rationale": rationale,
        "executed_agents": ["supervisor"],
        "messages": [AIMessage(content=f"[Supervisor] {decision}: {rationale}")],
    }

def build_supervisor_graph():
    """
    LangGraph 순차 파이프라인 + Critic 판단 Supervisor 분기 구조

        Detector(YOLO) ➔ Vision(GPT-4o) ➔ Policy ➔ Critic ➔ Supervisor
                                              ➔ [Vision(Retry) | HITL | Report]

    [구조 변경 이력 - 프리즈 예외 승인 (2026-08-04)]
    1) Detector Node 신설: WBF YOLO 앙상블 사전탐지를 vision_agent 내부에서 떼어냈다.
       결정론적 탐지와 LLM 판독의 실패 특성이 달라, 한 노드에 묶여 있으면 VLM 장애가
       YOLO 결과까지 삼켜버린다.
    2) MINT Fast-track 분기 제거: 우회 대상 노드들이 LLM을 전혀 쓰지 않아 절약 효과가
       0이었던 반면, 자동 매입 확정이 Critic 검증을 건너뛰는 위험만 있었다.
       이제 모든 건이 동일한 검증 경로를 통과한다.
    """
    builder = StateGraph(WMSInspectionState)

    # 1. 노드 등록 (add_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("detector_node", detector_node)
    builder.add_node("vision_agent", vision_agent)
    builder.add_node("policy_agent", policy_agent)
    builder.add_node("critic_agent", critic_agent)
    builder.add_node("human_node", human_node)
    builder.add_node("report_agent", report_agent)

    # 2. 파이프라인 진입 (START ➔ Detector ➔ Vision Agent)
    builder.add_edge(START, "detector_node")
    builder.add_edge("detector_node", "vision_agent")

    # 3. 순차 파이프라인 에지 (분기 없이 전 건이 동일한 검증 경로를 통과)
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
    
    # 5. [수정 이력] HITL로 이관된 건은 report_agent(자동 보증서 발급)로 보내지 않고 여기서
    # 그래프를 종료한다. 예전에는 human_node가 report_agent로 이어져서, 사람이 아무 결정도
    # 안 했는데도 자동으로 "HUMAN_RESOLVED" 처리 후 보증서까지 발급되어 버렸다 (해당 이슈는
    # 이전 세션에서 "HITL interrupt_before 미구현"으로 이미 식별된 부분). LangGraph의
    # interrupt_before는 워커(Celery)와 API가 서로 다른 프로세스라 영속 체크포인터 없이는
    # 못 쓰므로, 대신 human_node에서 그래프를 조기 종료하고 ReturnJob.status=HITL_REQUIRED로
    # 저장한 뒤(app/worker/tasks.py), 실제 재개는 관리자가 /admin/hitl/override를 통해
    # 별도 절차로 처리하도록 분리했다.
    builder.add_edge("human_node", END)
    
    # 6. End 엣지 (종료)
    builder.add_edge("report_agent", END)
    
    # 7. MemorySaver 연동 (HITL 중단점)
    memory = MemorySaver()
    return builder.compile(checkpointer=memory)

app_graph = build_supervisor_graph()
