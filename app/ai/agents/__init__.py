import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from app.ai.state import WMSInspectionState

# WBF YOLO 클래스명 -> UBCI_Specification_v2.0.0.0.md 결함 코드 매핑
# 속지(Track 2·3) 컷에서 **제외**하는 결함 유형 (2026-08-07).
#
# 모서리 마모는 물리적으로 책의 겉면(표지·뒤표지·책등)에서 발생하는 현상이다. 속지 컷에
# 마모처럼 보이는 것이 잡히더라도 그 부위는 표지·책등 컷에서 이미 검출되므로, 속지에서
# 또 세면 같은 손상을 두 번 세는 것이 된다.
#
# 그래서 속지 컷의 마모는 VLM 보고분도, YOLO 후보도 모두 버린다. 판정 주체를 옮기는 것이
# 아니라 **애초에 판정 대상에서 빼는 것**이다.
#
# 표지·뒤표지·책등(Track 1)은 영향을 받지 않는다 - 거기서는 VLM이 여러 컷을 함께 보고
# 종합 판단하며, YOLO 후보는 참고 제보로 그대로 제시된다.
INNER_PAGE_EXCLUDED_TYPES = {"DMG_EDGE_WEAR"}


def _is_inner_page(image_index) -> bool:
    """속지(Track 2·3) 컷인지. Track 1(0=앞표지,1=뒤표지,2=책등)은 제외한다."""
    try:
        return int(image_index) >= TRACK1_IMAGE_COUNT
    except (TypeError, ValueError):
        return False

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


# GPT-4o Vision 전송본의 긴 변 상한(px). S3 원본은 FHD(1920px)로 적재되지만 OpenAI는
# 512px 타일 단위로 과금하므로, 원본을 그대로 보내면 YOLO가 아닌 VLM 쪽 비용만 커진다.
# YOLO(WBF)는 로컬 풀해상도 크롭을 쓰고, VLM에는 이 상한으로 다운스케일한 사본만 보낸다.
VLM_MAX_IMAGE_EDGE = 1536


def _downscale_for_vlm(data: bytes) -> bytes:
    """이미지 바이트의 긴 변이 VLM_MAX_IMAGE_EDGE를 넘으면 비율 유지 다운스케일한 JPEG 반환.

    실패 시 원본 바이트를 그대로 반환한다(fail-open) - 다운스케일은 비용 최적화일 뿐
    판독 자체를 막을 이유가 없다. 업스케일은 절대 하지 않는다.
    """
    try:
        import io
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            if max(img.size) <= VLM_MAX_IMAGE_EDGE:
                return data
            img = img.convert("RGB")
            img.thumbnail((VLM_MAX_IMAGE_EDGE, VLM_MAX_IMAGE_EDGE), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            return buf.getvalue()
    except Exception as e:
        print(f"[Vision Agent] VLM 다운스케일 실패({e}) - 원본 바이트로 전송")
        return data


def _load_image_as_base64(path_or_url: str) -> Optional[str]:
    """로컬 파일 경로 또는 HTTP(S) URL을 GPT-4o Vision에 넣을 base64 문자열로 인코딩.

    전송 직전 _downscale_for_vlm으로 긴 변 1536px 상한을 적용한다 (원본 파일은 불변).
    """
    import base64
    try:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            import urllib.request
            req = urllib.request.Request(path_or_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                return base64.b64encode(_downscale_for_vlm(response.read())).decode("utf-8")
        with open(path_or_url, "rb") as f:
            return base64.b64encode(_downscale_for_vlm(f.read())).decode("utf-8")
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

class InnerPageRegion(BaseModel):
    """속지(펼친 내지) 사진에서 낙서 탐지를 돌릴 영역.

    [Track 2·3] doodle 모델은 AIHub 손글씨 **크롭 패치** 1만 장으로 학습돼 있어,
    인쇄면 전체를 그대로 넣으면 활자를 손글씨로 오인한다(실측: 깨끗한 속지 1장에
    오탐 12건, 전부 인쇄 본문). VLM이 지면 영역을 먼저 좁혀 주면 학습 도메인에
    가까운 입력이 되고, 손이나 배경도 함께 제외된다.
    """
    # 좌표를 Dict가 아니라 평탄한 int 필드로 둔다. OpenAI structured output(strict)은
    # Dict[str, int]를 필수 필드로 쓰면 "Extra required key" 오류로 요청 자체를 거부한다.
    image_index: int = Field(description="이 영역이 속한 원본 이미지의 인덱스 (0부터)")
    xmin: int = Field(description="지면 영역 좌측 x (0~1000 상대좌표)")
    ymin: int = Field(description="지면 영역 상단 y (0~1000 상대좌표)")
    xmax: int = Field(description="지면 영역 우측 x (0~1000 상대좌표)")
    ymax: int = Field(description="지면 영역 하단 y (0~1000 상대좌표)")


class DefectDetail(BaseModel):
    type: str = Field(description="결함의 종류 (예: DMG_INT_DOODLE, DMG_INT_STAIN, DMG_EXT_CRUSH, DMG_EXT_WET 등)")
    ratio: int = Field(description="전체 면적 대비 결함의 상대적 비율 (%)")
    level: Optional[int] = Field(
        default=None,
        description="변색/황변(DMG_INT_DISCOLOR) 전용 강도 1~3. 황변은 지면 전체에 나타나 "
                    "면적(ratio)이 항상 100%가 되므로 면적 대신 강도로 판정한다. "
                    "1=종이 끝만 살짝 바램(자연 노화) / 2=전반적으로 뚜렷한 황변 / "
                    "3=짙은 갈색·곰팡이성 얼룩 동반. 변색이 아닌 결함에는 넣지 않는다",
    )
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
    # [2026-08-04 조장 승인 확장] 현장 촬영 컷 중 도서가 식별되지 않는 이미지(작업자 얼굴만
    # 찍힘, 빈 배경, 심한 초점 이탈 등)의 인덱스 목록. HITL/상세 화면이 해당 컷을 "결함
    # 미검출(정상처럼 보임)"이 아니라 "도서 미식별 컷"으로 구분·필터링하는 데 쓴다.
    invalid_image_indexes: List[int] = Field(
        default_factory=list,
        description="도서가 식별되지 않는 이미지의 인덱스 목록 (0=정면 촬영 순서 기준). 모든 컷에 도서가 보이면 빈 배열",
    )
    # [2026-08-06 Track 2·3] 속지 컷의 지면 영역. doodle 모델을 이 영역에만 돌린다.
    inner_page_regions: List[InnerPageRegion] = Field(
        default_factory=list,
        description="펼친 속지(내지)가 찍힌 컷의 지면 영역 목록. 속지 컷이 없으면 빈 배열",
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
    # 증거 대조 검증 전용. [2026-08-07 조장 승인] 4o-mini는 판독 타당성 심사에서
    # 확정 결함 전건을 오탐으로 반려하는 등 신뢰할 수 없는 결과를 냈다. 결함이 1건 이상일
    # 때만 도는 경로라 MINT 물량에는 추가 비용이 없다.
    llm_verify = ChatOpenAI(model="gpt-4o", temperature=0.0)
except Exception:
    llm_vlm = None
    llm_mini = None
    llm_verify = None

# 촬영 규격상 Track 1(WBF 앙상블 담당)이 맡는 앞쪽 이미지 장수.
# 인덱스 0=앞면, 1=뒷면, 2=책등. 3번 이후는 책배·속지로 VLM이 판독한다.
TRACK1_IMAGE_COUNT = 3

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

    # --- Track 1 범위 한정 (2026-08-06) ---
    # 촬영 규격상 인덱스 0·1·2는 앞면·뒷면·책등으로 고정된다. 이 세 각도는 학습셋
    # (Roboflow 811장)에 같은 구도가 존재하므로 WBF 앙상블이 담당한다.
    #   - 앞뒤 표지 약 50%, 책등 약 40%
    #   - 반면 책배(종이 단면)는 80장 표본에 2~3장뿐이라 사실상 학습된 적이 없다.
    #
    # 인덱스를 규격으로 고정하면 이 세 장은 VLM 분류를 거칠 필요가 없어,
    # "VLM이 표지를 책배로 오분류하는" 실패 경로가 원천 차단된다.
    #
    # 3번 이후(책배·속지)는 vision_agent가 GPT-4o로 직접 판독한다. 모델이 배운 적 없는
    # 면에 바운딩 박스를 강요하지 않는다.
    #
    # 얇은 문고본·중철 제본은 책등 촬영을 스킵할 수 있으므로, 실제 장수가 3장 미만이면
    # 있는 만큼만 처리한다.
    track1_paths = image_paths[:TRACK1_IMAGE_COUNT]

    try:
        from app.ai.wbf_detector import wbf_detector
        for idx, path in enumerate(track1_paths):
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
        skipped = len(image_paths) - len(track1_paths)
        detector_text = (
            f"WBF 3-YOLO 앙상블 사전탐지 완료 - Track 1 {len(track1_paths)}장"
            f"(앞면·뒷면·책등)에서 결함 후보 {len(yolo_candidates)}건 검출"
            + (f" / 책배·속지 {skipped}장은 VLM 판독으로 회부" if skipped > 0 else "")
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
# Vision Agent 판독 프롬프트 본문.
# [2026-08-06] vision_agent 내부 리터럴에서 모듈 상수로 승격. 문자열은 한 글자도
# 바꾸지 않았다 - 판독기 A/B 측정(app/scripts/ab_vision_sonnet.py)이 **같은 프롬프트**를
# 써야 결과를 비교할 수 있는데, 복제해 두면 한쪽만 고쳐질 때 비교가 조용히 무의미해진다.
def build_yolo_hint(yolo_candidates: List[Dict[str, Any]]) -> str:
    """YOLO 사전탐지 후보를 판독 프롬프트에 붙일 문구로 만든다.

    [2026-08-06] 후보를 JSON 그대로(=confidence 포함) 넣으면 모델이 그것을 **답으로 베낀다.**
    실측: 후보 5건과 확정 결함 5건의 confidence가 소수점 4자리까지 동일
    (0.7578 / 0.7947 / 0.5241 / 0.4370 / 0.5903, job b7b34ae1). 후보에 잡음이 많을 때는
    말이 안 되는 것들을 걸러내며 "판독하는 것처럼" 보였지만, 후보 품질이 올라가자 전건
    복사가 드러났다. 즉 판독이 아니라 통과 도장이었다.

    그래서 후보에서 **confidence를 제거**하고 위치·유형만 넘긴다. 베낄 확신도가 없으면
    스스로 판단해 적을 수밖에 없다. 동시에 후보를 "정답 목록"이 아니라 **기각해야 할 수도
    있는 제보**로 제시한다.

    vision_agent와 판독기 A/B 측정(app/scripts/ab_vision_sonnet.py)이 이 함수를 공유한다.
    문구를 복제해 두면 한쪽만 고쳐질 때 비교가 조용히 무의미해진다.
    """
    if not yolo_candidates:
        return "\n\n[사전 YOLO 제보 없음 - 이미지에서 직접 결함을 판독할 것]"

    # Detector가 소유한 유형은 제보에서 뺀다 - 단, **속지 컷에 한해서다.**
    # 보여주면 다시 재심 구조가 되고 그게 전건 승인의 원인이었다.
    # 표지·책등(Track 1) 후보는 그대로 제시한다. 그쪽은 VLM의 종합 판단을 유지한다.
    hint_items = [
        {k: v for k, v in c.items() if k != "confidence"}
        for c in yolo_candidates
        if not (
            _is_inner_page(c.get("image_index"))
            and YOLO_TO_UBCI_TYPE.get(c.get("defect_type", ""), "") in INNER_PAGE_EXCLUDED_TYPES
        )
    ]
    if not hint_items:
        return "\n\n[사전 YOLO 제보 없음 - 이미지에서 직접 결함을 판독할 것]"
    return (
        "\n\n[사전 YOLO 제보 - **정답이 아니다**. 오탐이 섞여 있으며 전부 기각해도 된다]\n"
        f"{json.dumps(hint_items, ensure_ascii=False)}\n"
        "위 좌표를 눈으로 확인해 실제로 결함이 보이는 것만 채택하고, 보이지 않으면 버려라.\n"
        "제보에 없더라도 직접 본 결함은 반드시 추가하라.\n"
        "confidence는 제보 값이 아니라 **당신이 이미지를 보고 판단한 확신도**를 적어라."
    )


VISION_PROMPT_BASE = """당신은 WMS 디지털 품질 검수 센터의 수석 AI 비전(VLM) 검수원입니다.
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
6. 이미지 유효성(invalid_image_indexes) — invalid 판정은 **최후 수단**:
   - invalid로 지정할 수 있는 컷은 딱 두 가지뿐입니다:
     (a) 도서가 프레임에 **전혀 존재하지 않는** 컷 (작업자 얼굴/신체만 찍힘, 빈 배경/책상)
     (b) 초점 이탈·모션 블러가 심해 도서 표면의 상태를 물리적으로 읽을 수 없는 컷
   - 다음은 전부 **유효한 컷**입니다. invalid로 지정하지 말고 반드시 판독하세요:
     · 도서를 손에 들고 비스듬히 기울여 찍은 컷 (현장 웹캠 촬영의 기본 형태입니다)
     · 도서가 프레임 일부에만 걸쳐 있거나, 사람 얼굴·손·의자·배경과 함께 찍힌 컷
     · 표지가 아닌 책배(종이 단면)·책등·펼친 속지가 찍힌 컷
     · 표지 글자가 안 읽혀도 종이 상태(주름·오염·마모)는 판독 가능한 컷
   - 결함 판정(defects)은 유효한 컷에 대해서만 수행하고, 도서 미식별 컷에서는 결함을 보고하지
     마세요. 모든 컷에 도서가 보이면 빈 배열([])로 두세요.
   - **전 컷 invalid 지정은 "검수 불가" 선언과 같으며**, 시스템이 그 건을 자동 확정하지 않고
     관리자 수동 검수(HITL)로 이관합니다. 확신 없이 전 컷을 invalid로 만들지 말고,
     조금이라도 판독 가능한 컷은 판독을 시도한 뒤 낮은 confidence로 보고하세요.

7. [image_index 규칙 — 반드시 지킬 것]
   image_index는 **첨부된 이미지의 순서**입니다. 각 이미지 바로 앞에 `[이미지 index=N]`
   이라는 표시가 붙어 있으니 **그 숫자를 그대로 사용**하세요.
   - 사진에 무엇이 찍혔는지로 번호를 추측하지 마세요. 첨부 순서가 유일한 기준입니다.
   - 첨부된 이미지가 N장이면 사용 가능한 index는 0부터 N-1까지뿐입니다.
     그 범위를 벗어난 숫자를 절대 쓰지 마세요.

8. [촬영 순서 관례] 통상 0=앞면, 1=뒷면, 2=책등, 3번 이후=책배(종이 단면) 또는 속지입니다.
   다만 얇은 책은 책등 촬영을 생략할 수 있어 이 관례가 항상 맞지는 않습니다.
   **번호는 위 7번(첨부 순서)을 따르고, 각 사진의 실제 내용은 눈으로 보고 판단하세요.**
   - 앞면·뒷면·책등은 별도 YOLO 앙상블이 이미 검사했습니다(아래 후보 목록 참조).
   - 책배·속지는 그 모델이 학습한 적 없는 각도이므로 **당신이 직접 판독**해야 합니다.
     종이 단면의 마모·오염·변색, 내지의 얼룩·물 젖음·찢어짐을 빠짐없이 보고하세요.

9. [속지 지면 영역 — 결함 유무와 무관하게 반드시 채울 것]
   **펼쳐진 책의 내지(본문 페이지)가 보이는 컷이 하나라도 있으면**, 그 컷마다
   inner_page_regions 항목을 하나씩 만드세요. 이 배열은 결함 보고와 별개입니다.
   결함이 없어도, 깨끗한 속지여도 **지면이 보이면 반드시 채웁니다.**
   - 손가락, 책상, 배경을 제외하고 **종이 지면만** 감싸도록 좌표를 잡습니다.
   - 한 컷에 양쪽 페이지가 보이면 전체를 하나의 영역으로 묶어도 됩니다.
   - image_index는 위 7번 규칙(첨부 순서)을 그대로 따릅니다.
   - 표지·책등만 찍힌 컷이거나 내지가 전혀 안 보이면 그 컷은 넣지 마세요.
     모든 컷에 내지가 없으면 빈 배열([])입니다.

10. [변색 강도] 황변/변색(DMG_INT_DISCOLOR)을 보고할 때는 level에 1~3을 넣으세요.
   황변은 지면 전체에 나타나 면적으로는 심각도를 구분할 수 없기 때문입니다.
   1=종이 끝만 살짝 바램(중고책의 자연스러운 노화) / 2=전반적으로 뚜렷 / 3=짙은 갈색·곰팡이성.
   오래된 중고책이 누렇게 뜬 것은 정상 범위이므로 함부로 2~3을 주지 마세요.

11. [물 젖음/습기 손상 — DMG_EXT_WET, 놓치기 쉬우니 특히 주의]
   물에 젖었다 마른 책은 **찢김이나 얼룩 없이도** 아래 형태로 드러납니다. 하나라도 보이면
   DMG_EXT_WET으로 보고하세요 (색이 아니라 **종이의 기하학적 변형**을 보는 것이 핵심):
   - 책배(종이 단면)가 매끈한 직선이 아니라 **물결처럼 쭈글쭈글하게 부풀어** 있음
   - 책을 덮었는데 지면이 평평하지 않고 **두께가 부위별로 다르게 부풀어(팽윤)** 있음
   - 지면에 **물결 주름(cockling)·굴곡**이 잡혀 빛을 받는 면에 줄무늬 음영이 생김
   - 종이 가장자리를 따라 **얼룩진 경계선(tide line)**이 남아 있음
   - 표지가 물결지거나 뒤틀려 들뜸 / 코팅이 우글거림
   판단 기준: 새 책의 종이 단면은 **자로 그은 듯 균일한 직선**입니다. 그 직선이 무너져
   울퉁불퉁하면 물 손상을 의심하는 것이 정상입니다. 사용 중 자연스럽게 생기는 모서리
   마모(DMG_EDGE_WEAR)와 구별하세요 — 마모는 모서리 국소, 물 손상은 지면 전체의 파형입니다.
   - 확신이 서지 않으면 **보고하지 않고 넘기지 말고**, confidence를 0.4~0.6으로 낮춰
     보고하세요. 판독 누락(놓침)이 낮은 확신도 보고보다 훨씬 큰 손실입니다.

12. [담당 범위 — 표지는 종합 판단, 속지는 찢어짐 중심]
   **표지·뒤표지·책등(0~2번 컷)은 당신이 종합적으로 판단합니다.** 여러 컷을 함께 보고
   같은 결함인지 다른 결함인지까지 정리해 주세요. 제보는 참고일 뿐 기각해도 됩니다.

   **속지(3번째 이후 컷)에서는 모서리 마모(DMG_EDGE_WEAR)를 보고하지 마세요.**
   마모는 책의 겉면에서 생기는 손상이고, 속지 컷에 측면이 걸려 보이더라도 그 부위는
   표지·책등 컷에서 이미 판정됩니다. 여기서 또 세면 같은 손상을 두 번 세는 것입니다.
   속지에서 당신이 볼 것은 **찢어짐(DMG_EXT_TEAR)** 입니다.

   그 밖에 당신이 맡는 것은 탐지 모델이 할 수 없는 판단입니다:
   - 물 젖음/습기(DMG_EXT_WET) — 지면 전체의 기하학적 변형
   - 오염·얼룩(DMG_INT_STAIN), 황변·변색(DMG_INT_DISCOLOR) — 면적/강도 판단
   - 찢어짐(DMG_EXT_TEAR) — 특히 속지가 찢겼는지
   - 인쇄물과 손글씨의 구별, 도서 미식별 컷 판정

13. [판독 원칙 — 종합]
   - 이 검수 결과는 **실제 매입 대금**을 결정합니다. "결함 0건"은 "결함을 못 찾았다"가 아니라
     "정밀 판독 결과 무결점임을 보증한다"는 선언입니다. 확신이 없으면 0건으로 확정하지 말고
     낮은 confidence로라도 보고하거나, 판독 불가 컷은 invalid로 명시하세요.
   - 촬영 컷 전체가 물리적으로 판독 불가한 경우가 아니라면, **반드시 각 컷을 끝까지 살펴본 뒤**
     결과를 내세요. 사진이 지저분하거나 각도가 나쁘다는 이유로 판독을 포기하지 마세요.
"""


def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    print("[Agent] Vision Agent: GPT-4o VLM 정밀검수 -> GPT-4o-mini 예비감점 검증 중...")
    defects = state.get("defects") or []
    image_paths = state.get("image_paths") or []
    # Detector Node가 앞서 채워둔 앙상블 후보 (VLM 실패 시 폴백 근거로도 쓰인다)
    yolo_candidates = state.get("yolo_candidates") or []

    if llm_vlm and not defects and image_paths:
        # --- GPT-4o VLM 정밀 검수 (WBF 후보를 컨텍스트로 제공, 최종 판단은 VLM이 직접) ---
        structured_vlm = llm_vlm.with_structured_output(VisionResult)
        # [2026-08-06 수정] 후보를 JSON 그대로(=confidence 포함) 넣으면 모델이 그것을 **답으로
        # 베낀다.** 실측: 후보 5건과 확정 결함 5건의 confidence가 소수점 4자리까지 동일
        # (0.7578 / 0.7947 / 0.5241 / 0.4370 / 0.5903, job b7b34ae1). 후보에 잡음이 많을 때는
        # 말이 안 되는 것들을 걸러내며 "판독하는 것처럼" 보였지만, 후보 품질이 올라가자
        # 전건 복사가 드러났다. 즉 판독이 아니라 통과 도장이었다.
        #
        # 그래서 후보에서 **confidence를 제거**하고 위치·유형만 넘긴다. 모델이 베낄 확신도가
        # 없으면 스스로 판단해 적을 수밖에 없다. 동시에 후보를 "정답 목록"이 아니라
        # **기각해야 할 수도 있는 제보**로 제시한다.
        prompt_vlm = VISION_PROMPT_BASE + build_yolo_hint(yolo_candidates)

        # 각 이미지 바로 앞에 인덱스 라벨을 끼워 넣는다.
        # [2026-08-06] 라벨 없이 이미지만 나열하면 VLM이 **첨부 순서가 아니라 사진 내용으로**
        # 번호를 매긴다. 실측: 속지 1장만 넣었는데 프롬프트의 "3번 이후=속지" 관례를 보고
        # image_index=3을 반환했고, 4장 입력에서는 존재하지 않는 index=4를 지목했다.
        # 범위 밖 인덱스는 Critic이 환각으로 판정해 HITL로 보내므로, 프롬프트 탓에 매번
        # HITL이 걸리는 상태가 된다. 라벨로 앵커를 박아 순서를 강제한다.
        content_list = [{"type": "text", "text": prompt_vlm}]
        for i, path in enumerate(image_paths):
            b64 = _load_image_as_base64(path)
            if b64:
                content_list.append({"type": "text", "text": f"[이미지 index={i}]"})
                content_list.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

        special_notes = None
        vision_error = None
        invalid_image_indexes: list = []
        inner_page_regions: list = []
        try:
            res_vlm: VisionResult = structured_vlm.invoke([HumanMessage(content=content_list)])
            is_mint = res_vlm.is_mint
            defects = [d.model_dump() for d in res_vlm.defects]
            special_notes = res_vlm.special_notes
            # 범위를 벗어난 인덱스는 VLM 환각 신호이므로 버린다 (Critic의 image_index 검증과 동일 원칙)
            invalid_image_indexes = sorted({
                int(i) for i in (res_vlm.invalid_image_indexes or [])
                if isinstance(i, (int, float)) and 0 <= int(i) < len(image_paths)
            })
            # 속지 지면 영역도 같은 원칙으로 범위 검증한다. 도서 미식별 컷은 제외한다
            # (판독 불가로 분류된 컷에 낙서 탐지를 돌릴 이유가 없다).
            inner_page_regions = [
                r.model_dump() for r in (res_vlm.inner_page_regions or [])
                if 0 <= int(r.image_index) < len(image_paths)
                and int(r.image_index) not in invalid_image_indexes
            ]
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
        invalid_image_indexes = []
        inner_page_regions = []

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

    # --- Track 2·3: 속지 지면 크롭에 doodle 단독 추론 ---
    # VLM이 지정한 지면 영역만 잘라 doodle 모델에 넣는다. 인쇄면 전체를 넣으면 활자를
    # 손글씨로 오인하므로(실측: 깨끗한 속지 1장에 오탐 12건), 학습 도메인인 "손글씨 크롭
    # 패치"에 가까운 입력을 만들어 준다. 크롭본과 탐지 결과는 로컬에 적재해 나중에 검증한다.
    doodle_added = 0
    for region in (inner_page_regions or []):
        try:
            idx = int(region.get("image_index", -1))
            if not (0 <= idx < len(image_paths)):
                continue
            local_path = _ensure_local_path(image_paths[idx])
            if not local_path:
                continue

            import cv2
            from app.ai.wbf_detector import wbf_detector, detect_page_region

            img = cv2.imread(local_path)
            if img is None:
                continue
            ih, iw = img.shape[:2]
            # 좌표는 YOLO-World(공간 판단)를 우선 쓰고, 실패 시 VLM 좌표로 폴백한다.
            # VLM은 "이 사진이 속지인가"(의미 판단)까지만 신뢰한다 - 좌표 정확도는 낮다.
            region_src = "yoloworld"
            wr = detect_page_region(img)
            if wr is not None:
                x1 = max(0, min(iw - 1, int(wr[0] * iw)))
                y1 = max(0, min(ih - 1, int(wr[1] * ih)))
                x2 = max(x1 + 1, min(iw, int(wr[2] * iw)))
                y2 = max(y1 + 1, min(ih, int(wr[3] * ih)))
            else:
                region_src = "vlm"
                x1 = max(0, min(iw - 1, int(region.get("xmin", 0) / 1000 * iw)))
                y1 = max(0, min(ih - 1, int(region.get("ymin", 0) / 1000 * ih)))
                x2 = max(x1 + 1, min(iw, int(region.get("xmax", 1000) / 1000 * iw)))
                y2 = max(y1 + 1, min(ih, int(region.get("ymax", 1000) / 1000 * ih)))
            crop = img[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            hits = wbf_detector.detect_doodle_only(
                crop,
                debug_tag=f"idx{idx}_inner",
                debug_meta={
                    "source_image": os.path.basename(local_path),
                    "image_index": idx,
                    "region_source": region_src,  # yoloworld | vlm(폴백)
                    "vlm_region": {k: region.get(k) for k in ("xmin", "ymin", "xmax", "ymax")},
                    "crop_px": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                },
            )
            # 크롭 기준 0~1000 좌표를 원본 이미지 기준으로 역변환한다.
            # 프론트는 S3 원본 위에 BBox를 그리므로 원본 좌표여야 한다.
            cw, ch = (x2 - x1), (y2 - y1)
            for hd in hits:
                b = hd["bbox"]
                defects.append({
                    "type": YOLO_TO_UBCI_TYPE.get("doodle_scribble", "DMG_INT_DOODLE"),
                    "ratio": max(1, int((b["xmax"] - b["xmin"]) * (b["ymax"] - b["ymin"]) / 10000)),
                    "confidence": hd.get("confidence"),
                    "image_index": idx,
                    "bbox": {
                        "xmin": int((x1 + b["xmin"] / 1000 * cw) / iw * 1000),
                        "ymin": int((y1 + b["ymin"] / 1000 * ch) / ih * 1000),
                        "xmax": int((x1 + b["xmax"] / 1000 * cw) / iw * 1000),
                        "ymax": int((y1 + b["ymax"] / 1000 * ch) / ih * 1000),
                    },
                })
                doodle_added += 1
        except Exception as e:
            # 부가 탐지이므로 실패가 판독 결과를 폐기시키지 않는다.
            print(f"[Vision Agent] 속지 크롭 doodle 추론 실패({type(e).__name__}) - 건너뜀: {e}")

    if doodle_added:
        is_mint = False
        print(f"[Vision Agent] 속지 크롭 doodle 낙서 {doodle_added}건 추가")

    # Explainer 패널이 렌더할 실제 판독 서술. 프론트에서 문자열을 지어내지 않도록
    # 판독 근거(스캔 장수, 앙상블 후보 수, 결함 유형)를 여기서 확정해 State에 싣는다.
    scanned_cnt = len(image_paths)

    # --- 속지 마모 제외 (2026-08-07, 조장 지시) ---
    #
    # 모서리 마모는 책의 겉면에서 발생한다. 속지 컷에 측면 마모가 걸려 보이더라도 그 부위는
    # 표지·책등 컷에서 이미 검출되므로, 속지에서 또 세면 같은 손상을 이중으로 계산하게 된다.
    # 판정 주체를 옮기는 것이 아니라 **판정 대상에서 빼는 것**이다.
    #
    # 표지·뒤표지·책등(Track 1)의 마모는 그대로 VLM 종합 판단에 맡긴다 - 여러 컷을 함께 보고
    # 같은 모서리인지 다른 모서리인지 정리하는 것은 탐지 모델이 할 수 없는 일이다.
    before = len(defects)
    defects = [
        d for d in defects
        if not (
            _is_inner_page(d.get("image_index"))
            and str(d.get("type", "")) in INNER_PAGE_EXCLUDED_TYPES
        )
    ]
    inner_wear_dropped = before - len(defects)
    if inner_wear_dropped:
        print(f"[Vision Agent] 속지 마모 {inner_wear_dropped}건 제외 (겉면에서 중복 검출되는 부위)")

    # --- 확신도 출처 확정 + YOLO 제보 복사 탐지 (결정론적) ---
    #
    # [2026-08-07 실측] job b7b34ae1에서 확정 결함 5건의 BBox가 YOLO 제보와 **픽셀 단위로
    # 완전히 동일**한데도 복사 탐지가 한 건도 걸리지 않았다. 종전 탐지는 confidence 값의
    # 일치만 봤는데, VLM은 좌표는 그대로 베끼면서 confidence만 전건 0.8로 덮어썼기 때문이다
    # (실제 제보값은 0.758 / 0.795 / 0.524 / 0.437 / 0.59). 즉 감시 대상 필드가 틀렸다.
    #
    # 두 가지를 분리해서 처리한다.
    #  1) 출처(conf_source): BBox가 제보와 일치하면 그 결함의 확신도는 **제보의 실측값**이다.
    #     VLM이 써 넣은 값은 근거 없는 자기 신고이므로 실측값으로 교체하고 원본을 보존한다.
    #     제보에 없는 좌표면 VLM 단독 판독이므로 "추정치"임을 명시한다.
    #     (제보 채택 자체는 정상 동작이다 - 위반이 아니다)
    #  2) 위반(conf_copied_from_candidate): VLM이 이미지를 보지 않았다는 신호만 남긴다.
    #     - 확신도까지 제보와 소수점 4자리로 동일 (종전 기준)
    #     - 또는 확정 결함 2건 이상에 **완전히 동일한 확신도**를 부여 (평평한 자기 신고)
    copied_conf = 0
    if defects and yolo_candidates:
        cand_confs = {
            round(float(c["confidence"]), 4)
            for c in yolo_candidates
            if c.get("confidence") is not None
        }
        for d in defects:
            try:
                if round(float(d.get("confidence")), 4) in cand_confs:
                    d["conf_copied_from_candidate"] = True
                    copied_conf += 1
            except (TypeError, ValueError):
                continue

        # 1) BBox 대조로 확신도 출처를 확정한다 (IoU >= 0.9 = 사실상 같은 상자)
        for d in defects:
            best, best_iou = None, 0.0
            for c in yolo_candidates:
                if int(c.get("image_index") or 0) != int(d.get("image_index") or 0):
                    continue
                iou = _bbox_iou(d.get("bbox"), c.get("bbox"))
                if iou > best_iou:
                    best, best_iou = c, iou
            if best is not None and best_iou >= 0.9 and best.get("confidence") is not None:
                d["conf_source"] = "yolo"
                d["conf_vlm_selfreported"] = d.get("confidence")
                d["confidence"] = round(float(best["confidence"]), 4)
                d["conf_bbox_iou"] = round(best_iou, 3)
            else:
                d["conf_source"] = "vlm"

    # 2) 평평한 자기 신고 탐지 - VLM 단독 판독분의 확신도가 전부 같은 값이면 판단이 아니다
    vlm_confs = [
        round(float(d.get("confidence")), 4)
        for d in defects
        if d.get("conf_source") != "yolo" and d.get("confidence") is not None
    ]
    if len(vlm_confs) >= 2 and len(set(vlm_confs)) == 1:
        for d in defects:
            if d.get("conf_source") != "yolo":
                d["conf_flat_selfreported"] = True
                d["conf_copied_from_candidate"] = True
        copied_conf = max(copied_conf, len(vlm_confs))

    # --- 증거 대조 검증 (GPT-4o, BBox 크롭 건별 심사) ---
    #
    # [호출 위치] 속지 마모 제외와 확신도 출처 확정이 **끝난 뒤**에 돈다.
    #   - 앞에서 돌면 곧 폐기될 속지 결함까지 심사해 비용만 쓴다.
    #   - 고확신 면제(VERIFY_EXEMPT_CONF)는 conf_source/confidence가 확정돼야 판단할 수 있다.
    #     VLM이 써낸 자기 신고(전건 0.8)로 면제를 판단하면 전건이 면제돼 버린다.
    #
    # 오탐으로 지목된 항목은 제거하지 않고 표식만 남긴다 - 여기서 지우면 Critic/Supervisor가
    # 볼 근거가 사라지고, 판정 책임이 이 함수로 넘어와 버린다.
    verify_verdict = verify_defects_with_images(
        defects,
        image_paths,
        book_title=str(state.get("book_title") or state.get("title") or ""),
        special_notes=special_notes,
    )
    verify_note = None
    if verify_verdict is not None:
        suspects = set(verify_verdict.suspect_indices or [])
        for i, d in enumerate(defects):
            if i in suspects:
                d["evidence_suspect"] = True
        if verify_verdict.decision == "REJECTED":
            verify_note = (
                f"증거 대조 검증 반려 - {verify_verdict.reason}"
                + (f" (오탐 의심 인덱스: {sorted(suspects)})" if suspects else "")
            )
        else:
            verify_note = f"증거 대조 검증 통과 - {verify_verdict.reason}"
        print(f"[Vision Verify] {verify_note}")

    if is_mint:
        vision_text = (
            f"{scanned_cnt}장 다각도 스캔 완료 - WBF 3-YOLO 앙상블 및 GPT-4o VLM 판독 결과 "
            f"결함 0건, MINT(무결점) 판정"
        )
    else:
        type_summary = ", ".join(sorted({str(d.get("type")) for d in defects if d.get("type")})) or "미분류"
        copy_warn = (
            f" / ⚠ 판독 신뢰 불가: 확정 결함 {copied_conf}건의 확신도가 YOLO 제보 값과 동일합니다"
            f"(이미지를 직접 판단한 것이 아니라 제보를 되돌려준 것으로 보임) - 관리자 확인 필요"
            if copied_conf else ""
        )
        vision_text = (
            f"{scanned_cnt}장 다각도 스캔 완료 - 결함 {len(defects)}건 검출 ({type_summary}), "
            f"BBox 좌표 및 image_index 바인딩 완료{copy_warn}"
        )
    if special_notes:
        vision_text += f" / 특이사항: {special_notes}"
    if invalid_image_indexes:
        vision_text += f" / 도서 미식별 컷 {len(invalid_image_indexes)}장 제외 (인덱스: {invalid_image_indexes})"
    if verify_note:
        vision_text += f" / {verify_note}"

    result = {
        "is_mint": is_mint,
        "defects": defects,
        "vision_text": vision_text,
        "invalid_image_indexes": invalid_image_indexes,
        "executed_agents": ["vision_agent"],
        "messages": [AIMessage(content=f"[Vision Agent] WBF+GPT-4o VLM 검수 & GPT-4o-mini 검증 완료 (is_mint: {is_mint}, 결함 {len(defects)}건)")]
    }
    if special_notes:
        result["special_notes"] = special_notes
    return result

# ==========================================
# 1-b. 증거 대조 검증 (Vision 종합 검증) - GPT-4o-mini
# ==========================================
#
# 도입 배경 - 현행 Critic은 이미지를 보지 못한다:
#   critic_agent의 Stage B 프롬프트에는 image_url 파트가 없다. 전달되는 것은 도서명,
#   "이미지 장수(숫자)", 결함 목록 JSON, UBCI 점수뿐이다. 즉 "환각 방어" 담당이
#   **실제 증거(픽셀)를 한 번도 보지 않고** "BBox가 면적 50% 이상이면 인쇄물 오탐 의심",
#   "다른 이미지에 동일 좌표 중복" 같은 메타 규칙만으로 오탐을 추정해 왔다.
#
# vision_agent 안에는 이미 이미지가 컨텍스트에 올라와 있으므로, 여기서 한 번 더 심사하면
# 재업로드 없이 실제 증거를 보고 판정할 수 있다.
#
# Critic Stage B는 **제거하지 않고 그대로 둔다 (이중 검증)**:
#   - 본 함수      : 이미지를 본다. 판독이 증거와 맞는가 (증거 타당성)
#   - Critic Stage B: 점수를 본다. 판독과 UBCI가 정합한가 · 경계선인가 (정합성 · 라우팅)
#   두 검증은 보는 대상이 달라 실패 양상이 독립적이고, 서로의 약점을 덮는다.
#   (본 함수는 판독 맥락 안에 있어 동조 편향 위험이 있고, Critic은 독립적이나 눈이 멀었다.)
#
# 프리즈 규정("각 단계는 별도 노드/함수로 유지, 단일 프롬프트로 병합 금지") 준수를 위해
# vision 판독 프롬프트에 합치지 않고 **독립 함수 + 독립 프롬프트**로 분리한다.
# 동조 편향을 줄이기 위해 앞선 판독의 추론 과정은 넘기지 않고,
# **이미지와 확정된 결함 목록만** 새로 구성해 전달한다.
#
# 비용: 결함이 0건이면 호출하지 않는다(MINT 물량에서 추가 비용 0).
# 실패 시 fail-open - 부가 검증이므로 LLM 장애가 판독 결과를 폐기시키지 않는다.

# 증거 대조 검증을 면제하는 YOLO 실측 확신도 하한.
# 물리 탐지 모델이 이 이상으로 잡은 검출을, 좌표를 픽셀로 환산하지도 못하는 LLM이
# 뒤집게 두면 근거의 위계가 역전된다 (실측: 76% 검출이 기각되어 100점 MINT가 됐다).
VERIFY_EXEMPT_CONF = float(os.getenv("WMS_VERIFY_EXEMPT_CONF", "0.60"))

# BBox 주변을 얼마나 넓게 잘라 볼지. 결함만 딱 자르면 맥락(주변 표지면과의 대비)이
# 사라져 판단이 어려워지므로 여유를 준다.
VERIFY_CROP_EXPAND = 3.0
VERIFY_CROP_SIZE = 512


def _crop_around_bbox(
    path_or_url: str,
    bbox: Dict[str, Any],
    expand: float = VERIFY_CROP_EXPAND,
    out_size: int = VERIFY_CROP_SIZE,
) -> Optional[str]:
    """BBox 주변을 잘라 확대한 이미지를 base64로 돌려준다.

    [왜 자르는가] 전체 이미지를 넣고 "0~1000 좌표계의 (48,121)-(78,139)를 보라"고
    요구하면 VLM은 그 위치를 찾지 못한다. 정규화 좌표를 픽셀로 환산하는 능력이 없고,
    그 영역은 1536px 축소본 기준 **35x28px, 전체의 0.055%**라 보이지도 않는다.
    결과적으로 검증자는 지목된 곳 대신 이미지 전체 인상으로 답했고, 그래서 판정이
    0건 아니면 전건이라는 이분법으로 흔들렸다 (실측 job b7b34ae1).

    볼 곳을 미리 잘라 512px로 확대해 주면 좌표 환산 문제가 사라지고 작은 결함도
    실제로 보인다. Track 2·3의 속지 doodle 추론이 쓰는 크롭 전략과 같은 원리다.
    """
    try:
        from PIL import Image
        import base64
        import io

        local = _ensure_local_path(path_or_url)
        if not local:
            return None
        img = Image.open(local).convert("RGB")
        iw, ih = img.size

        x1 = int(bbox["xmin"]) / 1000.0 * iw
        y1 = int(bbox["ymin"]) / 1000.0 * ih
        x2 = int(bbox["xmax"]) / 1000.0 * iw
        y2 = int(bbox["ymax"]) / 1000.0 * ih
        if x2 <= x1 or y2 <= y1:
            return None

        # 여백은 **짧은 변**을 기준으로 계산해 축마다 같은 양을 더한다.
        #
        # [주의] 긴 변 기준 정사각 크롭으로 만들면 안 된다. 모서리 마모는 33x374px 같은
        # 가늘고 긴 띠 형태가 흔한데, 긴 변에 배율을 곱하면 1122px 정사각이 되어 표지
        # 전체가 잡힌다 - 확대해서 보여 주려던 목적이 정확히 무산된다(실측 defect#3).
        pad = max(min(x2 - x1, y2 - y1) * (expand - 1) / 2, 24.0)
        left = max(0, int(x1 - pad))
        top = max(0, int(y1 - pad))
        right = min(iw, int(x2 + pad))
        bottom = min(ih, int(y2 + pad))
        crop = img.crop((left, top, right, bottom))
        if crop.width < 8 or crop.height < 8:
            return None

        # 종횡비를 유지한 채 긴 변을 out_size에 맞춘다. 정사각으로 늘이면 띠 형태 결함이
        # 왜곡되고, 여백을 채워 정사각을 만들면 그 여백이 또 하나의 판단 대상이 된다.
        scale = out_size / max(crop.width, crop.height)
        if scale > 1:
            crop = crop.resize(
                (max(8, round(crop.width * scale)), max(8, round(crop.height * scale))),
                Image.LANCZOS,
            )

        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"[Vision Verify] 크롭 실패({type(e).__name__}) - 해당 건 심사 생략: {e}")
        return None


class DefectEvidenceVerdict(BaseModel):
    """크롭 1장에 대한 단일 결함 심사 결과."""
    visible: Literal["YES", "NO", "UNCLEAR"] = Field(
        description="확대된 이미지 중앙부에 보고된 유형의 손상이 실제로 보이는가. "
                    "보이면 YES, 명백히 보이지 않으면(깨끗한 면·인쇄물·배경 등) NO, "
                    "판단이 어려우면 UNCLEAR"
    )
    reason: str = Field(description="판단 근거 한 문장 (한국어)")


def verify_defects_with_images(
    defects: List[Dict[str, Any]],
    image_paths: List[str],
    book_title: str = "",
    special_notes: Optional[str] = None,
) -> Optional[CriticVerdict]:
    """확정된 결함을 **BBox 크롭 단위로 하나씩** 원본 이미지와 대조해 심사한다.

    [2026-08-07 재설계 — 조장 승인] 종전에는 전체 이미지 여러 장과 결함 목록 전체를
    한 번의 호출에 넣고 판정 하나를 받았다. 두 가지가 동시에 잘못됐다.

      1) 검증자가 지목된 좌표를 찾지 못한다 (_crop_around_bbox 주석 참조).
      2) 5건을 한 번에 물으면 그 호출의 전반적 인상이 5건 전부에 동시에 걸린다.
         **결과가 0건 아니면 전건이었고 2~3건이 나온 적이 없다** - 개별 심사를 하지
         않았다는 지문이다. temperature=0이므로 샘플링 문제가 아니다.

    건별 크롭 + 건별 호출로 바꿔 두 원인을 함께 제거한다. 판정이 독립적이 되므로
    중간값이 나올 수 있고, 크롭이 작아 호출 수가 늘어도 총 토큰은 오히려 줄어든다.

    [면제] YOLO 실측 확신도가 VERIFY_EXEMPT_CONF 이상인 검출은 심사하지 않는다.
    물리 탐지 결과를 LLM이 뒤집는 것은 근거의 위계가 뒤바뀐 것이기 때문이다.

    [보수적 처리] UNCLEAR는 오탐으로 보지 않는다. 판단이 어렵다는 것은 결함이 없다는
    뜻이 아니므로, 애매할 때 검출을 지워 점수를 올리지 않는다.

    Returns:
        CriticVerdict (decision / reason / suspect_indices) 또는 심사 불가 시 None.
    """
    if not llm_verify or not defects or not image_paths:
        return None

    structured = llm_verify.with_structured_output(DefectEvidenceVerdict)
    suspects: List[int] = []
    exempt = 0
    judged = 0
    skipped = 0
    notes: List[str] = []

    for i, d in enumerate(defects):
        # --- 면제 (B): 고확신 YOLO 검출은 심사 대상이 아니다 ---
        conf = d.get("confidence")
        if (
            d.get("conf_source") == "yolo"
            and isinstance(conf, (int, float))
            and float(conf) >= VERIFY_EXEMPT_CONF
        ):
            d["verify_status"] = "exempt_high_conf"
            exempt += 1
            continue

        idx = int(d.get("image_index") or 0)
        bbox = d.get("bbox")
        if not isinstance(bbox, dict) or not (0 <= idx < len(image_paths)):
            d["verify_status"] = "skipped_no_bbox"
            skipped += 1
            continue

        b64 = _crop_around_bbox(image_paths[idx], bbox)
        if not b64:
            d["verify_status"] = "skipped_crop_failed"
            skipped += 1
            continue

        dtype = str(d.get("type") or "")
        label = DEFECT_TRANSLATION_MAP.get(dtype, dtype or "상태 결함")
        prompt = f"""아래는 중고도서 검수 사진에서 **결함으로 지목된 부위를 확대한 이미지**입니다.
이미지 중앙부에 "{label}"({dtype})이 실제로 보이는지만 판단하세요.

- 도서명: {book_title or "미상"}
- 판독 특이사항: {special_notes or "없음"}

[판단 기준]
- 이미지는 지목 부위를 중심으로 주변 여유를 포함해 잘라 확대한 것입니다.
  가장자리가 아니라 **중앙부**를 보세요.
- 인쇄된 활자·삽화·표는 손상이 아닙니다. 인쇄물은 규칙적인 행·열을 이루고 색이
  균일하지만, 손글씨/낙서는 필압과 기울기가 불규칙합니다.
- 손·책상·배경 등 도서가 아닌 물체 위의 표시는 손상이 아닙니다.
- 조명 반사나 그림자는 손상이 아닙니다.
- 확대 과정에서 화질이 거칠어졌더라도, 손상의 형태가 보이면 YES입니다.
- 확신이 서지 않으면 NO가 아니라 **UNCLEAR**를 고르세요. NO는 "명백히 손상이 없다"는
  뜻이며, 이 판단은 실제 손상을 감점에서 지우는 데 쓰입니다.
"""
        try:
            res: DefectEvidenceVerdict = structured.invoke([
                HumanMessage(content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ])
            ])
        except Exception as e:
            # 건별 fail-open: 한 건이 실패해도 나머지 심사는 계속한다.
            print(f"[Vision Verify] 결함 #{i} 심사 실패({type(e).__name__}) - 건너뜀: {e}")
            d["verify_status"] = "skipped_llm_error"
            skipped += 1
            continue

        judged += 1
        d["verify_visible"] = res.visible
        d["verify_reason"] = res.reason
        if res.visible == "NO":
            d["verify_status"] = "rejected"
            suspects.append(i)
            notes.append(f"#{i} {label}: {res.reason}")
        else:
            d["verify_status"] = "confirmed" if res.visible == "YES" else "unclear"

    if judged == 0 and not suspects:
        # 전건 면제/생략이면 심사를 한 적이 없다. 통과로 위장하지 않는다.
        print(f"[Vision Verify] 심사 대상 없음 (면제 {exempt}건 / 생략 {skipped}건)")
        return None

    summary = (
        f"크롭 대조 심사 {judged}건 (고확신 면제 {exempt}건"
        + (f" / 생략 {skipped}건" if skipped else "")
        + f") - 오탐 지목 {len(suspects)}건"
    )
    if notes:
        summary += " : " + " ; ".join(notes[:3])

    return CriticVerdict(
        decision="REJECTED" if suspects else "APPROVED",
        reason=summary,
        suspect_indices=suspects,
    )


def _bbox_iou(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> float:
    """두 BBox({xmin,ymin,xmax,ymax}, 0~1000 정규화)의 IoU. 좌표가 없으면 0.0.

    확정 결함이 YOLO 제보와 같은 상자인지 판정하는 데 쓴다. 값 비교(confidence)가 아니라
    좌표 비교여야 하는 이유는 vision_agent 쪽 주석 참조 - VLM은 좌표를 그대로 베끼면서
    확신도만 임의 값으로 덮어쓴다.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return 0.0
    try:
        ax1, ay1, ax2, ay2 = (int(a["xmin"]), int(a["ymin"]), int(a["xmax"]), int(a["ymax"]))
        bx1, by1, bx2, by2 = (int(b["xmin"]), int(b["ymin"]), int(b["xmax"]), int(b["ymax"]))
    except (KeyError, TypeError, ValueError):
        return 0.0

    iw = max(0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return (inter / union) if union > 0 else 0.0


def _effective_ratio(d: Dict[str, Any], default: int = 5) -> int:
    """감점 구간 판정에 쓸 면적비(%)를 돌려준다. VLM이 비우면 **BBox 면적에서 유도**한다.

    [배경] VLM이 `ratio`를 0으로 내려보내는 경우가 실측으로 확인됐다(LPN-260806-A001,
    결함 5건 전부 ratio=0). 면적 기반 3단계 구간(마모·오염·찢어짐·긁힘 등)은 이 값이
    0이면 전부 최하위 구간으로 떨어져 **심각도 차등이 통째로 무력화**된다.

    좌표는 이미 있으므로 계산할 수 있다. BBox는 0~1000 정규화이므로 넓이비는
    (w/1000)x(h/1000)이고, 이는 "결함이 이미지에서 차지하는 면적 비율"이라는
    UBCI 규정의 정의와 같은 축이다. 실제 손상부는 박스보다 작을 수 있으나 **단조**
    (마모가 클수록 박스도 크다)하므로 구간 판정 목적에는 충분하다.

    좌표조차 없으면 근거가 없으므로 기존 기본값(5%)을 유지한다 - 없는 값을 크게
    잡아 감점을 부풀리지 않는다.
    """
    try:
        r = int(d.get("ratio") or 0)
    except (TypeError, ValueError):
        r = 0
    if r > 0:
        return r

    bbox = d.get("bbox") or {}
    try:
        w = (int(bbox["xmax"]) - int(bbox["xmin"])) / 1000.0
        h = (int(bbox["ymax"]) - int(bbox["ymin"])) / 1000.0
    except (KeyError, TypeError, ValueError):
        return default
    if w <= 0 or h <= 0:
        return default

    # 감사 추적: 이 값이 VLM 보고가 아니라 좌표에서 유도된 것임을 남긴다.
    d["ratio_source"] = "bbox"
    return max(1, round(w * h * 100))


def _edge_wear_profile(defects: List[Dict[str, Any]]) -> tuple[set, int]:
    """모서리 마모를 **책 기준 모서리 위치**로 묶어 (위치 집합, 최대 면적비)를 돌려준다.

    [왜 건수로 세지 않는가]
    한 권에는 모서리가 여러 개고 보통 함께 닳는다. 게다가 같은 구석이 앞표지 컷·뒤표지 컷·
    책등 컷에 모두 잡히므로, 검출 건수로 감점하면 **촬영 각도와 컷 수에 따라 같은 책이 다른
    점수**를 받는다(재현성 붕괴). 그래서 "서로 다른 모서리 몇 곳이 닳았는가"로 센다.

    [왜 image_index로 묶지 않는가]
    컷 번호는 물리적 면이 아니다. 앞표지를 찍을 때 책등 쪽 모서리가 같이 보이고, 그 다음
    책등 컷에서 같은 모서리가 다시 잡힌다. 컷으로 세면 한 곳을 두 곳으로 세어 이중 감점된다.

    [어떻게 공간으로 계산하는가]
    BBox는 컷마다 독립 좌표계(0~1000)라 컷을 가로질러 직접 겹쳐볼 수 없다. 대신 각 박스를
    **책을 정면에서 본 좌표계(book frame)의 모서리 라벨**로 환산해 집합으로 만든다.

      - 세로: 중심 y < 33% → TOP, > 67% → BOTTOM, 그 사이 → MID
      - 가로: 앞표지(0)는 화면 좌우 그대로, **뒤표지(1)는 좌우를 뒤집는다**
        (책을 돌려 찍으므로 실제 같은 쪽 모서리가 화면에서는 반대편에 온다)
      - 책등(2)은 좁은 띠라 가로 위치가 의미 없다. 세로 위치만 확정된 **와일드카드**로 두고,
        같은 세로 구간에 이미 표지 모서리가 있으면 그것과 동일한 곳으로 간주해 합친다.
      - 그 외 컷(속지·책배 등)은 표지와 좌표 기준이 달라 섞으면 오히려 왜곡되므로,
        가로를 판정하지 않고 책등과 같은 와일드카드 규칙을 따른다.

    한국 단행본은 좌철(제본이 왼쪽)이 일반적이라는 관례를 쓰지 않는다 - 좌/우를 그대로
    쓰되 뒤표지만 반전하므로, 제본 방향을 몰라도 앞뒤 대응이 맞는다.
    """
    corners: set = set()
    wildcard_rows: set = set()   # 가로를 알 수 없는 검출의 세로 구간
    max_ratio = 0

    for d in defects:
        dtype = str(d.get("type", "") or d.get("label", ""))
        if not ("WEAR" in dtype or "마모" in dtype):
            continue
        if d.get("evidence_suspect"):     # 오탐 지목분은 위치 계산에서도 뺀다
            continue

        max_ratio = max(max_ratio, _effective_ratio(d, default=0))

        bbox = d.get("bbox") or {}
        try:
            cy = (int(bbox["ymin"]) + int(bbox["ymax"])) / 2
            cx = (int(bbox["xmin"]) + int(bbox["xmax"])) / 2
        except (KeyError, TypeError, ValueError):
            # 좌표가 없으면 위치를 특정할 수 없다. 새 모서리로 세면 근거 없이 감점이
            # 늘어나므로 와일드카드(중앙 행)로 처리해 기존 모서리에 흡수시킨다.
            wildcard_rows.add("MID")
            continue

        row = "TOP" if cy < 333 else ("BOTTOM" if cy > 667 else "MID")
        idx = d.get("image_index")

        if idx == 0:                      # 앞표지 - 화면 좌우 그대로
            col = "L" if cx < 500 else "R"
        elif idx == 1:                    # 뒤표지 - 좌우 반전
            col = "R" if cx < 500 else "L"
        else:                             # 책등·기타 컷 - 가로 판정 불가
            wildcard_rows.add(row)
            continue

        corners.add((row, col))

    # 와일드카드는 같은 세로 구간에 표지 모서리가 이미 있으면 그것과 같은 곳으로 본다.
    # 없을 때만 독립된 한 곳으로 계산한다 (책등만 닳은 경우).
    for row in wildcard_rows:
        if not any(r == row for r, _ in corners):
            corners.add((row, "SIDE"))

    return corners, max_ratio


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

    # 모서리 마모는 건수가 아니라 "책의 서로 다른 모서리 몇 곳이 닳았는가"로 센다.
    # (같은 구석이 앞표지·뒤표지·책등 컷에 모두 잡히면 물리적으로는 한 곳이다)
    wear_corners, wear_max_ratio = _edge_wear_profile(defects)

    deduction_items = []
    total_deduction = 0
    suspect_excluded = []  # 증거 대조 검증이 오탐으로 지목해 감점에서 제외한 항목
    is_fatal_reject = False
    fatal_reason = ""
    edge_wear_added = False
    doodle_workbook_added = False
    wear_group_ded = wear_group_spread = 0
    wear_group_sev = wear_group_ratio = None

    for d in defects:
        dtype = str(d.get("type", "") or d.get("label", ""))
        ratio = _effective_ratio(d)   # VLM이 비우면 BBox 면적에서 유도
        page_cnt = d.get("page_count") or d.get("pages") or 1
        text_overlap = d.get("text_overlap", False) or "본문" in str(d.get("description", ""))
        label = DEFECT_TRANSLATION_MAP.get(dtype) or dtype or "상태 결함"

        # 증거 대조 검증(verify_defects_with_images)이 오탐으로 지목한 결함은 감점하지 않는다.
        # 목록에서는 지우지 않으므로 HITL 화면과 BBox 오버레이에는 그대로 보이고,
        # 다만 매입가를 좌우하는 점수에는 반영하지 않는다 - 판독이 증거와 어긋난다고
        # 판정된 항목으로 판매자에게 불이익을 주지 않기 위함이다.
        #
        # 전부 오탐으로 걸러져 감점이 0이 되면 score가 100이 되는데, 그 경우는
        # critic_agent Stage A의 "결함 N건인데 감점 0점" 정합성 검사가 잡아 HITL로 이관한다.
        # (프리즈 규정: "검수하지 못했다"와 "흠이 없다"를 같게 취급하지 않는다)
        if d.get("evidence_suspect"):
            suspect_excluded.append(label)
            d["applied_deduction"] = 0
            d["deduction_scope"] = "excluded"
            d["deduction_note"] = "증거 대조 검증이 오탐으로 지목 - 감점 제외"
            continue

        # 🚨 치명적 결함 즉시 반려 (UBCI Spec v2.0.0.0 Section 1 & Section 4)
        if "WET" in dtype or "WATER" in dtype or "WARPING" in dtype or "침수" in dtype or "휨" in dtype:
            is_fatal_reject = True
            fatal_reason = "🚨 액체 오염(Water Stain) 또는 페이지 휨(Warping) 감지 ➔ UBCI v2.0.0.0 규정에 의거 즉시 반려(REJECT)"
            deduction_items.append((label, 100, f"{label} (치명적 결함 ➔ 즉시 반려)"))
            break

        if "WEAR" in dtype or "마모" in dtype:
            # 마모는 건당이 아니라 **부위 단위 그룹 감점**이므로, 개별 BBox에 감점을 쪼개
            # 붙이면 안 된다. 오버레이가 건당 감점처럼 읽히지 않도록 그룹임을 명시한다.
            # (실측: 5건 검출 / 부위 3곳 / 그룹 합산 -7점 - 종전 표시는 건당 -5점이라
            #  화면상 -25점으로 읽혔다)
            d["deduction_scope"] = "group"
            d["deduction_group"] = "EDGE_WEAR"
            if not edge_wear_added:
                # 심각도(최대 면적비) + 확산도(서로 다른 모서리 수)로 산정하고 총 -15점 Cap.
                # 종전에는 상태와 무관하게 -5 단일 고정이라, 살짝 닳은 책과 헤질 정도로 닳은
                # 책이 같은 점수를 받았고 모서리 마모만으로는 어떤 경우에도 S급(>=95)을
                # 벗어날 수 없었다(=95점 고정). 등급이 매입가를 결정하므로 차등이 필요하다.
                base_ded = 3 if wear_max_ratio < 5 else (5 if wear_max_ratio < 15 else 10)
                spread = max(1, len(wear_corners))
                spread_ded = (spread - 1) * 2
                wear_ded = min(15, base_ded + spread_ded)

                sev = "경미" if base_ded == 3 else ("보통" if base_ded == 5 else "심함")
                detail = f"모서리 마모 (-{wear_ded}점, {sev} 면적 {wear_max_ratio}% / 마모 부위 {spread}곳"
                detail += ", 총 -15점 Cap 적용)" if base_ded + spread_ded > 15 else ")"

                deduction_items.append((label, wear_ded, detail))
                total_deduction += wear_ded
                edge_wear_added = True
                wear_group_ded, wear_group_spread = wear_ded, spread
                wear_group_sev, wear_group_ratio = sev, wear_max_ratio
        elif "DOODLE" in dtype or "필기" in dtype or "낙서" in dtype:
            if is_workbook:
                d["deduction_scope"] = "group"
                d["deduction_group"] = "WORKBOOK_DOODLE"
                d["applied_deduction"] = 15
                d["deduction_note"] = "수험서/문제집 전체 필기 -15점 단일 Cap (건별 합산 아님)"
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
                d["applied_deduction"] = final_ded
                d["deduction_scope"] = "single"
                d["deduction_note"] = f"{'페이지 5장 초과' if page_cnt > 5 else '페이지 5장 이하'}{overlap_str}"
        # --- 오염(STAIN) : 면적 기준 3단계 ---
        # 국소적 결함이라 넓을수록 심각하므로 기존 매트릭스와 동일하게 ratio를 축으로 쓴다.
        # doodle 분기와 같은 패턴으로 여기서 직접 append하고 아래 공용 1.5배 가중치를 타지
        # 않는다 - 면적 구간에서 이미 심각도를 반영하므로 이중 가중이 된다(-20 x 1.5 = -30).
        elif "STAIN" in dtype or "오염" in dtype or "얼룩" in dtype:
            base_ded = 5 if ratio < 5 else (10 if ratio < 15 else 20)
            total_deduction += base_ded
            deduction_items.append((label, base_ded, f"{label} (-{base_ded}점, 면적 {ratio}%)"))
            d["applied_deduction"] = base_ded
            d["deduction_scope"] = "single"
            d["deduction_note"] = f"면적 {ratio}% 구간"

        # --- 변색/황변(DISCOLOR) : 강도 기준 3단계 ---
        # 황변은 페이지 전면에 나타나므로 ratio가 항상 ~100%가 되어 면적이 의미를 갖지 못한다.
        # (면적으로 재면 세월 먹은 정상적인 헌책이 매번 최고 감점을 맞는다)
        # VLM이 level 1~3으로 강도를 보고하며, 중고책의 자연 노화를 불량으로 폐기하지 않도록
        # L1~L2는 등급에 영향을 주지 않는 수준으로 관대하게 설계했다.
        # 이 타입도 text_overlap 가중치에서 제외한다 - 전면적 결함이라 가중치가 항상
        # 발동해 차등 기능을 하지 못하고 모든 변색 감점을 1.5배로 부풀리기만 한다.
        elif "DISCOLOR" in dtype or "변색" in dtype or "황변" in dtype:
            level = d.get("level")
            try:
                level = int(level)
            except (TypeError, ValueError):
                level = 1  # 강도 미보고 시 가장 관대한 단계로 처리
            level = min(3, max(1, level))
            base_ded = {1: 2, 2: 5, 3: 10}[level]
            total_deduction += base_ded
            deduction_items.append((label, base_ded, f"{label} (-{base_ded}점, 강도 L{level})"))
            d["applied_deduction"] = base_ded
            d["deduction_scope"] = "single"
            d["deduction_note"] = f"황변 강도 L{level} (면적 아님)"

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
            d["applied_deduction"] = final_ded
            d["deduction_scope"] = "single"
            d["deduction_note"] = f"면적 {ratio}% 구간{overlap_str}"

    # 마모 그룹 감점을 소속 결함 전체에 동일하게 새긴다. 건별 합산이 아니라는 사실을
    # 화면이 그대로 읽을 수 있어야 한다 (오버레이가 지어내지 않도록 값과 문구를 함께 준다).
    if edge_wear_added:
        for d in defects:
            if d.get("deduction_group") != "EDGE_WEAR" or d.get("deduction_scope") == "excluded":
                continue
            d["applied_deduction"] = wear_group_ded
            d["deduction_note"] = (
                f"모서리 마모 {wear_group_spread}곳 합산 -{wear_group_ded}점 "
                f"({wear_group_sev} 면적 {wear_group_ratio}%) - 건별 합산 아님"
            )

    score_unverified = False
    if is_fatal_reject:
        score = 0
        grade_str = "REJECT C급 (폐기)"
        decision_str = "REJECT"
        policy_text = f"UBCI v2.0.0.0 사내 수석 룰 적용 ➔ {fatal_reason}"
    else:
        score = max(0, min(100, 100 - total_deduction))
        grade_str = "S급 (MINT)" if score >= 95 else ("A급 (GOOD)" if score >= 85 else ("B급 (NORMAL)" if score >= 65 else "REJECT C급 (폐기)"))
        decision_str = "APPROVE" if score >= 65 else "REJECT"

        # [2026-08-07 실측 버그] 증거 대조 검증이 결함을 **전건** 오탐으로 지목하면 감점이
        # 0이 되어 UBCI 100점 / S급(MINT)이 나온다. LPN-260806-A001 재검수에서 마모 5건이
        # 전건 제외되며 "결함이 전혀 없는 상태"라는 보증서 문구까지 생성됐다 - 5곳이 검출된
        # 책이 무결점 최상급으로 확정된 것이다.
        #
        # 종전 설계는 critic_agent Stage A가 이 상태를 잡아 HITL로 보내는 것으로 충분하다고
        # 봤다. 이관은 실제로 동작하지만 **점수·등급·보증서 문구는 그대로 남아** 화면과
        # 고객 보증서에 100점 MINT로 표시된다. 이관은 집행을 막을 뿐 표시를 고치지 않는다.
        #
        # 검증이 판독을 전부 기각했다는 것은 "흠이 없다"가 아니라 "판독하지 못했다"이므로,
        # 무결점 등급을 주지 않고 판정을 보류한다. (프리즈 규정과 같은 원칙)
        score_unverified = bool(defects) and total_deduction == 0 and bool(suspect_excluded)
        if score_unverified:  # noqa: SIM102 - 아래 분기들이 이 플래그를 함께 읽는다
            grade_str = "판정 보류 (증거 대조 전건 반려)"
            decision_str = "HITL"

        if deduction_items:
            deduction_str = " + ".join([item[2] for item in deduction_items])
            policy_text = f"UBCI v2.0.0.0 공식 매트릭스 적용 ➔ {deduction_str} = 총 {total_deduction}점 감점 (UBCI {score}점 / {grade_str} / 처분: {decision_str})"
        elif score_unverified:
            # "결함 없음"이라고 쓰면 안 된다 - 결함은 보고됐고 검증이 그것을 기각했을 뿐이다.
            policy_text = (
                f"UBCI v2.0.0.0 공식 매트릭스 적용 ➔ 보고된 결함 {len(defects)}건이 증거 대조 "
                f"검증에서 **전건** 오탐으로 지목되어 감점 근거가 남지 않았습니다 "
                f"(UBCI 산출 불가 / {grade_str} / 처분: {decision_str})"
            )
        else:
            policy_text = f"UBCI v2.0.0.0 공식 매트릭스 적용 ➔ 결함 없음 (UBCI {score}점 / {grade_str} / 처분: {decision_str})"
        # 감점에서 제외된 항목은 감사 추적을 위해 반드시 남긴다. 기록하지 않으면
        # "Vision은 결함을 보고했는데 Policy가 조용히 무시한" 것처럼 보인다.
        if suspect_excluded:
            policy_text += (
                f" / 증거 대조 검증에서 오탐으로 지목되어 감점 제외: "
                f"{', '.join(suspect_excluded)} ({len(suspect_excluded)}건)"
            )
            # 같은 유형이 이미 감점에 반영돼 있으면(Cap·묶음 산정 타입) 제외해도 총점은
            # 그대로다. 그 사실을 밝히지 않으면 HITL 검수자와 보증서 독자가 "오탐을 빼서
            # 점수가 올라갔다"고 잘못 읽는다.
            scored_labels = {item[0] for item in deduction_items}
            if all(lb in scored_labels for lb in suspect_excluded):
                policy_text += " (동일 유형이 이미 감점에 반영되어 총점 변동 없음)"

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
        # 증거 대조가 판독을 전건 기각해 점수의 근거가 남지 않은 상태.
        # Report Agent가 "결함 없음" 문구를 쓰지 못하게 하고, Critic이 HITL로 이관한다.
        "score_unverified": score_unverified,
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

    # 확신도를 YOLO 제보에서 그대로 베낀 결함 - 이미지를 보고 판단한 결과가 아니다.
    # Vision Agent가 결정론적으로 표시해 둔 플래그를 여기서 정합성 위반으로 승격시킨다.
    # (판독을 신뢰할 수 없으므로 자동 확정 금지 - 결함 자체는 근거로 보존한다)
    copied = [i for i, d in enumerate(defects) if isinstance(d, dict) and d.get("conf_copied_from_candidate")]
    if copied:
        integrity_issues.append(
            f"결함 {len(copied)}건의 확신도가 YOLO 제보 값과 완전히 일치 - VLM이 이미지를 "
            f"직접 판단하지 않고 제보를 반환한 것으로 보임"
        )

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
            # Policy가 실제로 적용한 감점. preliminary_deduction은 Vision 단계의 예비값이라
            # 그룹 산정(마모 부위 합산)·Cap·오탐 제외를 전혀 반영하지 않는다 - 보증서에
            # 실제와 다른 감점이 찍히던 원인.
            "deduction": d.get("applied_deduction", d.get("preliminary_deduction") or 0),
            "deduction_scope": d.get("deduction_scope"),
            "ratio": d.get("ratio"),
            "text_overlap": d.get("text_overlap"),
        }
        for d in defects
    ]

    # 증거 대조가 판독을 전건 기각한 건은 "무결점"이 아니라 "미확정"이다. 이 사실을 알려
    # 주지 않으면 결함 목록이 비어 보이므로 LLM이 규칙 1을 적용해 "트집 잡을 곳이 없었다"고
    # 쓴다 (실측: LPN-260806-A001 마모 5건 전건 제외 → "결함이 전혀 없는 상태" 보증서 발행).
    unverified = bool(state.get("score_unverified"))
    unverified_block = (
        "\n[중요 - 판정 미확정]\n"
        f"이 도서는 결함 {len(defects)}건이 보고됐으나 증거 대조 검증이 전건을 오탐으로 "
        "지목해 점수의 근거가 확정되지 않았습니다. **무결점·최상급이라는 표현을 절대 쓰지 "
        "말고**, 관리자 확인이 진행 중이라는 사실을 담담하게 밝히세요. 아래 규칙 1(결함 없음 "
        "문구)은 이 경우 적용하지 않습니다.\n"
        if unverified else ""
    )

    prompt = f"""당신은 중고도서 품질 보증서를 쓰는 카피라이터 겸 검수 기록관입니다.
아래 AI 검수 결과를 바탕으로, 실제 구매 고객이 QR로 열어보는 보증서 본문을 작성하세요.

[검수 결과]
- 도서명: {book_title}
- UBCI 최종 점수: {ubci_score}점 ({grade_str})
- 검출 결함 목록(JSON): {json.dumps(defect_brief, ensure_ascii=False)}
- 정성적 특이사항: {special_notes or "없음"}
{unverified_block}
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
