import base64
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from typing import List, Optional

class BoundingBox(BaseModel):
    x: float = Field(description="결함 박스의 좌측 상단 X 좌표 (0~100 퍼센트)")
    y: float = Field(description="결함 박스의 좌측 상단 Y 좌표 (0~100 퍼센트)")
    width: float = Field(description="결함 박스의 너비 (0~100 퍼센트)")
    height: float = Field(description="결함 박스의 높이 (0~100 퍼센트)")

class DefectReport(BaseModel):
    has_defect: bool = Field(description="훼손 여부 존재 (True/False)")
    defect_type: str = Field(description="훼손 유형 (예: 찢어짐, 얼룩, 구겨짐, 깨끗함)")
    defect_description: str = Field(description="훼손의 상세한 위치 및 심각도에 대한 묘사")
    defect_coordinates: Optional[List[BoundingBox]] = Field(default=[], description="화면에 오버레이로 그릴 결함 박스의 상대 좌표(퍼센트) 목록")

class VisionAgent:
    def __init__(self):
        # 강력한 멀티모달 추론을 위해 gpt-4o 모델을 Vision API로 활용합니다.
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.1)
        # 응답을 항상 Pydantic의 DefectReport 구조(JSON)로 강제 변환하여 후속 에이전트(Policy)가 읽기 쉽게 만듭니다.
        self.structured_llm = self.llm.with_structured_output(DefectReport)

    def _encode_image(self, image_path: str) -> str:
        """로컬 이미지를 Base64 문자열로 변환합니다."""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def analyze_images(self, image_paths: list[str]) -> DefectReport:
        """Base64 다중 이미지(정면, 후면, 내지)를 GPT-4o에 전달하여 훼손 부위를 입체적으로 추론합니다."""
        base64_images = [self._encode_image(path) for path in image_paths]
        
        prompt = """당신은 'B2B 도서 물류 자동화 플랫폼'의 정밀 도서 검수 비전 AI입니다.
당신에게는 1장의 도서 정면, 1장의 도서 후면, 그리고 N장의 훼손 의심 부위(측면/내지) 사진들이 제공됩니다. 
제공된 모든 사진을 종합적으로 분석하여 단일 도서에 대한 상태를 파악하세요:
1. 책 표지나 내지에 찢어짐, 얼룩, 구겨짐, 변색 등의 훼손이 있는지 전체적으로 확인하세요.
2. 훼손이 있다면 그 위치(예: 정면 우측 상단, 후면 모서리, 내지 중앙 등)와 크기를 최대한 상세히 묘사하세요.
3. 프론트엔드 시각화를 위해 bounding box(x, y, width, height)를 퍼센트(%) 단위로 반환하세요.
4. **중요: 도서 표지에 붙어있는 'LPN-XXXXXXXX-XXXX' 형식의 바코드 라벨(흰색 스티커)은 정품/재고 관리를 위한 정상적인 식별표입니다. 절대 오염이나 훼손으로 간주하지 마세요.**
5. 모든 사진을 검토한 결과 훼손이 전혀 없다면 완전히 깨끗하다고 보고하세요.
"""
        
        content_list = [{"type": "text", "text": prompt}]
        for b64 in base64_images:
            content_list.append({
                "type": "image_url", 
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })

        message = HumanMessage(content=content_list)
        
        # API 호출 및 정형화된 JSON 반환
        response = self.structured_llm.invoke([message])
        return response

if __name__ == "__main__":
    # 테스트 구문 (실행을 위해선 OPENAI_API_KEY 환경변수가 필요합니다)
    # agent = VisionAgent()
    # print(agent.analyze_image("../../data/sample_images/raw_inputs/sample1.jpg"))
    pass
