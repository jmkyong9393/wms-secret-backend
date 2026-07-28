import base64
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from typing import List, Optional
import json

class BoundingBox(BaseModel):
    ymin: int = Field(description="결함 박스의 상단 Y 좌표 (0~1000)")
    xmin: int = Field(description="결함 박스의 좌측 X 좌표 (0~1000)")
    ymax: int = Field(description="결함 박스의 하단 Y 좌표 (0~1000)")
    xmax: int = Field(description="결함 박스의 우측 X 좌표 (0~1000)")

class DefectDetail(BaseModel):
    defect_type: str = Field(description="결함 유형 (예: 표지 스크래치, 찢어짐, 책등 갈라짐, 습기 휨, 액체 얼룩, 장서인 등)")
    severity: str = Field(description="심각도 분류 (Minor <5%, Moderate 5~15%, Severe >=15% 또는 형태학 임계치)")
    area_percent: float = Field(description="결함이 표지/측면 전체 면적에서 차지하는 비율(%)")
    is_text_overlapped: bool = Field(description="결함이 도서의 제목이나 본문 텍스트 영역을 침범했는지 여부")
    morphology_note: Optional[str] = Field(description="형태학적 휨(곡률)이나 외곽선 단절이 있는 경우 상세 묘사")
    expected_deduction: int = Field(description="UBCI 룰에 따른 예상 감점 수치 (예: -5, -10)")
    bbox: BoundingBox = Field(description="해당 결함의 2D Bounding Box 좌표 (JSONB용)")
    image_index: int = Field(description="결함이 발견된 원본 이미지의 인덱스 (0번째=정면, 1번째=후면, 2번째~=내지/측면 등)", default=0)

class DefectReport(BaseModel):
    paper_flatness_analysis: str = Field(description="[필수 작성] 종이의 물리적 평탄도 분석. 불규칙한 빛 반사, 그림자, 표면 굴곡이 있는지, 가장자리나 내지가 쭈글쭈글한지(습기/물먹음 훼손) 여부를 최우선으로 자세히 묘사하세요.")
    is_clean: bool = Field(description="위 분석을 바탕으로 훼손이 없는 완벽한 MINT 상태인지 여부")
    instant_reject_reason: Optional[str] = Field(description="심한 오염, 습기/물먹음 훼손(쭈글쭈글함) 등 즉시 반려 사유 존재 시 기재")
    defects: List[DefectDetail] = Field(default=[], description="발견된 개별 결함들의 상세 정보 배열 (JSONB 탑재용)")
    estimated_final_ubci: int = Field(description="최종 UBCI 점수 (기본 100점에서 각 결함 차감 합계)")

class VisionAgent:
    def __init__(self):
        # 1차 비전 검수 모델 (gpt-4o)
        self.vision_llm = ChatOpenAI(model="gpt-4o", temperature=0.1)
        self.structured_vision_llm = self.vision_llm.with_structured_output(DefectReport)
        
        # 2차 수학 연산 교차 검증 모델 (gpt-4o-mini)
        self.math_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
        self.structured_math_llm = self.math_llm.with_structured_output(DefectReport)

    def _encode_image(self, image_path: str) -> str:
        """로컬 이미지를 Base64 문자열로 변환합니다."""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def analyze_images(self, image_paths: list[str]) -> DefectReport:
        """2-Tier 구조: gpt-4o로 이미지 분석 후, gpt-4o-mini로 점수 연산 교차 검증"""
        
        # 1. 1차 비전 검수 (gpt-4o)
        base64_images = [self._encode_image(path) for path in image_paths]
        
        vision_prompt = """당신은 'B2B 도서 물류 자동화 플랫폼'의 초정밀 도서 검수 비전 AI (UBCI 심사관)입니다.
당신에게는 도서의 정면, 후면, 측면, 내지 등 여러 장의 사진이 제공됩니다.
아래의 [UBCI v2.1 2D 감점 매트릭스] 규칙에 따라 결함을 찾아내고 점수를 산정하세요.

[UBCI v2.1 2D 감점 매트릭스 룰]
기본 점수는 100점입니다. 심각도는 결함 면적 기준입니다 (Minor <5%, Moderate 5~15%, Severe >=15%).
- 긁힘/스크래치: Minor(-2점), Moderate(-5점), Severe(-10점)
- 찢어짐(Tear): Minor(-5점), Moderate(-10점), Severe(-15점)
- 찍힘/구겨짐: Minor(-3점), Moderate(-5점), Severe(-10점)
- 책등 갈라짐(단절): 미세(-5점), 깊음(-10점)
- 일반 오염/빛바램: Minor(-3점), Moderate(-6점), Severe(-10점)
- 도서관/장서인 도장: 크기 무관 -15점 (중징계)
- 필기/낙서/밑줄(Handwriting/Scribble): Minor(-10점), Moderate(-20점), Severe(즉시 반려)
- 🚨 액체 얼룩(Water): 즉시 반려 (instant_reject_reason 작성)
- 🚨 습기 휨(Page Warping): 즉시 반려 (instant_reject_reason 작성)

[텍스트 침범 가중치]
- 발견된 결함이 도서의 텍스트(제목, 본문 글자) 영역을 가리거나 침범했다면 해당 감점 점수에 1.5배를 곱하여 차감하세요.

출력 지시사항:
1. [CoT 필수] 먼저 `paper_flatness_analysis` 필드에 책의 평탄도, 종이의 물결침, 그림자 굴곡 여부를 시각적으로 분석해 상세히 적으세요. 특히 물먹음(Page Warping)이 없는지 꼼꼼히 체크하세요.
2. 각 결함의 정확한 BBox 좌표를 ymin, xmin, ymax, xmax (0~1000 정규화된 스케일) 포맷으로 기록하고 형태학적 특징(morphology_note)을 기록하세요. GPT-4o의 Visual Grounding 능력을 최대한 활용하세요.
3. 매트릭스에 기반한 예상 감점(expected_deduction)을 명시하세요.
4. 바코드(LPN 스티커)는 절대 오염으로 간주하지 마세요.
5. [매우 중요] 도서 내지나 겉면에 원래 인쇄된 글자가 아닌, 사용자가 펜이나 연필로 쓴 '필기/낙서/밑줄'이 있는지 극도로 주의해서 찾아내세요. AI는 종종 인쇄된 글자와 필기를 혼동하므로, 색상(파란펜, 빨간펜 등)이나 불규칙한 선형태를 꼼꼼히 살피세요.
6. [매우 중요] 결함이 발견된 이미지가 몇 번째 이미지인지 `image_index` (0번째부터 시작)를 정확히 기재하세요.
7. [매우 중요/강제 지시] GPT-4o는 책의 측면이나 정면 모서리의 미세한 '물결침(쭈글쭈글함, 우는 현상)'을 정상적인 그림자로 오인하여 놓치는 경향이 있습니다. 종이 모서리나 표면에 불규칙한 빛 반사, 굴곡, 음영이 단 1%라도 보인다면 즉시 `is_clean=False`로 판정하고, `defect_type`을 `습기 휨` 또는 `액체 얼룩(Water)`으로 분류하여 0점(반려) 처리하세요.
8. [가장자리 집중 관찰] 도서의 바닥면, 측면 엣지(Edge) 부분을 픽셀 단위로 스캔하여 일직선이 아니거나 물결(Wave) 형태를 띤다면 100% 수분 훼손입니다.
9. 모든 사진이 완벽할 때만 is_clean=True 를 반환하세요."""
        content_list = [{"type": "text", "text": vision_prompt}]
        for b64 in base64_images:
            content_list.append({
                "type": "image_url", 
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })

        vision_message = HumanMessage(content=content_list)
        draft_report = self.structured_vision_llm.invoke([vision_message])
        
        if not draft_report:
            draft_report = DefectReport(is_clean=True, estimated_final_ubci=100)
            
        draft_json = draft_report.model_dump_json(indent=2) if hasattr(draft_report, 'model_dump_json') else draft_report.json(indent=2)
        
        # 2. 2차 검증 (gpt-4o-mini) - 수학 연산 및 로직 무결성 검증
        verification_prompt = f"""당신은 UBCI 감점 점수 연산 검증 AI입니다. 
앞선 1차 비전 AI가 작성한 도서 결함 리포트를 전달받았습니다. 
당신의 임무는 1차 AI가 매트릭스 룰과 수학 공식(1.5배 가중치, 합산 등)을 올바르게 적용했는지 교차 검증하고, 
틀린 계산이 있다면 정정하여 최종 리포트를 반환하는 것입니다.

[UBCI v2.1 2D 감점 매트릭스 룰]
기본 점수는 100점입니다. 심각도는 결함 면적 기준입니다 (Minor <5%, Moderate 5~15%, Severe >=15%).
- 긁힘/스크래치: Minor(-2점), Moderate(-5점), Severe(-10점)
- 찢어짐(Tear): Minor(-5점), Moderate(-10점), Severe(-15점)
- 찍힘/구겨짐: Minor(-3점), Moderate(-5점), Severe(-10점)
- 책등 갈라짐(단절): 미세(-5점), 깊음(-10점)
- 일반 오염/빛바램: Minor(-3점), Moderate(-6점), Severe(-10점)
- 도서관/장서인 도장: 크기 무관 -15점 (중징계)
- 필기/낙서/밑줄(Handwriting/Scribble): Minor(-10점), Moderate(-20점), Severe(즉시 반려)
- 🚨 액체 얼룩(Water): 즉시 반려 (instant_reject_reason 작성)
- 🚨 습기 휨(Page Warping): 즉시 반려 (instant_reject_reason 작성)
[텍스트 침범 가중치] 텍스트 영역 침범 시 감점 점수에 1.5배를 곱하여 차감. (소수점 발생 시 반올림)

1차 AI 리포트 데이터:
{draft_json}

검증 지시사항:
1. 각 결함별 'severity'와 'is_text_overlapped'를 보고 'expected_deduction' 연산이 정확한지 검사하세요. 오류가 있다면 정정하세요.
2. 100점에서 모든 expected_deduction을 합산하여 'estimated_final_ubci'가 맞는지 확인하고 정정하세요.
3. 수학적 정정 외에 다른 묘사나 BBox 좌표는 1차 AI의 데이터를 그대로 유지하세요.
"""
        math_message = HumanMessage(content=verification_prompt)
        final_verified_report = self.structured_math_llm.invoke([math_message])
        
        if not final_verified_report:
            return draft_report
            
        return final_verified_report

if __name__ == "__main__":
    pass
