import json
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from .state import WMSInspectionState

# ==========================================
# 0. Pydantic Output Schemas (구조화된 출력)
# ==========================================

class DefectDetail(BaseModel):
    type: str = Field(description="결함의 종류 (예: 표지 찢김, 얼룩, 변색 등)")
    ratio: int = Field(description="전체 면적 대비 결함의 상대적 비율 (%)")

class VisionResult(BaseModel):
    is_mint: bool = Field(description="결함이 전혀 없는 완전한 새 책(Mint)인지 여부")
    defects: List[DefectDetail] = Field(description="결함 리스트. Mint인 경우 빈 리스트 반환", default_factory=list)

class PolicyResult(BaseModel):
    ubci_score: int = Field(description="계산된 UBCI 점수 (0~100)")

class CriticResult(BaseModel):
    reason_code: Literal["OK", "REJECT"] = Field(description="검증 통과 여부. OK 또는 REJECT")
    repair_directive: Optional[str] = Field(description="REJECT일 경우, Policy Agent가 다시 계산하도록 지시하는 수정 가이드라인")

class ReportResult(BaseModel):
    result: str = Field(description="최종 상태 (예: AUTO_REFUND_APPROVED, INSPECTION_COMPLETED 등)")
    message: str = Field(description="고객에게 전달될 다정하고 친절한 CS 톤앤매너의 결과 안내문")

# ==========================================
# 0. LLM 인스턴스 생성 (비용 최적화)
# ==========================================
# Vision Agent용 (VLM, 고성능)
llm_vision = ChatOpenAI(model="gpt-4o", temperature=0.1)
# 텍스트 기반 일반 Agent용 (비용 절감 및 속도 최적화)
llm_mini = ChatOpenAI(model="gpt-4o-mini", temperature=0.1)

# ==========================================
# 1. Vision Agent
# ==========================================
def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Vision Agent: GPT-4o Vision 판독 중...")
    
    # 구조화된 출력 파서 부착 (Vision은 gpt-4o 사용)
    structured_llm = llm_vision.with_structured_output(VisionResult)
    
    prompt = """당신은 WMS 도서 물류센터의 매우 깐깐한 AI 비전 수석 검수원입니다.
입력된 도서의 상태를 분석하여 다음을 판독하세요:
1. 책 표지의 모서리 마모, 미세한 스크래치, 찍힘, 얼룩, 빛 반사에 가려진 굴곡은 물론이고, **오래된 책에서 나타나는 전반적인 낡음, 빛바램(Yellowing), 세월의 흔적**까지 샅샅이 찾아내세요.
2. 찾아낸 각 결함이 책 전체 면적의 몇 %를 차지하는지 상대적 비율(Ratio)을 추정하세요. (낡음이나 빛바램은 전체 면적 대비 매우 클 수 있습니다.)
3. [중요] 비닐 포장이 뜯어지지 않은 공장 출고 상태 급이 아니라면 절대 Mint가 될 수 없습니다. 
   미세한 중고 흔적이나 모서리 닳음이 하나라도 있다면 is_mint를 무조건 False로 설정하세요.
주의: 실제 이미지가 들어오면 픽셀 단위로 깐깐하게 검수하여 숨겨진 세월의 흔적까지 모두 잡아내세요.
"""
    
    # State에 있는 마지막 메시지(이미지 혹은 텍스트)를 프롬프트와 함께 전달
    messages = [SystemMessage(content=prompt)] + state["messages"]
    
    try:
        response: VisionResult = structured_llm.invoke(messages)
    except Exception:
        # Fallback for mock test
        response = VisionResult(is_mint=False, defects=[DefectDetail(type="가상 판독 오류", ratio=10)])
    
    return {
        "is_mint": response.is_mint,
        "defects": [d.model_dump() for d in response.defects],
        "messages": [AIMessage(content=f"[Vision Agent] 판독 완료 (Mint: {response.is_mint})")]
    }

# ==========================================
# 2. Policy Agent (RAG 시뮬레이션)
# ==========================================
def policy_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Policy Agent: UBCI 규정 매핑 및 점수 계산 중...")
    
    # 일반 텍스트 추론은 비용 절감을 위해 gpt-4o-mini 사용
    structured_llm = llm_mini.with_structured_output(PolicyResult)
    defects = state.get("defects", [])
    repair_directive = state.get("repair_directive")
    
    # RAG 검색 로직을 프롬프트로 임시 대체 (Mock)
    rag_rules = """
[UBCI 감점 규정]
- 표지 찢김: 비율 10% 이상 시 15점 감점
- 얼룩/오염: 비율 5% 이상 시 10점 감점
- 변색 / 빛바램 / 세월의 흔적(낡음): 자연스러운 헌 책 특성일 경우 10점~15점 감점 (너무 과하게 깎지 말 것)
- 기타 미세 결함(마모, 굴곡 등): 결함 하나당 2~3점 감점 (전체 합계가 70점 밑으로 내려갈 정도의 심각한 파손이 아니라면 적당히 감점할 것)
"""
    
    prompt = f"""당신은 WMS의 정책 검수관입니다. 기본 점수 100점에서 시작합니다.
다음은 Vision Agent가 찾아낸 결함 리스트입니다: {json.dumps(defects, ensure_ascii=False)}

위의 [UBCI 감점 규정]을 참고하여 100점에서 감점한 최종 UBCI 점수를 계산하세요.
결함이 하나라도 존재한다면 점수는 절대 100점이 될 수 없습니다. 반드시 감점하세요.
"""
    if repair_directive:
        prompt += f"\n\n[주의: Critic Agent의 반려 피드백이 있습니다! 수정 지시사항을 반드시 반영하여 감점하세요]:\n{repair_directive}"
    
    response: PolicyResult = structured_llm.invoke([SystemMessage(content=rag_rules), HumanMessage(content=prompt)])
    
    return {
        "ubci_score": response.ubci_score,
        "reason_code": None,        # Critic 검증을 위해 플래그 초기화
        "repair_directive": None,   # 반영을 완료했으므로 피드백 초기화
        "messages": [AIMessage(content=f"[Policy Agent] UBCI 점수 산정 완료 ({response.ubci_score}점)")]
    }

# ==========================================
# 3. Critic Agent
# ==========================================
def critic_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Critic Agent: 계산 타당성 교차 검증 중...")
    
    # 일반 텍스트 검증은 gpt-4o-mini 사용
    structured_llm = llm_mini.with_structured_output(CriticResult)
    ubci_score = state.get("ubci_score")
    defects = state.get("defects", [])
    revision_count = state.get("revision_count", 0)
    
    prompt = f"""당신은 AI 산정 결과를 감사하는 수석 검열관입니다.
Policy Agent가 다음 결함 리스트에 대해 최종 점수를 {ubci_score}점으로 계산했습니다.
결함: {json.dumps(defects, ensure_ascii=False)}

다음을 검증하세요:
1. 점수가 0에서 100 사이인지 확인.
2. 만약 결함(defects)이 하나라도 존재하는데 점수가 100점이라면 반드시 REJECT 하세요.
3. 점수가 0점 미만으로 비정상적이라면 REJECT 하세요.
통과한다면 reason_code에 "OK"를 반환하세요.
만약 REJECT한다면, reason_code에 "REJECT"를 적고 repair_directive에 "결함이 존재하므로 100점이 될 수 없습니다. 규정에 없는 결함이라도 최소 1~5점 감점하세요." 와 같이 구체적인 수정 지시를 반드시 작성하세요.
"""
    
    response: CriticResult = structured_llm.invoke([HumanMessage(content=prompt)])
    
    new_revision = revision_count + 1 if response.reason_code == "REJECT" else revision_count
    
    return {
        "reason_code": response.reason_code,
        "repair_directive": response.repair_directive,
        "revision_count": new_revision,
        "messages": [AIMessage(content=f"[Critic Agent] 검증 완료 ({response.reason_code})")]
    }

# ==========================================
# 4. Auto-Refund Agent (Fast-track)
# ==========================================
def auto_refund_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Auto Refund Agent: MINT 등급 자동 환불 처리 중...")
    
    # 단순 환불 폼 작성은 gpt-4o-mini 사용
    structured_llm = llm_mini.with_structured_output(ReportResult)
    
    prompt = "이 책은 결함이 전혀 없는 완전한 새 책(Mint)으로 판독되었습니다. 즉시 환불 승인하는 안내문을 작성하세요. 아주 정중하고 기분 좋은 톤이어야 합니다."
    response: ReportResult = structured_llm.invoke([HumanMessage(content=prompt)])
    
    return {
        "final_report": json.dumps(response.model_dump(), ensure_ascii=False),
        "messages": [AIMessage(content="[Auto Refund Agent] MINT 환불 보증서 발행 완료")]
    }

# ==========================================
# 5. Report Agent
# ==========================================
def report_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Report Agent: 최종 고객 리포트 생성 중...")
    
    # 감성 렌더링 및 텍스트 생성은 gpt-4o-mini 사용
    structured_llm = llm_mini.with_structured_output(ReportResult)
    ubci_score = state.get("ubci_score")
    defects = state.get("defects", [])
    
    prompt = f"""고객의 중고 도서 검수가 완료되었습니다.
- 최종 산정 점수: {ubci_score}점
- 발견된 결함: {json.dumps(defects, ensure_ascii=False)}

이 데이터를 바탕으로 고객에게 보낼 최종 통보문을 작성하세요. 
점수가 낮더라도 기분 상하지 않게 다정하고 세심한 톤을 유지하세요."""

    response: ReportResult = structured_llm.invoke([HumanMessage(content=prompt)])
    
    return {
        "final_report": json.dumps(response.model_dump(), ensure_ascii=False),
        "messages": [AIMessage(content="[Report Agent] 최종 고객 통보문 발행 완료")]
    }

# ==========================================
# 6. Human-In-The-Loop (HITL) 노드
# ==========================================
def human_node(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] HITL 노드 진입 - 관리자의 수동 개입(승인/수정) 대기 중")
    
    # 무한 루프 원천 차단: 강제 재개 시 카운터 및 에러 꼬리표 초기화
    return {
        "revision_count": 0,
        "reason_code": None,
        "messages": [AIMessage(content="[Human Node] 관리자 개입 완료. 파이프라인 재개.")]
    }
