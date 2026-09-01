import os

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .agents import (
    critic_agent,
    detector_node,
    human_node,
    policy_agent,
    report_agent,
    vision_agent,
)
from .state import WMSInspectionState

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
    예전에는 이 라우팅 함수가 reason_code를 직접 해석해 분기를 결정하고, supervisor_node는 print만 하는 껍데기였다.
    그 결과 "Supervisor가 총괄 지휘한다"는 설계 의도가 코드 어디에도 드러나지 않았고, 판단 근거도 상태에 남지 않아 추적이 불가능했다.
    이제 판단은 전적으로 supervisor_node가 수행하고, 이 함수는 그 결정을 집행만 한다.
    """
    decision = state.get("supervisor_decision")
    return _DECISION_TO_NODE.get(decision, "report_agent")


# route_from_vision()
# 이 함수는 is_mint=True면 Policy/Critic/Supervisor를 모두 건너뛰고 auto_refund_agent로 직행시켰다. 명분은 "비용 최적화 Fast-track"이었으나, 측정 결과 우회 대상 3개 노드(policy_agent / critic_agent / supervisor_node)는 **LLM을 단 한 번도 호출하지 않는 순수 결정론적 if/else 함수**였다. 즉 Fast-track이 절약하는 LLM 호출은 0건이었고, 보증서 생성(GPT-4o-mini 1회)은 auto_refund_agent와 report_agent 양쪽 모두에서 동일하게 발생했다. 절약 효과가 전혀 없는 대신, 금전적 확정(자동 매입/환불)이 환각 방어 담당 Critic의 검증을 건너뛴 채 Vision 단독 판정만으로 내려지는 심각한 구조적 위험만 남았다.
#
# 실제 사고: OpenAI 키 만료로 VLM이 401을 반환하자 defects가 빈 배열로 남았고, is_mint=True로 해석되어 모든 반품 도서가 검증 없이 UBCI 100점 MINT로 자동 매입 승인됐다.
#
# 이제 MINT도 동일하게 Policy -> Critic -> Supervisor 검증을 통과한다. 결함이 실제로 0건이면 Policy가 100점을 산출하고 Critic이 정합성을 확인하므로 판정 결과는 같으며, 경로가 하나로 합쳐져 검증 누락 구멍이 사라진다.
# "MINT 자동 매입"이라는 비즈니스 기능 자체는 state.auto_refund_eligible 플래그로 보존되어 워커가 집행한다.


def supervisor_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    Supervisor 중앙 지휘 노드.

    Vision(결함 판독) / Policy(UBCI 점수 산정) / Critic(교차 검증) 세 에이전트의 결과를 모두 수령하여 종합 판단한 뒤, 다음 행동을 최종 결정한다.
    각 하위 에이전트는 자기 영역의 사실만 보고하고, "그래서 이 건을 어떻게 처리할 것인가"라는 지휘 판단은 오직 이 노드만 내린다.
    """
    # --- 하위 3개 에이전트의 보고 수령 ---
    is_mint = state.get("is_mint")  # Vision Agent
    defects = state.get("defects") or []  # Vision Agent
    special_notes = state.get("special_notes")  # Vision Agent
    ubci_score = state.get("ubci_score")  # Policy Agent
    reason = state.get("reason_code")  # Critic Agent
    revision = state.get("revision_count", 0)  # Critic Agent

    # 판독 커버리지: 전달된 촬영 컷 중 Vision이 실제로 판독한 컷이 몇 장인가.
    image_count = len(state.get("image_paths") or [])
    invalid_indexes = state.get("invalid_image_indexes") or []
    valid_image_count = max(0, image_count - len(invalid_indexes))

    print(
        f"[Supervisor Manager] 3-Agent 보고 종합 수령 "
        f"(Vision: 결함 {len(defects)}건/MINT={is_mint}/유효컷 {valid_image_count}of{image_count} | "
        f"Policy: UBCI {ubci_score}점 | Critic: {reason}, 재검수 {revision}회)"
    )

    # --- 종합 판단 ---
    # 0) 판독 커버리지 게이트 - 최우선 검사.
    #    사고 사례(LPN-260804-A009): Vision이 촬영 4컷 전부를 "도서 미식별"로 제외하자 (invalid_image_indexes=[0,1,2,3]) 결함이 0건이 되었고, Policy가 그 0건을 근거로 UBCI 100점 MINT를 산출해 자동 승인 + 보증서까지 발급됐다.
    #    실물은 육안으로도 물젖음 주름이 보이는 도서였다.
    #    기존 방어망(vision_failed)은 VLM **호출 실패**만 막는다. 이 건은 호출이 성공했고 구조화 응답도 정상이었으므로 전부 통과했다.
    #    즉 "한 장도 못 읽었다"와 "다 읽었는데 흠이 없다"가 하위 노드에서는 똑같이 defects=[]로 표현되며, 그 둘을 구분할 수 있는 정보(image_paths ↔ invalid_image_indexes)는 오직 여기, 전 에이전트 보고를 종합하는 Supervisor에만 모인다.
    #    프리즈 규정 "판독 실패 처리 원칙"(검수하지 못했다 ≠ 검수했더니 흠이 없다)의 집행부다.
    #    LLM을 쓰지 않는 결정론적 규칙이므로 Supervisor의 기존 성격(규칙 기반 지휘 라우팅, 감사 추적 가능)도 그대로 유지된다.
    if image_count > 0 and valid_image_count == 0:
        decision = "ESCALATE_HUMAN"
        rationale = (
            f"판독 커버리지 미달 - 촬영 {image_count}장 전부가 도서 미식별 컷으로 제외되어 "
            f"(invalid={invalid_indexes}) 실제로 판독된 컷이 0장이다. 결함 {len(defects)}건 / "
            f"UBCI {ubci_score}점은 '무결점'이 아니라 '검수 불가'를 의미하므로 자동 확정을 "
            f"차단하고 관리자 수동 검수로 이관 결정."
        )
        print(f"[Supervisor Manager] 지휘 결정: {decision} - {rationale}")
        return {
            "supervisor_decision": decision,
            "supervisor_rationale": rationale,
            # 판독 실패는 점수를 남기지 않는다 (프리즈 규정: ubci_score는 None으로 둔다).
            # 관리자가 재촬영·재검수 후 확정하거나 HITL에서 직접 등급을 정한다.
            "ubci_score": None,
            # is_mint도 함께 내린다. 이걸 남겨두면 langgraph_wrapper가 final_grade를 "MINT"로 라벨링해 HITL 대시보드의 목표 등급 기본값이 MINT로 뜬다
            # 판독 못 한 건을 관리자에게 MINT로 추천하는 셈이 된다.
            "is_mint": False,
            "auto_refund_eligible": False,
            "reason_code": "NO_VALID_IMAGE_HITL",
            "executed_agents": ["supervisor"],
            "messages": [AIMessage(content=f"[Supervisor] {decision}: {rationale}")],
        }

    # 1) HITL 이관: Critic이 애매성을 보고했거나(경계선 58~66점 / 최대 재시도 초과),
    #    재검수 루프가 한계에 도달한 경우. 자동 판정을 강행하지 않고 사람에게 넘긴다.
    if (
        reason
        in ["MAX_RETRIES_AMBIGUOUS_HITL", "BOUNDARY_AMBIGUOUS_HITL", "HUMAN_REQUIRED"]
        or revision >= 2
    ):
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

    # 로그 문자열에는 em-dash 같은 비 CP949 문자를 쓰지 않는다 (Windows 콘솔에서 UnicodeEncodeError로 파이프라인 자체가 죽는다 - Docker는 UTF-8이라 무증상)
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
       결정론적 탐지와 LLM 판독의 실패 특성이 달라, 한 노드에 묶여 있으면 VLM 장애가 YOLO 결과까지 삼켜버린다.
    2) MINT Fast-track 분기 제거: 우회 대상 노드들이 LLM을 전혀 쓰지 않아 절약 효과가 0이었던 반면, 자동 매입 확정이 Critic 검증을 건너뛰는 위험만 있었다.
       이제 모든 건이 동일한 검증 경로를 통과한다.
    """
    builder = StateGraph(WMSInspectionState)

    # 1. 노드 등록 (add_node)
    # 계측 래퍼로 감싼다 — 구간 지연과 LLM 토큰을 노드 단위로 수집한다.
    # 노드 구현은 그대로 두고 등록 시점에만 감싸므로 파이프라인 구조·모델 배정은 불변이다.
    from app.ai.instrumentation import instrument

    builder.add_node("supervisor", instrument("supervisor", supervisor_node))
    builder.add_node("detector_node", instrument("detector_node", detector_node))
    builder.add_node("vision_agent", instrument("vision_agent", vision_agent))
    builder.add_node("policy_agent", instrument("policy_agent", policy_agent))
    builder.add_node("critic_agent", instrument("critic_agent", critic_agent))
    builder.add_node("human_node", instrument("human_node", human_node))
    builder.add_node("report_agent", instrument("report_agent", report_agent))

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
            "report_agent": "report_agent",
        },
    )

    # 5. HITL로 이관된 건은 report_agent(자동 보증서 발급)로 보내지 않고 여기서 그래프를 종료한다.
    # LangGraph의 interrupt_before는 워커(Celery)와 API가 서로 다른 프로세스라 영속 체크포인터 없이는 못 쓰므로, 대신 human_node에서 그래프를 조기 종료하고 ReturnJob.status=HITL_REQUIRED로 저장한 뒤(app/worker/tasks.py), 실제 재개는 관리자가 /admin/hitl/override를 통해 별도 절차로 처리하도록 분리했다.
    builder.add_edge("human_node", END)

    # 6. End 엣지 (종료)
    builder.add_edge("report_agent", END)

    # 7. MemorySaver 연동 (HITL 중단점)
    memory = MemorySaver()
    return builder.compile(checkpointer=memory)


app_graph = build_supervisor_graph()
