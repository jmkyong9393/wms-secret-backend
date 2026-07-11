import base64
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

class DefectReport(BaseModel):
    has_defect: bool = Field(description="훼손 여부 존재 (True/False)")
    defect_type: str = Field(description="훼손 유형 (예: 찢어짐, 얼룩, 구겨짐, 깨끗함)")
    defect_description: str = Field(description="훼손의 상세한 위치 및 심각도에 대한 묘사")

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

    def analyze_image(self, image_path: str) -> DefectReport:
        """Base64 이미지를 GPT-4o에 전달하여 훼손 부위를 제로샷으로 추론합니다."""
        base64_image = self._encode_image(image_path)
        
        prompt = """당신은 'B2B 도서 물류 자동화 플랫폼'의 정밀 도서 검수 비전 AI입니다.
주어진 도서 이미지를 분석하여 다음 사항을 파악하세요:
1. 책 표지나 내지에 찢어짐, 얼룩, 구겨짐, 변색 등의 훼손이 있는지 확인하세요.
2. 훼손이 있다면 그 위치와 크기(예: 모서리 2cm 찢어짐, 중앙에 500원 동전 크기 커피 얼룩 등)를 최대한 상세히 묘사하세요.
3. 훼손이 전혀 없다면 완전히 깨끗하다고 보고하세요.
"""
        
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
            ]
        )
        
        # API 호출 및 정형화된 JSON 반환
        response = self.structured_llm.invoke([message])
        return response

if __name__ == "__main__":
    # 테스트 구문 (실행을 위해선 OPENAI_API_KEY 환경변수가 필요합니다)
    # agent = VisionAgent()
    # print(agent.analyze_image("../../data/sample_images/raw_inputs/sample1.jpg"))
    pass
