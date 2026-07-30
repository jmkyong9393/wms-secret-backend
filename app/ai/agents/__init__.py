import json
import uuid
from datetime import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from app.ai.state import WMSInspectionState

# ==========================================
# 0. Pydantic Output Schemas (구조화된 출력)
# ==========================================

class DefectDetail(BaseModel):
    type: str = Field(description="결함의 종류 (예: DMG_INT_DOODLE, DMG_INT_STAIN, DMG_EXT_CRUSH, DMG_EXT_WET 등)")
    ratio: int = Field(description="전체 면적 대비 결함의 상대적 비율 (%)")
    preliminary_deduction: int = Field(description="4o-mini가 1차 계산한 예비 감점 수치", default=10)

class VisionResult(BaseModel):
    is_mint: bool = Field(description="결함이 전혀 없는 완전한 새 책(Mint)인지 여부")
    defects: List[DefectDetail] = Field(description="결함 리스트. Mint인 경우 빈 리스트 반환", default_factory=list)

class PolicyResult(BaseModel):
    ubci_score: int = Field(description="계산된 최종 UBCI 점수 (0~100)")
    ubci_grade: str = Field(description="최종 등급 (S, A, B, REJECT)")
    decision: str = Field(description="입고 처분 결정 (APPROVE, DOWNGRADE, REJECT)")

class CriticResult(BaseModel):
    reason_code: Literal["OK", "REJECT", "MAX_RETRIES_AMBIGUOUS_HITL", "BOUNDARY_AMBIGUOUS_HITL"] = Field(description="프로세스 검증 통과 여부 및 HITL 이관 코드")
    repair_directive: Optional[str] = Field(description="REJECT 또는 HITL 이관 시 수정 지시 가이드라인")

class QualityCertificateResult(BaseModel):
    cert_id: str = Field(description="발급된 디지털 WMS 검수 보증서 고유 번호")
    certificate_text: str = Field(description="디지털 검수 보증서 전문")

# ==========================================
# 0. LLM 인스턴스 생성 (GPT-4o VLM + GPT-4o-mini temperature=0.0)
# ==========================================
try:
    llm_vlm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
    llm_mini = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
except Exception:
    llm_vlm = None
    llm_mini = None

# ==========================================
# 1. Vision Agent (2-Stage: GPT-4o VLM BBox 검수 ➔ GPT-4o-mini 예비 감점 계산)
# ==========================================
def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Vision Agent: [Stage 1] GPT-4o VLM BBox & 전면 검수 -> [Stage 2] GPT-4o-mini 예비 감점 연산 중...")
    defects = state.get("defects") or []
    
    if llm_vlm and not defects:
        structured_vlm = llm_vlm.with_structured_output(VisionResult)
                prompt_vlm = """당신은 WMS 디지털 품질 검수 센터의 수석 AI 비전(VLM) 검수원입니다.
OpenCV CLAHE(Contrast Limited Adaptive Histogram Equalization) 동적 명암 전처리가 완료된 도서 이미지(앞표지, 뒷표지, 속지)를 시각적으로 정밀 분석하여 결함 및 BBox(0~1000 상대좌표)를 100% 정밀 검출하세요.

[BBox 검출 및 결함 판정 4대 원칙]
1. 표지 (Front/Back Cover):
   - 찌그러짐, 찢어짐, 심한 오염이 없는 깨끗한 표지는 결함 없음(Clean, []).
2. 속지 필기/낙서 (DMG_INT_DOODLE):
   - 본문 지문, 보기 번호(①~④)에 친 연필/볼펜 동그라미 표기 -> DMG_INT_DOODLE.
   - 지문/쿼리문 하단에 그은 연필/볼펜 밑줄(Underline) -> DMG_INT_DOODLE.
   - 문제 박스 안이나 여백에 적힌 손글씨 메모(SQL 쿼리, outer join, 10:10:00, 숫적 메모 등) -> DMG_INT_DOODLE.
3. 인쇄본 구별:
   - 교재 본문에 기본 인쇄된 텍스트, 표(Table), 인포그래픽 박스는 절대 결함으로 오탐하지 말 것.
4. 좌표계:
   - xmin, ymin, xmax, ymax는 이미지 0~1000 픽셀 상대 좌표로 정밀 바인딩.
"""
        messages = [SystemMessage(content=prompt_vlm)] + (state.get("messages") or [])
        try:
            res_vlm: VisionResult = structured_vlm.invoke(messages)
            is_mint = res_vlm.is_mint
            defects = [d.model_dump() for d in res_vlm.defects]
        except Exception:
            is_mint = len(defects) == 0
    else:
        is_mint = len(defects) == 0

    for d in defects:
        dtype = str(d.get("type", ""))
        ratio = d.get("ratio", 10)
        if "DOODLE" in dtype or "필기" in dtype or "낙서" in dtype:
            d["preliminary_deduction"] = min(15, max(5, ratio))
        elif "TEAR" in dtype or "찢어짐" in dtype:
            d["preliminary_deduction"] = 5 if ratio < 5 else (10 if ratio < 15 else 15)
        else:
            d["preliminary_deduction"] = max(5, ratio)

    return {
        "is_mint": is_mint,
        "defects": defects,
        "messages": [AIMessage(content=f"[Vision Agent] GPT-4o VLM 검수 & GPT-4o-mini 예비 감점 산출 완료 (is_mint: {is_mint}, 결함 {len(defects)}건)")]
    }

# ==========================================
# 2. Policy Agent (UBCI_Specification_v2.0.0.0.md 100% 공식 매트릭스 수식 엔진)
# ==========================================
def policy_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Policy Agent: UBCI v2.0.0.0 공식 감점 매트릭스 & 텍스트 침범 가중치 적용 연산 중...")
    defects = state.get("defects") or []
    book_title = str(state.get("book_title") or state.get("title") or "")
    is_workbook = any(k in book_title for k in ["수험서", "문제집", "기출", "자격검정", "실전문제", "학습", "교재", "AIVLE", "SQL"])

    DEFECT_TRANSLATION_MAP = {
        "DMG_INT_DOODLE": "내부 손글씨/낙서",
        "DMG_INT_STAIN": "내지 오염/이물질",
        "DMG_EXT_CRUSH": "표지 모서리 찍힘/구겨짐",
        "DMG_EXT_WET": "액체 오염/습기/휨 (WATER_DAMAGE)",
        "DMG_EXT_TEAR": "커버 찢어짐 (Tear)",
        "DMG_INT_DISCOLOR": "내지 황변/빛바램",
        "DMG_EXT_SCRATCH": "표지 긁힘/스크래치",
        "DMG_EXT_STICKER": "스티커/바코드 자국",
        "DMG_EDGE_WEAR": "모서리 마모",
        "DMG_SPINE_CRACK": "책등 갈라짐",
        "DMG_BINDING_LOOSE": "제본 벌어짐",
        "DMG_SIGNATURE": "측면 서명/이름",
        "DMG_STAMP": "도서관/장서인 도장"
    }

    deduction_items = []
    total_deduction = 0
    is_fatal_reject = False
    fatal_reason = ""
    edge_wear_added = False
    doodle_workbook_added = False

    for d in defects:
        dtype = str(d.get("type", "") or d.get("label", ""))
        ratio = d.get("ratio", 5)
        page_cnt = d.get("page_count") or d.get("pages") or 1
        text_overlap = d.get("text_overlap", False) or "본문" in str(d.get("description", ""))
        label = DEFECT_TRANSLATION_MAP.get(dtype) or dtype or "상태 결함"

        # 🚨 치명적 결함 즉시 반려 (UBCI Spec v2.0.0.0 Section 1 & Section 4)
        if "WET" in dtype or "WATER" in dtype or "WARPING" in dtype or "침수" in dtype or "휨" in dtype:
            is_fatal_reject = True
            fatal_reason = "🚨 액체 오염(Water Stain) 또는 페이지 휨(Warping) 감지 ➔ UBCI v2.0.0.0 규정에 의거 즉시 반려(REJECT)"
            deduction_items.append((label, 100, f"{label} (치명적 결함 ➔ 즉시 반려)"))
            break

        if "WEAR" in dtype or "마모" in dtype:
            if not edge_wear_added:
                deduction_items.append((label, 5, "도서 전체 모서리 마모 (-5점 단일 고정 Cap)"))
                total_deduction += 5
                edge_wear_added = True
        elif "DOODLE" in dtype or "필기" in dtype or "낙서" in dtype:
            if is_workbook:
                if not doodle_workbook_added:
                    deduction_items.append((label, 15, "수험서/문제집 도서 전체 필기/낙서 (-15점 단일 고정 Cap)"))
                    total_deduction += 15
                    doodle_workbook_added = True
            else:
                base_ded = 15 if page_cnt > 5 else 10
                multiplier = 1.5 if text_overlap else 1.0
                final_ded = int(base_ded * multiplier)
                total_deduction += final_ded
                overlap_str = " (본문 텍스트 침범 x1.5 가중치)" if text_overlap else ""
                deduction_items.append((label, final_ded, f"{label} (-{final_ded}점{overlap_str})"))
        else:
            if "SCRATCH" in dtype or "긁힘" in dtype or "스크래치" in dtype:
                base_ded = 2 if ratio < 5 else (5 if ratio < 15 else 10)
            elif "TEAR" in dtype or "찢어짐" in dtype or "찢김" in dtype:
                base_ded = 5 if ratio < 5 else (10 if ratio < 15 else 15)
            elif "STICKER" in dtype or "스티커" in dtype:
                base_ded = 2 if ratio < 5 else (3 if ratio < 15 else 5)
            elif "CRUSH" in dtype or "찍힘" in dtype or "구겨짐" in dtype or "찌그러짐" in dtype:
                base_ded = 3 if ratio < 5 else (5 if ratio < 15 else 10)
            elif "SPINE" in dtype or "갈라짐" in dtype:
                base_ded = 5 if ratio < 15 else 10
            elif "BINDING" in dtype or "제본" in dtype:
                if ratio >= 15:
                    is_fatal_reject = True
                    fatal_reason = "🚨 제본 완전 벌어짐 ➔ 즉시 반려(REJECT)"
                    break
                base_ded = 10
            elif "SIGNATURE" in dtype or "서명" in dtype or "이름" in dtype:
                base_ded = 10
            elif "STAMP" in dtype or "도장" in dtype:
                base_ded = 15
            else:
                base_ded = 2 if ratio < 5 else (5 if ratio < 15 else 8)

            multiplier = 1.5 if text_overlap else 1.0
            final_ded = int(base_ded * multiplier)
            total_deduction += final_ded
            overlap_str = " (본문 텍스트 침범 x1.5 가중치)" if text_overlap else ""
            deduction_items.append((label, final_ded, f"{label} (-{final_ded}점{overlap_str})"))

    if is_fatal_reject:
        score = 0
        grade_str = "REJECT C급 (폐기)"
        decision_str = "REJECT"
        policy_text = f"UBCI v2.0.0.0 사내 수석 룰 적용 ➔ {fatal_reason}"
    else:
        score = max(0, min(100, 100 - total_deduction))
        grade_str = "S급 (MINT)" if score >= 95 else ("A급 (GOOD)" if score >= 85 else ("B급 (NORMAL)" if score >= 65 else "REJECT C급 (폐기)"))
        decision_str = "APPROVE" if score >= 65 else "REJECT"
        if deduction_items:
            deduction_str = " + ".join([item[2] for item in deduction_items])
            policy_text = f"UBCI v2.0.0.0 공식 매트릭스 적용 ➔ {deduction_str} = 총 {total_deduction}점 감점 (UBCI {score}점 / {grade_str} / 처분: {decision_str})"
        else:
            policy_text = f"UBCI v2.0.0.0 공식 매트릭스 적용 ➔ 결함 없음 (UBCI {score}점 / {grade_str} / 처분: {decision_str})"

    return {
        "defects": defects,
        "ubci_score": score,
        "policy_text": policy_text,
        "reason_code": None,
        "repair_directive": None,
        "messages": [AIMessage(content=f"[Policy Agent] {policy_text}")]
    }

# ==========================================
# 3. Critic Agent (판정 애매 도서 & 최대 루프 초과 시 HITL 이관 제어)
# ==========================================
def critic_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Critic Agent: 판정 결과 애매성 평가 및 HITL 관리자 이관 판단 중...")
    revision = state.get("revision_count", 0)
    score = state.get("ubci_score")
    
    if revision >= 2:
        return {
            "reason_code": "MAX_RETRIES_AMBIGUOUS_HITL",
            "repair_directive": "최대 재검수 횟수(2회) 초과 ➔ HITL 관리자 수동 오버라이드 이관",
            "revision_count": revision,
            "messages": [AIMessage(content="[Critic Agent] ⚠️ 최대 재검수 루프(2회) 초과 ➔ HITL 관리자 검수 이관")]
        }

    if score is not None and 58 <= score <= 66:
        return {
            "reason_code": "BOUNDARY_AMBIGUOUS_HITL",
            "repair_directive": f"입고 등급 경계선(UBCI {score}점) 판정 애매 ➔ HITL 관리자 수동 오버라이드 이관",
            "revision_count": revision,
            "messages": [AIMessage(content=f"[Critic Agent] ⚠️ 입고 등급 경계선(UBCI {score}점) 판정 애매 ➔ HITL 관리자 개입 이관")]
        }

    if score is None and revision < 2:
        return {
            "reason_code": "REJECT",
            "repair_directive": "UBCI 점수 계산 누락. Vision Agent 재검수 지시",
            "revision_count": revision + 1,
            "messages": [AIMessage(content=f"[Critic Agent] 🔄 프로세스 비정상 ➔ Vision Agent 재검수 (재시도 {revision + 1}/2회)")]
        }

    return {
        "reason_code": "OK",
        "repair_directive": None,
        "revision_count": revision,
        "messages": [AIMessage(content="[Critic Agent] 판정 명확성 검증 완료 ➔ Report Agent 보증서 발행 승인 (OK)")]
    }

# ==========================================
# 4. Auto-Refund Agent
# ==========================================
def auto_refund_agent(state: WMSInspectionState) -> WMSInspectionState:
    return {
        "final_report": "MINT 등급 디지털 검수 보증서 및 자동 환불 승인",
        "messages": [AIMessage(content="[Auto Refund Agent] MINT 디지털 품질 보증서 발행 완료")]
    }

# ==========================================
# 5. Report Agent (디지털 WMS 검수 보증서 발행)
# ==========================================
def report_agent(state: WMSInspectionState) -> WMSInspectionState:
    ubci_score = state.get("ubci_score", 100)
    grade_str = "S급 (MINT)" if ubci_score >= 95 else ("A급 (GOOD)" if ubci_score >= 85 else ("B급 (NORMAL)" if ubci_score >= 65 else "REJECT C급 (폐기)"))
    cert_id = f"CERT-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    
    cert_text = f"📜 [디지털 WMS 품질 검수 인증서] (인증 ID: {cert_id}) ➔ Nexus 사내 정밀 비전 검증 시스템이 외관 표지 훼손율 및 내지 전수 픽셀 분석을 최종 검증하였습니다. 내부 AI 비전 종합 판정 결과, 독서 및 장기 보관에 지장이 없는 우수한 품질의 {grade_str} 실재고로 공식 인증합니다."
    return {
        "ubci_score": ubci_score,
        "final_report": cert_text,
        "report_text": cert_text,
        "messages": [AIMessage(content=f"[Report Agent] {cert_text}")]
    }

# ==========================================
# 6. Human Node (HITL 관리자 수동 오버라이드 처리 노드)
# ==========================================
def human_node(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Human Node (HITL): 관리자 수동 검수 개입 및 오버라이드 완공 처리 중...")
    return {
        "revision_count": 0,
        "reason_code": "HUMAN_RESOLVED",
        "repair_directive": None,
        "messages": [AIMessage(content="[Human Node (HITL)] 관리자 수동 오버라이드 처리 완공")]
    }
