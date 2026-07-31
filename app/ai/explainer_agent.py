import os
from typing import Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

class ExplainerAgent:
    """
    [Multi-Agent LLMOps 아키텍처] Explainer Agent
    Vision (비전 결함), Policy (WMS 규정/UBCI), Critic (교차 검증) 3개 에이전트의 산출 데이터를 종합하여,
    현장 작업자와 B2B 거래처가 납득할 수 있는 1문장 전문 종합 검수 소견서를 GPT-4o-mini로 실시간 생성합니다.
    """
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            self.llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0, openai_api_key=api_key)
        else:
            self.llm = None

        self.prompt = ChatPromptTemplate.from_messages([
            ("system", """
            당신은 중고 도서 WMS 물류센터의 최종 정산 승인 기관인 'Explainer Agent'입니다.
            Vision, Policy, Critic 에이전트가 수집한 검수 파이프라인 데이터를 바탕으로, 현장 작업자와 B2B 도매 고객이 즉시 이해할 수 있는 전문적인 1문장 종합 검수 소견서를 작성하세요.

            [작성 지침]
            1. 격식 있고 명확한 물류 표준 톤앤매너를 유지할 것.
            2. 감점 항목(낙서, 젖음, 오염, 모서리 손상 등)이 있다면 감점 원인과 등급 타당성을 언급할 것.
            3. 점수(UBCI)와 등급(MINT/GOOD/NORMAL/REJECT)의 타당성을 입증하는 1문장 요약 소견을 반환할 것.
            4. 불필요한 서론/결론 없이 1문장 소견서만 직접 출력할 것.
            """),
            ("user", """
            - 도서 제목: {title}
            - LPN 바코드: {lpn}
            - Vision Agent 감지 결함: {defect_description}
            - Policy Agent UBCI 점수: {ubci_score}점 ({grade}급)
            - Critic Agent 교차 검증: {critic_status} (신뢰도 {confidence}%)
            """)
        ])

    def generate_explanation(
        self,
        title: str,
        lpn: str,
        defect_description: str,
        ubci_score: int,
        grade: str,
        critic_status: str = "검증 완료",
        confidence: float = 99.2
    ) -> str:
        """
        OpenAI API가 설정되어 있으면 GPT-4o-mini로 실시간 소견서를 생성하고,
        API 키 미설정 시에는 규격화된 고품질 물류 소견 템플릿을 안전하게 반환합니다.
        """
        if self.llm:
            try:
                chain = self.prompt | self.llm
                res = chain.invoke({
                    "title": title or "도서",
                    "lpn": lpn or "LPN-UNKNOWN",
                    "defect_description": defect_description or "MINT 깨끗함",
                    "ubci_score": ubci_score,
                    "grade": grade or "GOOD",
                    "critic_status": critic_status,
                    "confidence": confidence
                })
                return res.content.strip()
            except Exception as e:
                print(f"[ExplainerAgent] OpenAI LLM 호출 실패 fallback 적용: {e}")

        # Fallback 템플릿 (OpenAI 키가 없거나 네트워크 오류 시 안전 방어)
        if defect_description and defect_description != "MINT" and defect_description != "정상":
            return f"Explainer Agent 소견: [{title}] 도서의 {defect_description} 감점 요인이 반영되어, UBCI {ubci_score}점 ({grade}급) 입고 정산이 최종 승인되었습니다."
        else:
            return f"Explainer Agent 소견: [{title}] 도서의 결함이 감지되지 않은 최상급 상태로, UBCI {ubci_score}점 ({grade}급) 입고 정산이 최종 승인되었습니다."
