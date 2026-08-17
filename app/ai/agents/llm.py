"""
LLM 인스턴스.

[프리즈 규정] Vision Agent = GPT-4o 고정, 나머지 = GPT-4o-mini 고정.
모델 배정을 바꾸려면 .claude/rules/01-freeze-zones.md의 예외 절차를 따른다.
"""
from langchain_openai import ChatOpenAI

# 토큰 계측 콜백. 모델·temperature는 건드리지 않는다 (프리즈 규정: 모델 배정 고정).
# 콜백은 어느 노드에서 부른 호출인지 contextvar로 판별해 노드별로 적재한다.
try:
    from app.ai.instrumentation import token_collector
    _CB = [token_collector]
except Exception:
    _CB = []

# LLM 인스턴스 생성 (Vision Agent = GPT-4o 고정, 나머지 = GPT-4o-mini 고정)
try:
    llm_vlm = ChatOpenAI(model="gpt-4o", temperature=0.0, callbacks=_CB)
    llm_mini = ChatOpenAI(model="gpt-4o-mini", temperature=0.0, callbacks=_CB)
    # 증거 대조 검증 전용. 4o-mini는 판독 타당성 심사에서 확정 결함 전건을 오탐으로 반려하는 등 신뢰할 수 없는 결과를 냈다.
    # 결함이 1건 이상일 때만 도는 경로라 MINT 물량에는 추가 비용이 없다.
    llm_verify = ChatOpenAI(model="gpt-4o", temperature=0.0, callbacks=_CB)
except Exception as _e:
    # 종전에는 예외를 조용히 삼켜, 생성자 인자 하나가 잘못돼도 전 LLM이 None이 된 채
    # 기동됐다(계측 콜백 추가 시 실제로 발생). 최소한 흔적은 남긴다.
    import logging as _logging
    _logging.getLogger(__name__).error(
        f"[LLM 초기화 실패] {type(_e).__name__}: {_e} — 전 노드의 LLM 호출이 비활성화된다")
    llm_vlm = None
    llm_mini = None
    llm_verify = None

# 촬영 규격상 Track 1(WBF 앙상블 담당)이 맡는 앞쪽 이미지 장수
