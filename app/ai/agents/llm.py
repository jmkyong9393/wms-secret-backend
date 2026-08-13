"""
LLM 인스턴스.

[프리즈 규정] Vision Agent = GPT-4o 고정, 나머지 = GPT-4o-mini 고정.
모델 배정을 바꾸려면 .claude/rules/01-freeze-zones.md의 예외 절차를 따른다.
"""
from langchain_openai import ChatOpenAI

# LLM 인스턴스 생성 (Vision Agent = GPT-4o 고정, 나머지 = GPT-4o-mini 고정)
try:
    llm_vlm = ChatOpenAI(model="gpt-4o", temperature=0.0)
    llm_mini = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
    # 증거 대조 검증 전용. 4o-mini는 판독 타당성 심사에서 확정 결함 전건을 오탐으로 반려하는 등 신뢰할 수 없는 결과를 냈다.
    # 결함이 1건 이상일 때만 도는 경로라 MINT 물량에는 추가 비용이 없다.
    llm_verify = ChatOpenAI(model="gpt-4o", temperature=0.0)
except Exception:
    llm_vlm = None
    llm_mini = None
    llm_verify = None

# 촬영 규격상 Track 1(WBF 앙상블 담당)이 맡는 앞쪽 이미지 장수
