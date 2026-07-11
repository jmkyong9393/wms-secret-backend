from langchain_core.messages import AIMessage
from .state import WMSInspectionState

def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    1. Vision Agent
    TODO: GPT-4o Vision API를 호출하여 이미지에서 BBox 추출 및 결함(Mint) 여부를 판단하세요.
    - 핵심: 사진 촬영 거리/구도에 영향을 받지 않도록, 전체 책 면적 대비 결함의 '상대 비율(Relative Ratio)'을 추출해야 합니다.
    - 입력: state["messages"] 내의 이미지 URL
    """
    print("[Agent] Vision Agent 실행 중... (GPT-4o Vision 호출 모사)")
    
    # --- 여기서 실제 LLM 체인을 호출하세요 ---
    
    # 뼈대 코드 (LLM의 응답을 파싱했다고 가정하는 더미 데이터)
    dummy_is_mint = False
    dummy_defects = [{"type": "표지 찢김", "ratio": 15}]
    
    return {"is_mint": dummy_is_mint, "defects": dummy_defects}

def policy_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    2. Policy Agent (RAG 적용)
    TODO: Vision이 넘겨준 상대 비율(예: 가로 15% 찢김)을 바탕으로 Vector DB(RAG)를 검색하여 UBCI 규정을 찾아오세요.
    - 핵심: RAG로 검색된 규정을 기반으로 감점 점수를 계산합니다. Critic Agent의 수정 지시(repair_directive)가 있다면 반영해야 합니다.
    """
    print("[Agent] Policy Agent 실행 중... (ChromaDB RAG 검색 모사)")
    
    # --- 여기서 실제 RAG 기반 LLM 체인을 호출하세요 ---
    
    dummy_ubci_score = 80  # 20점 감점
    return {"ubci_score": dummy_ubci_score}

def critic_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    3. Critic Agent
    TODO: Policy Agent가 연산한 점수가 타당한지 교차 검증하세요. 실패 시 "REJECT" 코드를 반환합니다.
    """
    print("[Agent] Critic Agent 실행 중... (교차 검증 모사)")
    
    # --- 여기서 평가(Eval) LLM 프롬프트를 호출하세요 ---
    
    current_revision = state.get("revision_count", 0)
    
    # 뼈대 코드: 1번은 일부러 반려해보고, 2번째에 OK를 내리는 모사
    if current_revision == 0:
        return {
            "reason_code": "UBCI_POLICY_VIOLATION",
            "repair_directive": "찢김 15%는 C등급이므로 30점을 감점해야 합니다. 재계산하세요.",
            "revision_count": current_revision + 1
        }
    else:
        return {
            "reason_code": "OK",
            "repair_directive": None,
            "revision_count": current_revision + 1
        }

def auto_refund_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    4. Auto-Refund Agent (Fast-track)
    TODO: MINT 등급의 새 책에 대한 환불 승인 사유서(JSON)를 작성하세요.
    """
    print("[Agent] Auto Refund Agent 실행 중... (MINT 등급 고속 승인 모사)")
    
    dummy_report = '{"status": "APPROVED", "reason": "최상급(MINT) 상태 확인 완료"}'
    return {"final_report": dummy_report}

def report_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    5. Report Agent (감성/페르소나 렌더링)
    TODO: 검증이 완료된 사유를 바탕으로, 상황에 맞는 CS 페르소나(Tone & Manner)를 입혀 고객용 보증서를 생성하세요.
    """
    print("[Agent] Report Agent 실행 중... (보증서 작성 모사)")
    
    dummy_report = '{"status": "REJECTED_OR_PARTIAL", "reason": "고객님, 아쉽게도 표지 찢김이 발견되어 일부 감점 처리 되었습니다..."}'
    return {"final_report": dummy_report}

def human_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    6. Human-In-The-Loop (HITL) 노드
    TODO: Critic이 반복해서 반려할 경우 관리자의 수동 개입을 대기합니다. (내용 비워둠)
    """
    print("[Agent] HITL 노드 진입 - 관리자의 수동 개입(승인/수정) 대기 중")
    return state
