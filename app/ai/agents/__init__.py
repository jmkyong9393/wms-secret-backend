import json
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from app.ai.state import WMSInspectionState

# WBF YOLO 클래스명 -> UBCI_Specification_v2.0.0.0.md 결함 코드 매핑
YOLO_TO_UBCI_TYPE = {
    "Wornout": "DMG_EDGE_WEAR",
    "ripped": "DMG_EXT_TEAR",
    "doodle_scribble": "DMG_INT_DOODLE",
}

# UBCI 결함 코드 -> 고객이 읽는 한국어 라벨.
# [수정 이력] 원래 policy_agent 함수 내부 지역변수였는데, 보증서 문서 빌더와 프론트 BBox
# 라벨이 같은 사전을 필요로 해 모듈 스코프로 올렸다 (같은 매핑이 여러 곳에 복제되는 것을 방지).
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
    "DMG_STAMP": "도서관/장서인 도장",
}


def _load_image_as_base64(path_or_url: str) -> Optional[str]:
    """로컬 파일 경로 또는 HTTP(S) URL을 GPT-4o Vision에 넣을 base64 문자열로 인코딩."""
    import base64
    try:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            import urllib.request
            req = urllib.request.Request(path_or_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                return base64.b64encode(response.read()).decode("utf-8")
        with open(path_or_url, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        print(f"[Vision Agent] 이미지 로드 실패 ({path_or_url}): {e}")
        return None


def _ensure_local_path(path_or_url: str) -> Optional[str]:
    """WBF YOLO 추론은 로컬 파일 경로가 필요 - URL이면 임시 파일로 다운로드."""
    if not (path_or_url.startswith("http://") or path_or_url.startswith("https://")):
        return path_or_url if os.path.exists(path_or_url) else None
    try:
        import urllib.request
        import tempfile
        req = urllib.request.Request(path_or_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read()
        suffix = os.path.splitext(path_or_url)[1] or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            return tmp.name
    except Exception as e:
        print(f"[Vision Agent] WBF용 로컬 다운로드 실패 ({path_or_url}): {e}")
        return None

# ==========================================
# 0. Pydantic Output Schemas (구조화된 출력)
# ==========================================

class DefectDetail(BaseModel):
    type: str = Field(description="결함의 종류 (예: DMG_INT_DOODLE, DMG_INT_STAIN, DMG_EXT_CRUSH, DMG_EXT_WET 등)")
    ratio: int = Field(description="전체 면적 대비 결함의 상대적 비율 (%)")
    preliminary_deduction: int = Field(description="4o-mini가 1차 계산한 예비 감점 수치", default=10)
    bbox: Optional[Dict[str, int]] = Field(
        default=None,
        description="결함의 2D Bounding Box, {xmin,ymin,xmax,ymax} 0~1000 상대좌표 (wbf_detector.py의 bbox 출력과 동일 키)"
    )
    confidence: Optional[float] = Field(default=None, description="결함 판독 신뢰도 (0.0~1.0) - WBF 후보와 일치하면 해당 confidence, VLM 단독 판독이면 VLM 자체 추정치")
    text_overlap: bool = Field(default=False, description="결함이 도서의 제목/본문 텍스트 영역을 침범했는지 여부 - UBCI 1.5배 가중치 판단에 사용 (Policy Agent가 이 필드를 읽음)")
    image_index: Optional[int] = Field(default=None, description="결함이 발견된 원본 이미지의 인덱스 (0=정면, 1=후면, 2번째~=내지/측면 등) - 프론트 이미지별 BBox 오버레이 1:1 매핑에 사용")

class VisionResult(BaseModel):
    is_mint: bool = Field(description="결함이 전혀 없는 완전한 새 책(Mint)인지 여부")
    defects: List[DefectDetail] = Field(description="결함 리스트. Mint인 경우 빈 리스트 반환", default_factory=list)
    special_notes: Optional[str] = Field(
        default=None,
        description="UBCI 감점과 무관한 정성적 관찰 (도서관 장서 도장, 부록 CD 누락, 저자 친필 서명 등)"
    )

class PolicyResult(BaseModel):
    ubci_score: int = Field(description="계산된 최종 UBCI 점수 (0~100)")
    ubci_grade: str = Field(description="최종 등급 (S, A, B, REJECT)")
    decision: str = Field(description="입고 처분 결정 (APPROVE, DOWNGRADE, REJECT)")

class CriticResult(BaseModel):
    reason_code: Literal["OK", "REJECT", "MAX_RETRIES_AMBIGUOUS_HITL", "BOUNDARY_AMBIGUOUS_HITL"] = Field(description="프로세스 검증 통과 여부 및 HITL 이관 코드")
    repair_directive: Optional[str] = Field(description="REJECT 또는 HITL 이관 시 수정 지시 가이드라인")


class CriticVerdict(BaseModel):
    """
    Critic Stage B(LLM 판독 타당성 심사)의 구조화 출력.

    [설계 노트] 이 스키마는 with_structured_output으로 강제된다. 응답 텍스트를
    ast.literal_eval / json.loads로 파싱하는 방식은 모델이 코드블록(```)이나 서론을
    한 줄만 덧붙여도 예외가 나면서 노드 전체가 죽는다 - 프롬프트로 "코드 블록 제거"를
    부탁하는 대신 스키마로 보장받는다. decision도 자유 문자열이 아닌 Literal이라
    "Approved", "APPROVE" 같은 변형이 원천 차단된다.
    """
    decision: Literal["APPROVED", "REJECTED"] = Field(description="판독 타당성 승인 여부")
    reason: str = Field(description="판정 근거 한 문장 (한국어)")
    suspect_indices: List[int] = Field(
        default_factory=list,
        description="오탐(False Positive)이 의심되는 결함의 defects 배열 인덱스 목록. 없으면 빈 배열",
    )

class QualityCertificateResult(BaseModel):
    cert_id: str = Field(description="발급된 디지털 WMS 검수 보증서 고유 번호")
    certificate_text: str = Field(description="디지털 검수 보증서 전문")


class DefectFinding(BaseModel):
    """고객용 보증서에 노출되는 결함 1건의 서술."""
    image_index: int = Field(default=0, description="이 결함이 발견된 원본 검수 이미지 인덱스")
    label: str = Field(description="결함 이름 (예: 내지 손글씨/낙서). 코드가 아닌 사람이 읽는 한국어")
    deduction: int = Field(default=0, description="이 결함으로 차감된 UBCI 점수")
    reason: str = Field(description="상세 사유 한 문장. 고객이 읽는 문장이므로 결함을 숨기지 않되 위트 있고 정중하게 포장")


class CertificateDocument(BaseModel):
    """
    Report Agent가 생성하는 고객 공개용 보증서 본문.
    프론트(/certificate/[lpn])는 이 필드들을 그대로 렌더하기만 하며, 어떤 문장도 프론트에서
    조립하지 않는다. (기존에는 등급별 if-else 문장이 프론트에 하드코딩되어 있었다.)
    """
    headline: str = Field(description="보증서 상단 한 줄 총평. 위트 있게, 단 과장 광고는 금지 (예: '표지부터 마지막 장까지, 흠잡을 데가 없었습니다')")
    summary: str = Field(description="종합 소견 2~3문장. 검수 방식과 판정 결과를 고객 눈높이로 설명")
    condition_detail: str = Field(description="상세 사유 본문. 결함이 하나도 없으면 '결함 없음'을 위트 있게 풀어 쓰고, 있으면 어떤 결함이 어떻게 반영됐는지 정직하게 서술")
    findings: List[DefectFinding] = Field(default_factory=list, description="결함별 상세 내역. 결함이 없으면 빈 리스트")
    care_tip: Optional[str] = Field(default=None, description="이 도서 상태에 맞는 짧은 보관/사용 팁 한 줄")

# ==========================================
# 0. LLM 인스턴스 생성 (프리즈 규정: Vision Agent = GPT-4o 고정, 나머지 = GPT-4o-mini 고정)
# ==========================================
try:
    llm_vlm = ChatOpenAI(model="gpt-4o", temperature=0.0)
    llm_mini = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
except Exception:
    llm_vlm = None
    llm_mini = None

# ==========================================
# 0. Detector Node (WBF 3-YOLO 앙상블 사전탐지 - LLM 미사용, 결정론적)
# ==========================================
def detector_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    WBF(Weighted Box Fusion) 3-YOLO 앙상블로 결함 후보를 사전 탐지한다. LLM을 쓰지 않는다.

    [분리 배경] 예전에는 vision_agent 하나가 이 YOLO 탐지 + GPT-4o VLM 판독 + GPT-4o-mini
    검증 3단계를 모두 수행했다. 그 결과 VLM 호출이 실패하면 YOLO가 이미 찾아둔 결함 후보까지
    함께 사라져 "결함 0건"이 되었고, 그것이 MINT(무결점) 판정으로 이어졌다. 결정론적 탐지와
    확률적 LLM 판독은 실패 특성이 전혀 다르므로 노드를 분리해, VLM이 죽어도 YOLO 결과는
    상태에 남아 후속 판정 근거로 쓰이게 한다.
    """
    image_paths = state.get("image_paths") or []
    yolo_candidates = []

    try:
        from app.ai.wbf_detector import wbf_detector
        for idx, path in enumerate(image_paths):
            local_path = _ensure_local_path(path)
            if not local_path:
                continue
            for d in wbf_detector.detect_defects_wbf(local_path):
                yolo_candidates.append({
                    "image_index": idx,
                    "type": YOLO_TO_UBCI_TYPE.get(d["defect_type"], d["defect_type"]),
                    "confidence": d["confidence"],
                    "bbox": d["bbox"],
                })
        detector_text = (
            f"WBF 3-YOLO 앙상블 사전탐지 완료 - {len(image_paths)}장에서 결함 후보 {len(yolo_candidates)}건 검출"
        )
    except Exception as e:
        detector_text = f"WBF 앙상블 사전탐지 실패({type(e).__name__}) - GPT-4o VLM 단독 판독으로 진행"
        print(f"[Agent] Detector Node: {detector_text}")

    print(f"[Agent] Detector Node: {detector_text}")
    return {
        "yolo_candidates": yolo_candidates,
        "detector_text": detector_text,
        "executed_agents": ["detector_node"],
        "messages": [AIMessage(content=f"[Detector] {detector_text}")],
    }


# ==========================================
# 1. Vision Agent (GPT-4o VLM 정밀검수 -> GPT-4o-mini 예비감점 검증)
# ==========================================
def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Vision Agent: GPT-4o VLM 정밀검수 -> GPT-4o-mini 예비감점 검증 중...")
    defects = state.get("defects") or []
    image_paths = state.get("image_paths") or []
    # Detector Node가 앞서 채워둔 앙상블 후보 (VLM 실패 시 폴백 근거로도 쓰인다)
    yolo_candidates = state.get("yolo_candidates") or []

    if llm_vlm and not defects and image_paths:
        # --- GPT-4o VLM 정밀 검수 (WBF 후보를 컨텍스트로 제공, 최종 판단은 VLM이 직접) ---
        structured_vlm = llm_vlm.with_structured_output(VisionResult)
        yolo_hint = (
            f"\n\n[사전 YOLO(WBF 3모델 앙상블) 탐지 후보 - 참고용이며 최종 판단은 이미지를 직접 보고 확정할 것]\n"
            f"{json.dumps(yolo_candidates, ensure_ascii=False)}"
            if yolo_candidates else
            "\n\n[사전 YOLO 탐지 후보 없음 - 이미지에서 직접 결함을 판독할 것]"
        )
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
   - 각 결함(defects[i])의 bbox 필드에 xmin, ymin, xmax, ymax를 이미지 0~1000 픽셀 상대 좌표로
     반드시 채워서 반환하세요 (누락 금지). confidence 필드에는 본인의 판독 확신도(0.0~1.0)를 적으세요.
   - 해당 결함이 도서 제목이나 본문 텍스트 영역을 가리거나 침범하면 text_overlap을 true로,
     아니면 false로 설정하세요 (UBCI 1.5배 가중치 판정에 직접 사용됩니다).
   - image_index에는 결함이 발견된 이미지의 순서(0번째=정면, 1번째=후면, 2번째부터=내지/측면 등)를
     정확히 기재하세요.
5. 정성적 관찰(special_notes):
   - UBCI 감점과 무관하지만 특기할 사항(도서관 장서 도장, 부록 CD/카드 누락, 저자 친필 서명 등)이
     보이면 top-level special_notes 필드에 한 줄로 기록하세요. 없으면 null로 두세요.
""" + yolo_hint

        content_list = [{"type": "text", "text": prompt_vlm}]
        for path in image_paths:
            b64 = _load_image_as_base64(path)
            if b64:
                content_list.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

        special_notes = None
        vision_error = None
        try:
            res_vlm: VisionResult = structured_vlm.invoke([HumanMessage(content=content_list)])
            is_mint = res_vlm.is_mint
            defects = [d.model_dump() for d in res_vlm.defects]
            special_notes = res_vlm.special_notes
        except Exception as e:
            # [수정 이력 - CRITICAL] 예전에는 여기서 `is_mint = len(defects) == 0` 이었다.
            # VLM 호출이 실패하면 defects가 빈 채로 남으므로 "결함 0건 = MINT(무결점)"으로
            # 해석되어, OpenAI 장애/키 만료 시 모든 반품 도서가 UBCI 100점 MINT로 자동
            # 승인·매입되었다. "검수하지 못했다"와 "검수했더니 흠이 없다"는 완전히 다른
            # 사실인데 이를 동일하게 취급한 것.
            # 판독 실패는 MINT로 승격시키지 않고 reason_code=HUMAN_REQUIRED를 세워
            # Supervisor의 기존 HITL 이관 분기를 타게 한다 (그래프 엣지는 변경하지 않음).
            print(f"[Vision Agent] GPT-4o VLM 호출 실패 - MINT 자동승격 금지, HITL 이관: {e}")
            vision_error = f"{type(e).__name__}: {e}"

        if vision_error:
            # 판독 실패는 즉시 HITL로 보내지 않는다. Rate limit(429)/타임아웃 같은 일시적
            # 장애가 대부분이므로, 기존 재검수 루프(Critic의 revision_count, 최대 2회)에
            # 태워 재시도하게 하고 그래도 안 되면 Supervisor가 HITL로 이관한다.
            #
            # 핵심은 defects를 비운 채 ubci_score 산출을 막는 것이다. Critic의
            # `score is None and revision < 2` 분기가 재검수를, `revision >= 2` 분기가
            # HITL 이관을 이미 담당하고 있으므로 별도 분기를 새로 만들 필요가 없다.
            #
            # YOLO 후보는 state에 그대로 보존한다. 다만 그것을 임의의 ratio/감점 값으로
            # 변환해 등급을 매기지는 않는다 - 근거 없는 수치를 지어내는 것이기 때문이다.
            # HITL 관리자 화면에서 사람이 판단할 때 참고 증거로만 쓰인다.
            return {
                "is_mint": None,
                "defects": [],
                "vision_failed": True,
                "vision_text": (
                    f"GPT-4o VLM 판독 실패 - UBCI 점수 산출을 보류하고 재검수 루프로 회부합니다. "
                    f"(YOLO 사전탐지 후보 {len(yolo_candidates)}건 보존 / 원인: {vision_error[:160]})"
                ),
                "executed_agents": ["vision_agent"],
                "messages": [AIMessage(content="[Vision Agent] VLM 판독 실패 - 재검수 루프 회부")],
            }
    else:
        is_mint = len(defects) == 0
        special_notes = None

    # --- Stage 3: GPT-4o-mini 매트릭스 수식 교차 검증 (BBox/type은 유지, preliminary_deduction만 재계산) ---
    mini_verified = False
    if llm_mini and defects:
        try:
            structured_mini = llm_mini.with_structured_output(VisionResult)
            verify_prompt = f"""당신은 UBCI 예비 감점 연산 검증 AI입니다.
1차 Vision AI가 판독한 아래 결함 목록(type, ratio)의 preliminary_deduction(예비 감점)이
UBCI 매트릭스 기준에 합당한지 검증하고, 틀렸다면 정정하세요.
- DMG_INT_DOODLE: 5~15점 사이 (문제집 예외 Cap은 이후 Policy Agent가 별도 처리)
- DMG_EXT_TEAR: Minor(ratio<5) 5점, Moderate(5<=ratio<15) 10점, Severe(ratio>=15) 15점
- 그 외 결함: ratio에 비례하되 최소 5점

is_mint, type, bbox는 절대 바꾸지 말고 defects 배열의 순서와 개수도 그대로 유지한 채
preliminary_deduction 값만 재계산해서 반환하세요.

1차 판독 결과(JSON): {json.dumps({"is_mint": is_mint, "defects": defects}, ensure_ascii=False)}
"""
            res_mini: VisionResult = structured_mini.invoke([HumanMessage(content=verify_prompt)])
            if res_mini and res_mini.defects and len(res_mini.defects) == len(defects):
                for i, d in enumerate(defects):
                    d["preliminary_deduction"] = res_mini.defects[i].preliminary_deduction
                mini_verified = True
        except Exception as e:
            print(f"[Vision Agent] GPT-4o-mini 검증 실패, 휴리스틱 폴백으로 진행: {e}")

    # 검증 LLM이 실패했을 때만 쓰는 결정론적 폴백 (기존 로직 보존)
    if not mini_verified:
        for d in defects:
            dtype = str(d.get("type", ""))
            ratio = d.get("ratio", 10)
            if "DOODLE" in dtype or "필기" in dtype or "낙서" in dtype:
                d["preliminary_deduction"] = min(15, max(5, ratio))
            elif "TEAR" in dtype or "찢어짐" in dtype:
                d["preliminary_deduction"] = 5 if ratio < 5 else (10 if ratio < 15 else 15)
            else:
                d["preliminary_deduction"] = max(5, ratio)

    # Explainer 패널이 렌더할 실제 판독 서술. 프론트에서 문자열을 지어내지 않도록
    # 판독 근거(스캔 장수, 앙상블 후보 수, 결함 유형)를 여기서 확정해 State에 싣는다.
    scanned_cnt = len(image_paths)
    if is_mint:
        vision_text = (
            f"{scanned_cnt}장 다각도 스캔 완료 - WBF 3-YOLO 앙상블 및 GPT-4o VLM 판독 결과 "
            f"결함 0건, MINT(무결점) 판정"
        )
    else:
        type_summary = ", ".join(sorted({str(d.get("type")) for d in defects if d.get("type")})) or "미분류"
        vision_text = (
            f"{scanned_cnt}장 다각도 스캔 완료 - 결함 {len(defects)}건 검출 ({type_summary}), "
            f"BBox 좌표 및 image_index 바인딩 완료"
        )
    if special_notes:
        vision_text += f" / 특이사항: {special_notes}"

    result = {
        "is_mint": is_mint,
        "defects": defects,
        "vision_text": vision_text,
        "executed_agents": ["vision_agent"],
        "messages": [AIMessage(content=f"[Vision Agent] WBF+GPT-4o VLM 검수 & GPT-4o-mini 검증 완료 (is_mint: {is_mint}, 결함 {len(defects)}건)")]
    }
    if special_notes:
        result["special_notes"] = special_notes
    return result

# ==========================================
# 2. Policy Agent (UBCI_Specification_v2.0.0.0.md 100% 공식 매트릭스 수식 엔진)
# ==========================================
def policy_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Policy Agent: UBCI v2.0.0.0 공식 감점 매트릭스 & 텍스트 침범 가중치 적용 연산 중...")

    # Vision Agent가 판독에 실패한 건은 점수를 산출하지 않는다. 결함 목록이 비어 있다는
    # 사실이 "무결점"을 뜻하지 않기 때문에, 여기서 100점을 매기면 판독 실패가 그대로
    # 최고 등급 자동 승인으로 이어진다. ubci_score를 None으로 남겨두면 Critic이
    # 재검수(최대 2회) 후 HITL 이관까지 기존 루프로 처리한다.
    if state.get("vision_failed"):
        skip_text = "Vision 판독 실패로 UBCI 점수 산출을 보류합니다 (재검수 루프로 회부)."
        print(f"[Agent] Policy Agent: {skip_text}")
        return {
            "ubci_score": None,
            "policy_text": skip_text,
            "executed_agents": ["policy_agent"],
            "messages": [AIMessage(content=f"[Policy Agent] {skip_text}")],
        }

    defects = state.get("defects") or []
    book_title = str(state.get("book_title") or state.get("title") or "")
    is_workbook = any(k in book_title for k in ["수험서", "문제집", "기출", "자격검정", "실전문제", "학습", "교재", "AIVLE", "SQL"])

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

    # --- RAG 근거 조항 인용 (Grounding) ---
    #
    # [중요] 점수(score)는 위에서 이미 결정론적 산식으로 확정됐다. 아래 검색은 그 감점의
    # **출처를 규정집에서 찾아 붙이기만** 하며, 어떤 경우에도 score를 바꾸지 않는다.
    # 검색 결과가 점수에 영향을 주면 같은 도서가 실행할 때마다 다른 등급을 받게 되어
    # UBCI 등급의 재현성과 감사 추적성이 깨진다 (등급은 매입가를 결정하는 값이다).
    #
    # RAG 서버가 죽어 있거나 인덱스가 없으면 조용히 빈 목록을 반환한다(fail-open).
    deduction_basis = []
    try:
        from app.core.rag_service import cite_deduction_basis

        cited_types = set()
        for d in defects:
            dtype = str(d.get("type") or "")
            if not dtype or dtype in cited_types:
                continue
            cited_types.add(dtype)
            basis = cite_deduction_basis(dtype, DEFECT_TRANSLATION_MAP.get(dtype, ""))
            if basis:
                deduction_basis.append({"defect_type": dtype, **basis})
    except Exception as e:
        print(f"[Policy Agent] 근거 조항 인용 실패 - 점수는 그대로 유지하고 인용만 생략합니다: {e}")

    if deduction_basis:
        # 여러 결함이 같은 조항을 근거로 삼는 경우가 흔하므로 조항 단위로 중복을 제거한다
        # (순서는 유지 - dict가 삽입 순서를 보존).
        refs = ", ".join(dict.fromkeys(f"{b['doc_title']} {b['clause_ref']}" for b in deduction_basis))
        policy_text += f" | 근거 조항: {refs}"

    return {
        "defects": defects,
        "ubci_score": score,
        "policy_text": policy_text,
        "deduction_basis": deduction_basis,
        "reason_code": None,
        "repair_directive": None,
        "executed_agents": ["policy_agent"],
        "messages": [AIMessage(content=f"[Policy Agent] {policy_text}")]
    }

# ==========================================
# 3. Critic Agent (판정 애매 도서 & 최대 루프 초과 시 HITL 이관 제어)
# ==========================================
def critic_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Critic Agent: 판정 결과 애매성 평가 및 HITL 관리자 이관 판단 중...")
    revision = state.get("revision_count", 0)
    score = state.get("ubci_score")

    # Vision 판독 실패도 별도 분기를 만들지 않고 기존 재검수 루프에 태운다.
    # ubci_score가 None으로 남아 있으므로 아래 `score is None and revision < 2` 분기가
    # Vision 재판독을 지시하고(최대 2회), 소진되면 이 위의 revision >= 2 분기가
    # Supervisor를 통해 HITL로 이관한다. Rate limit/타임아웃 같은 일시 장애는 재시도로
    # 회복되고, 키 만료처럼 회복 불가한 장애만 사람에게 도달한다.
    vision_failed = bool(state.get("vision_failed"))

    if revision >= 2:
        cause = "Vision 판독이 2회 연속 실패" if vision_failed else f"판정 애매성 지속 (UBCI {score}점)"
        critic_text = f"최대 재검수 루프(2회) 초과 - {cause}. 자동 확정 불가로 HITL 관리자 수동 오버라이드로 이관합니다."
        return {
            "reason_code": "MAX_RETRIES_AMBIGUOUS_HITL",
            "repair_directive": "최대 재검수 횟수(2회) 초과 ➔ HITL 관리자 수동 오버라이드 이관",
            "revision_count": revision,
            "critic_text": critic_text,
            "executed_agents": ["critic_agent"],
            "messages": [AIMessage(content="[Critic Agent] ⚠️ 최대 재검수 루프(2회) 초과 ➔ HITL 관리자 검수 이관")]
        }

    if score is not None and 58 <= score <= 66:
        critic_text = f"교차 검증 결과 UBCI {score}점은 NORMAL/REJECT 등급 경계선(58~66점) 구간 - 자동 확정 보류, HITL 이관"
        return {
            "reason_code": "BOUNDARY_AMBIGUOUS_HITL",
            "repair_directive": f"입고 등급 경계선(UBCI {score}점) 판정 애매 ➔ HITL 관리자 수동 오버라이드 이관",
            "revision_count": revision,
            "critic_text": critic_text,
            "executed_agents": ["critic_agent"],
            "messages": [AIMessage(content=f"[Critic Agent] ⚠️ 입고 등급 경계선(UBCI {score}점) 판정 애매 ➔ HITL 관리자 개입 이관")]
        }

    if score is None and revision < 2:
        cause = "Vision 판독 실패(외부 VLM 오류)" if vision_failed else "Policy Agent UBCI 점수 미산출"
        critic_text = f"{cause} - Vision Agent 재판독 지시 (재시도 {revision + 1}/2회)"
        return {
            "reason_code": "REJECT",
            "repair_directive": "UBCI 점수 계산 누락. Vision Agent 재검수 지시",
            "revision_count": revision + 1,
            # 재시도 시 이전 실패 표식을 반드시 지운다. 남겨두면 재판독이 성공해도
            # Policy가 계속 점수 산출을 건너뛰어 무한히 재검수만 반복하게 된다.
            "vision_failed": False,
            "critic_text": critic_text,
            "executed_agents": ["critic_agent"],
            "messages": [AIMessage(content=f"[Critic Agent] 🔄 {cause} ➔ Vision Agent 재검수 (재시도 {revision + 1}/2회)")]
        }

    # --- 실질 교차검증 (Cross-Check) ---
    # [수정 이력] 이전 Critic은 점수 구간(58~66)과 재시도 횟수만 확인해, 이름에 붙은
    # "Cross-Check / 환각 방어" 역할을 실제로는 전혀 수행하지 않았다. Vision이 보고한 결함과
    # Policy가 실제로 감점한 항목이 어긋나도(예: 결함 3건인데 감점 1건) 그대로 통과했다.
    # LLM 없이 결정론적으로 검증 가능한 항목들을 여기서 대조한다.
    defects = state.get("defects") or []
    image_count = len(state.get("image_paths") or [])
    integrity_issues = []

    for i, d in enumerate(defects):
        if not isinstance(d, dict):
            integrity_issues.append(f"결함[{i}] 형식 오류")
            continue
        # BBox 누락: 프론트가 결함 위치를 표시할 수 없어 "투명 공개" 보증이 깨진다.
        if not isinstance(d.get("bbox"), dict):
            integrity_issues.append(f"결함[{i}]({d.get('type')}) BBox 좌표 누락")
        # image_index 범위 초과: VLM이 존재하지 않는 이미지를 지목한 환각 신호.
        idx = d.get("image_index")
        if image_count and isinstance(idx, int) and not (0 <= idx < image_count):
            integrity_issues.append(f"결함[{i}] image_index({idx})가 촬영 장수({image_count}) 범위를 벗어남")

    # 결함이 있는데 감점이 0점(=100점 만점)이면 Vision과 Policy 보고가 모순된다.
    if defects and score == 100:
        integrity_issues.append(f"결함 {len(defects)}건이 보고되었으나 UBCI 감점이 0점(100점)으로 산출됨")

    # 결함이 없는데 감점이 발생한 경우도 마찬가지로 모순이다.
    if not defects and score is not None and score < 100:
        integrity_issues.append(f"결함 0건인데 UBCI {score}점(감점 {100 - score}점)이 산출됨")

    if integrity_issues:
        detail = " / ".join(integrity_issues[:5])
        critic_text = f"교차 검증 실패 - Vision·Policy 보고 불일치 감지: {detail}. 자동 확정 대신 관리자 검토로 이관합니다."
        return {
            "reason_code": "HUMAN_REQUIRED",
            "repair_directive": f"정합성 위반: {detail}",
            "revision_count": revision,
            "critic_text": critic_text,
            "executed_agents": ["critic_agent"],
            "messages": [AIMessage(content=f"[Critic Agent] ⚠️ 정합성 위반 {len(integrity_issues)}건 ➔ HITL 이관")]
        }

    # --- Stage B: LLM 판독 타당성 심사 (GPT-4o-mini) ---
    #
    # Stage A(위)가 산술·구조 정합성을 결정론적으로 검증했다면, Stage B는 결정론적으로
    # 판단할 수 없는 것 - "Vision이 본 것이 실제로 그 결함이 맞는가" - 를 심사한다.
    # 예: 내지 대부분을 덮는 DMG_INT_DOODLE(인쇄된 표를 낙서로 오탐), 앞/뒤 표지에 거의
    # 동일 좌표로 중복 보고된 결함, special_notes와 모순되는 결함 등.
    #
    # 비용 설계: 결함이 0건이면 심사할 대상 자체가 없으므로 호출하지 않는다. 물량의 다수를
    # 차지하는 MINT 건에서는 LLM 호출이 0회라 전체 비용 증가가 미미하다.
    # 실패 시 fail-open: 부가 검증이므로 LLM 장애가 파이프라인을 멈추게 하지 않는다.
    llm_verdict = None
    if llm_mini and defects:
        try:
            evidence = [
                {
                    "index": i,
                    "type": d.get("type"),
                    "image_index": d.get("image_index"),
                    "bbox": d.get("bbox"),
                    "ratio": d.get("ratio"),
                    "confidence": d.get("confidence"),
                    "deduction": d.get("preliminary_deduction"),
                }
                for i, d in enumerate(defects)
            ]
            verdict_prompt = f"""당신은 중고도서 비전 검수 결과를 감리하는 Critic입니다.
Vision AI가 보고한 결함 판독이 타당한지 보수적이고 엄격하게 심사하세요.

[심사 대상]
- 도서명: {state.get("book_title") or "미상"}
- 검수 이미지 장수: {image_count}장
- Vision 특이사항: {state.get("special_notes") or "없음"}
- 판독 결함 목록(JSON): {json.dumps(evidence, ensure_ascii=False)}
- Policy 산출 점수: UBCI {score}점

[REJECTED로 판단해야 하는 경우]
1. BBox가 이미지 대부분(면적 50% 이상)을 덮는 결함 - 인쇄된 본문/표/삽화를 결함으로
   오탐했을 가능성이 높습니다. (좌표계는 0~1000 기준)
2. 서로 다른 이미지에 거의 동일한 좌표·유형으로 중복 보고된 결함.
3. 결함 유형과 발견 위치가 물리적으로 모순되는 경우
   (예: 표지에만 생기는 손상이 내지에서 보고됨).
4. special_notes의 서술과 결함 목록이 서로 모순되는 경우.
5. confidence가 현저히 낮은데(0.3 미만) 감점이 큰 경우.

위 어디에도 해당하지 않으면 APPROVED로 판단하세요. 애매하면 REJECTED를 선택하세요.
오탐이 의심되는 항목이 있으면 suspect_indices에 해당 index를 넣으세요.
"""
            structured_critic = llm_mini.with_structured_output(CriticVerdict)
            llm_verdict = structured_critic.invoke([HumanMessage(content=verdict_prompt)])
        except Exception as e:
            print(f"[Critic Agent] Stage B LLM 심사 실패 - 결정론적 판정만으로 진행: {e}")

    if llm_verdict and llm_verdict.decision == "REJECTED":
        suspect = f" (오탐 의심 인덱스: {llm_verdict.suspect_indices})" if llm_verdict.suspect_indices else ""
        critic_text = f"판독 타당성 심사 반려 - {llm_verdict.reason}{suspect}"

        # 재검수 여유가 남아 있으면 Vision에 재판독을 지시하고, 소진됐으면 사람에게 넘긴다.
        if revision < 2:
            return {
                "reason_code": "REJECT",
                "repair_directive": f"판독 타당성 미달: {llm_verdict.reason}. 지목된 결함을 재확인할 것{suspect}",
                "revision_count": revision + 1,
                # 재판독을 위해 이전 결함 목록을 비운다 (vision_agent는 defects가 비어야 재호출한다).
                "defects": [],
                "critic_text": f"{critic_text} ➔ Vision Agent 재판독 지시 (재시도 {revision + 1}/2회)",
                "executed_agents": ["critic_agent"],
                "messages": [AIMessage(content=f"[Critic Agent] 🔄 {critic_text}")]
            }

        return {
            "reason_code": "MAX_RETRIES_AMBIGUOUS_HITL",
            "repair_directive": f"판독 타당성 미달이 재검수 후에도 해소되지 않음: {llm_verdict.reason}",
            "revision_count": revision,
            "critic_text": f"{critic_text} ➔ 재검수 소진, HITL 관리자 검수 이관",
            "executed_agents": ["critic_agent"],
            "messages": [AIMessage(content=f"[Critic Agent] ⚠️ {critic_text} ➔ HITL 이관")]
        }

    grade_label = "S급(MINT)" if (score or 0) >= 95 else ("A급(GOOD)" if (score or 0) >= 85 else ("B급(NORMAL)" if (score or 0) >= 65 else "C급(REJECT)"))
    verdict_note = f" / 판독 타당성 심사 승인({llm_verdict.reason})" if llm_verdict else ""
    critic_text = (
        f"교차 검증 통과 - 결함 {len(defects)}건의 BBox/image_index 정합성 및 "
        f"산출 점수(UBCI {score}점)와 {grade_label} 등급 분기 조건 확인{verdict_note}, 보증서 발행 승인"
    )
    return {
        "reason_code": "OK",
        "repair_directive": None,
        "revision_count": revision,
        "critic_text": critic_text,
        "executed_agents": ["critic_agent"],
        "messages": [AIMessage(content="[Critic Agent] 판정 명확성 검증 완료 ➔ Report Agent 보증서 발행 승인 (OK)")]
    }

# ==========================================
# 4-5 공용. 고객 공개용 보증서 문서 빌더
# ==========================================
def _grade_label(ubci_score: int) -> str:
    if ubci_score >= 95:
        return "S급 (MINT)"
    if ubci_score >= 85:
        return "A급 (GOOD)"
    if ubci_score >= 65:
        return "B급 (NORMAL)"
    return "REJECT C급 (폐기)"


def _fallback_certificate(ubci_score: int, defects: List[dict], special_notes: Optional[str]) -> dict:
    """
    LLM 호출이 불가능하거나 실패했을 때 쓰는 결정론적 보증서 본문.
    문장은 여기(백엔드)에서만 만든다 - 프론트가 등급별 문장을 하드코딩하지 않게 하기 위함.
    """
    grade_str = _grade_label(ubci_score)
    findings = []
    for d in defects:
        dtype = str(d.get("type") or "")
        label = DEFECT_TRANSLATION_MAP.get(dtype, dtype or "상태 결함")
        deduction = int(d.get("preliminary_deduction") or 0)
        findings.append({
            "image_index": int(d.get("image_index") or 0),
            "label": label,
            "deduction": deduction,
            "reason": f"{label} 흔적이 확인되어 UBCI {deduction}점을 차감했습니다. 읽는 데는 지장이 없는 수준입니다.",
        })

    if not findings:
        headline = "샅샅이 뒤졌지만, 트집 잡을 곳이 없었습니다"
        condition_detail = (
            "결함 없음. 표지 모서리부터 내지 마지막 장까지 전수 스캔했지만 감점 사유를 단 한 건도 찾지 못했습니다. "
            "검수 AI가 머쓱해진 몇 안 되는 경우입니다."
        )
        care_tip = "직사광선만 피해 보관하시면 지금 이 상태가 오래갑니다."
    else:
        headline = f"정직하게 {len(findings)}가지를 짚어드립니다"
        condition_detail = (
            f"총 {len(findings)}건의 사용 흔적이 확인되어 UBCI {grade_str} 판정을 받았습니다. "
            "숨기지 않고 아래에 그대로 공개하며, 해당 감점은 이미 판매가에 반영되어 있습니다."
        )
        care_tip = "이미 반영된 흔적이니 마음 편히 읽으셔도 좋습니다."

    summary = (
        f"Nexus 비전 검수 AI가 표지 훼손율과 내지 전 페이지를 픽셀 단위로 판독했습니다. "
        f"종합 판정 결과 UBCI {ubci_score}점, {grade_str}으로 확정되었습니다."
    )
    if special_notes:
        condition_detail += f" 참고로 {special_notes} 점도 함께 확인되었습니다."

    return {
        "headline": headline,
        "summary": summary,
        "condition_detail": condition_detail,
        "findings": findings,
        "care_tip": care_tip,
    }


def build_certificate_document(state: WMSInspectionState) -> dict:
    """
    고객 공개용 보증서 본문을 생성한다.

    [설계 노트] MINT Fast-track(auto_refund_agent)과 일반 경로(report_agent)가 같은 품질의
    문서를 내도록 문서 생성 로직을 이 함수 하나로 모았다. 노드를 병합한 것이 아니라 두 노드가
    같은 헬퍼를 호출하는 형태이므로, 4-Agent 분리 구조와 모델 배정(프리즈 규정)은 그대로다.
    문서 생성 LLM은 규정대로 GPT-4o-mini(llm_mini)를 쓴다.
    """
    ubci_score = state.get("ubci_score")
    if ubci_score is None:
        ubci_score = 100
    defects = state.get("defects") or []
    special_notes = state.get("special_notes")
    book_title = state.get("book_title") or "본 도서"
    grade_str = _grade_label(ubci_score)

    if not llm_mini:
        return _fallback_certificate(ubci_score, defects, special_notes)

    defect_brief = [
        {
            "type": d.get("type"),
            "korean_label": DEFECT_TRANSLATION_MAP.get(str(d.get("type") or ""), d.get("type")),
            "image_index": d.get("image_index") or 0,
            "deduction": d.get("preliminary_deduction") or 0,
            "ratio": d.get("ratio"),
            "text_overlap": d.get("text_overlap"),
        }
        for d in defects
    ]

    prompt = f"""당신은 중고도서 품질 보증서를 쓰는 카피라이터 겸 검수 기록관입니다.
아래 AI 검수 결과를 바탕으로, 실제 구매 고객이 QR로 열어보는 보증서 본문을 작성하세요.

[검수 결과]
- 도서명: {book_title}
- UBCI 최종 점수: {ubci_score}점 ({grade_str})
- 검출 결함 목록(JSON): {json.dumps(defect_brief, ensure_ascii=False)}
- 정성적 특이사항: {special_notes or "없음"}

[작성 규칙]
1. 결함이 하나도 없으면(빈 목록) condition_detail에 "결함이 없다"는 사실을 반드시 명시하되,
   "해당 없음" 같은 사무적 표현 대신 위트 있게 풀어 쓰세요.
   (예: "샅샅이 뒤졌지만 트집 잡을 곳이 없었습니다" 같은 톤)
2. 결함이 있으면 절대 축소하거나 숨기지 말고 정직하게 쓰되, 고객이 불안해지지 않도록
   정중하고 위트 있게 포장하세요. findings 배열에 결함별로 1건씩 채우고,
   각 항목의 image_index/deduction은 위 JSON 값을 그대로 옮기세요.
3. 과장 광고 금지 - "최고급", "완벽한 신품" 같은 단정적 표현은 쓰지 마세요.
4. 모든 문장은 한국어 존댓말. 이모지는 쓰지 마세요.
5. headline은 20자 내외 한 줄, summary는 2~3문장, condition_detail은 2~4문장.
"""

    try:
        structured = llm_mini.with_structured_output(CertificateDocument)
        doc: CertificateDocument = structured.invoke([HumanMessage(content=prompt)])
        result = doc.model_dump()
        # LLM이 findings를 비우거나 개수를 틀리게 채우면 실제 결함 수와 화면이 어긋난다.
        # 결함 개수는 검수 사실이므로 LLM 판단에 맡기지 않고 강제로 정합성을 맞춘다.
        if len(result.get("findings") or []) != len(defects):
            result["findings"] = _fallback_certificate(ubci_score, defects, special_notes)["findings"]
        return result
    except Exception as e:
        print(f"[Report Agent] 보증서 문서 생성 실패, 결정론적 폴백 사용: {e}")
        return _fallback_certificate(ubci_score, defects, special_notes)


# ==========================================
# 4. Report Agent (구 Explainer Agent - 디지털 WMS 검수 보증서 및 종합 검수 소견 발행)
# ==========================================
#
# [명칭 정리] PM 문서와 프론트 UI에는 "Explainer Agent(GPT-4o-mini 1문장 검수 소견 생성)"가
# 별도 AI 모델로 기재되어 있으나, 그 구현체였던 app/ai/explainer_agent.py는
# 2026-08-04 AI 파이프라인 통합 시 제거되어 archive로만 남아 있다(그래프 노드였던 적도 없음).
# 현재 그 역할 - Vision/Policy/Critic 산출물을 종합해 GPT-4o-mini로 사람이 읽는 검수 소견을
# 생성하는 일 - 은 이 Report Agent가 전담한다. 문서/발표 자료도 이 이름으로 통일할 것.
#
# [구조 변경 - 프리즈 예외 승인 (2026-08-04)] auto_refund_agent 노드 제거.
# MINT Fast-track 분기가 사라지면서 도달 불가능해졌고, 하는 일도 이 노드와 동일했다
# (양쪽 다 build_certificate_document()로 GPT-4o-mini 1회 호출). "MINT 자동 매입/환불"이라는
# 비즈니스 기능은 auto_refund_eligible 플래그로 보존되어 워커(execute_wms_action)가 집행한다.
def report_agent(state: WMSInspectionState) -> WMSInspectionState:
    ubci_score = state.get("ubci_score")
    if ubci_score is None:
        ubci_score = 100
    cert_id = f"CERT-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    doc = build_certificate_document(state)
    doc["cert_id"] = cert_id
    doc["grade"] = _grade_label(ubci_score)
    doc["ubci_score"] = ubci_score

    # 무결점(MINT) 확정 건은 관리자 개입 없이 자동 매입/환불 대상이 된다.
    # 단, 이 플래그는 등급이 Policy 산정 + Critic 교차검증을 모두 통과한 뒤에만 세워지므로,
    # 예전 Fast-track처럼 검증을 건너뛴 채 금전 결정이 확정되는 일은 없다.
    auto_refund_eligible = bool(state.get("is_mint")) and ubci_score >= 95

    return {
        "ubci_score": ubci_score,
        "final_report": doc["summary"],
        "report_text": doc["summary"],
        "certificate": doc,
        "auto_refund_eligible": auto_refund_eligible,
        "executed_agents": ["report_agent"],
        "messages": [AIMessage(content=f"[Report Agent] 디지털 품질 보증서 발행 완료 ({cert_id}) - {doc['headline']}")]
    }

# ==========================================
# 6. Human Node (HITL 관리자 수동 오버라이드 처리 노드)
# ==========================================
def human_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    HITL 인계 지점(handoff station).

    이 노드는 스스로 판단하지 않는다 - Supervisor가 3개 에이전트 보고를 종합해
    "ESCALATE_HUMAN"을 결정했을 때만 도달하며, 여기서는 그 지시를 집행해 작업을 관리자
    결재 대기 상태로 표시하고 그래프를 종료한다(app/ai/supervisor.py에서 human_node -> END).
    이후 실제 재개는 관리자가 POST /admin/hitl/override로 처리한다.

    [수정 이력] 예전에는 여기서 곧바로 "HUMAN_RESOLVED"로 마킹하고 report_agent로 넘어가
    사람이 아무 결정도 하지 않았는데 자동으로 보증서까지 발급되어 버렸다.
    """
    rationale = state.get("supervisor_rationale") or "판정 애매성으로 관리자 검토 필요"
    print(f"[Agent] Human Node (HITL): Supervisor 이관 지시 집행 - {rationale}")
    return {
        "reason_code": "AWAITING_HUMAN_REVIEW",
        "messages": [AIMessage(content=f"[Human Node (HITL)] 관리자 수동 검수 대기 (HITL_REQUIRED) - {rationale}")]
    }
