"""
UBCI 감점 매트릭스 SSOT (Single Source of Truth)

[이 파일이 존재하는 이유]
감점 수치는 원래 policy_agent 함수 안에 리터럴로 흩어져 있었다. 그 숫자들은 사람이
`docs/ai_knowledge_base/WMS_표준_운영_정책서.md` 제4조⑤에서 옮겨 적은 값인데, 옮겨 적은
뒤로는 두 곳이 각자 갱신되며 조용히 벌어졌다(오염·황변에서 실제로 어긋났다).
정책서를 고쳐도 코드가 안 바뀌고, 코드를 고쳐도 정책서가 안 바뀌는 상태였다.

수치를 이 파일 하나로 모으고 각 항목에 근거 조항(`clause`)을 붙여, 화면에 출력되는
감점 사유가 정책서 조항을 그대로 가리키게 한다.

[RAG 인용과의 차이 - 중요]
이 조항 라벨은 **결정론적**이다. 벡터 검색 결과가 아니라 코드에 고정된 매핑이므로
RAG 서버가 죽어도 근거 표기가 사라지지 않고, 실행할 때마다 같은 조항이 나온다.
RAG(`rag_service.cite_deduction_basis`)는 조문 **전문 발췌**를 덧붙이는 보조 역할이다.

[이 파일이 정하지 않는 것]
반품 수용 여부·배송비 부담·귀책 판단은 여기 없다. 그것들은 조건이 여럿이고 플랫폼마다
달라 매트릭스로 환원되지 않는다. Policy Agent Stage B(LLM+RAG)가 담당한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# 정책서 제4조⑤ 심각도 구간 — Minor(<5%) / Moderate(5~15%) / Severe(>=15%)
MINOR_MAX_RATIO = 5.0
MODERATE_MAX_RATIO = 15.0

# 정책서 제4조⑤ "텍스트 침범 가중치": BBox가 제목·본문 글자 영역을 침범하면 1.5배.
TEXT_OVERLAP_MULTIPLIER = 1.5


@dataclass(frozen=True)
class DeductionRule:
    """
    결함 1종의 감점 규칙.

    면적 3구간(minor/moderate/severe)을 쓰거나, 면적과 무관한 고정값(flat)을 쓴다.
    둘을 섞어 쓰지 않는다 - 같은 결함을 두 기준으로 재면 어느 쪽이 적용됐는지
    화면에서 읽을 수 없다.
    """
    label: str
    clause: str
    minor: Optional[int] = None
    moderate: Optional[int] = None
    severe: Optional[int] = None
    flat: Optional[int] = None
    # 면적/강도 구간에서 이미 심각도를 반영한 항목은 텍스트 침범 가중치를 타지 않는다.
    # (이중 가중이 되어 -20 x 1.5 = -30 같은 값이 나온다)
    text_overlap_weighted: bool = True

    def tier_for(self, ratio: float) -> int:
        """면적 비율(%)에 해당하는 감점을 돌려준다."""
        if self.flat is not None:
            return self.flat
        if ratio < MINOR_MAX_RATIO:
            return self.minor or 0
        if ratio < MODERATE_MAX_RATIO:
            return self.moderate or 0
        return self.severe or 0


# ─────────────────────────────────────────────────────────────
# 면적 구간 기반 감점 (정책서 제4조⑤ 2D 감점 매트릭스)
# ─────────────────────────────────────────────────────────────
SCRATCH = DeductionRule("긁힘/스크래치", "제4조⑤", minor=2, moderate=5, severe=10)
TEAR = DeductionRule("찢어짐", "제4조⑤", minor=5, moderate=10, severe=15)
CRUSH = DeductionRule("찍힘/구겨짐", "제4조⑤", minor=3, moderate=5, severe=10)

# 오염은 정책서 제4조⑤가 "일반 오염/빛바램"으로 황변과 한 행에 묶어 -3/-6/-10을 준다.
# 코드는 둘을 분리하고 오염을 -5/-10/-20으로 더 엄격하게 본다 - 오염 면적 15% 이상은
# 재판매가 사실상 곤란한 수준인데 -10이면 85점(A급)이 유지되어 상품가치를 과대평가한다.
# 조장 결정(2026-08-14): 코드 기준을 정본으로 삼고 정책서를 이에 맞춰 개정한다.
STAIN = DeductionRule(
    "내지 오염/이물질", "제4조⑤", minor=5, moderate=10, severe=20,
    text_overlap_weighted=False,
)

# 황변은 페이지 전면에 나타나 면적비가 항상 ~100%가 된다. 면적으로 재면 세월 먹은
# 정상적인 헌책이 매번 최고 감점을 맞으므로, VLM이 보고한 강도(level 1~3)를 축으로 쓴다.
# L1~L2는 등급에 영향을 주지 않는 수준으로 관대하게 둔다(중고책의 자연 노화).
DISCOLOR_BY_LEVEL = {1: 2, 2: 5, 3: 10}
DISCOLOR = DeductionRule("변색/황변", "제4조⑤", text_overlap_weighted=False)

STICKER = DeductionRule("스티커/가격표 자국", "제4조⑤", minor=2, moderate=3, severe=5)

# 정책서 제4조⑤ 특수 마킹: 책등 갈라짐 미세(-5) / 깊음(-10). 구간이 2단이라
# minor와 moderate를 같은 값으로 두어 "15% 미만이면 -5"를 표현한다.
SPINE_CRACK = DeductionRule("책등 갈라짐", "제4조⑤ 특수 마킹", minor=5, moderate=5, severe=10)

# 정책서 제4조⑤ 특수 마킹: 도서관/장서인 도장은 크기 무관 -15(중징계).
# 정책서 v2.1.0.0 제3조에서 "장서인은 장물 의심으로 즉시 반려하지 않고 감점 처리"로
# 완화됐다(구버전은 REJECT였다).
STAMP = DeductionRule("도서관 장서인/도장", "제4조⑤ 특수 마킹", flat=15)
SIGNATURE = DeductionRule("이름/서명 기재", "제4조⑤ 특수 마킹", flat=10)

# 위 어디에도 걸리지 않는 미분류 결함의 폴백.
DEFAULT = DeductionRule("기타 상태 결함", "제4조⑤", minor=2, moderate=5, severe=8)

# 제본 벌어짐은 면적 15% 미만이면 감점, 이상이면 즉시 반려(아래 REJECT 절 참조).
BINDING_LOOSE = DeductionRule("제본 벌어짐", "제3조 제본 파손", flat=10)


# ─────────────────────────────────────────────────────────────
# 모서리 마모 (정책서 미기재 - 코드에서 신설, 정책서 역반영 대상)
# ─────────────────────────────────────────────────────────────
# 마모는 건수가 아니라 "책의 서로 다른 모서리 몇 곳이 닳았는가"로 센다. 같은 구석이
# 앞표지·뒤표지·책등 컷에 모두 잡혀도 물리적으로는 한 곳이기 때문이다.
#
# 종전에는 상태와 무관하게 -5 단일 고정이라, 살짝 닳은 책과 헤질 정도로 닳은 책이 같은
# 점수를 받았고 모서리 마모만으로는 어떤 경우에도 S급(>=95)을 벗어날 수 없었다(=95점 고정).
# 등급이 매입가를 결정하므로 차등이 필요하다.
EDGE_WEAR = DeductionRule("모서리 마모", "제4조⑤ (내부 신설)", minor=3, moderate=5, severe=10)
EDGE_WEAR_SPREAD_STEP = 2   # 마모 부위가 1곳 늘 때마다 추가 감점
EDGE_WEAR_CAP = 15          # 마모 총 감점 상한


# ─────────────────────────────────────────────────────────────
# 필기/낙서 (정책서 제3조 ↔ 코드 상이 - 조장 결정으로 코드 기준 채택)
# ─────────────────────────────────────────────────────────────
# 정책서 제3조는 "일반 도서 5페이지 초과" / "문제집 1문항 이상"을 REJECT로 규정한다.
# 코드는 둘 다 감점으로 처리한다. 중고 매입(BUYBACK)은 낙서가 있어도 감가해서 매입하는
# 것이 사업 모델이므로 일괄 반려하면 매입 물량이 성립하지 않기 때문이다.
# 조장 결정(2026-08-14): 검수 유형별로 분기한다(RETURN=반려, BUYBACK=감점).
DOODLE_PAGE_THRESHOLD = 5
DOODLE = DeductionRule("필기/낙서", "제3조 사용 흔적", minor=10, moderate=10, severe=15)

# 수험서·문제집은 필기가 본질적으로 여러 페이지에 퍼지므로 건별 합산하면 순식간에
# REJECT로 떨어진다(실측: "쉽게 풀어쓴 C언어 Express" 손글씨 12건이 건당 -10점씩 누적되어
# 40점까지 하락). 도서 전체에 대해 단일 Cap을 적용한다.
WORKBOOK_DOODLE_CAP = 15
WORKBOOK_DOODLE = DeductionRule(
    "수험서/문제집 전체 필기", "제3조 문제집 사용 흔적", flat=WORKBOOK_DOODLE_CAP,
    text_overlap_weighted=False,
)


# ─────────────────────────────────────────────────────────────
# 즉시 반려 (정책서 제3조 REJECT 하드 리미트)
# ─────────────────────────────────────────────────────────────
# 물젖음은 곰팡이로 번져 인접 재고까지 오염시키므로, 창고 리스크가 개별 도서의 손실보다
# 크다. 정책서 제3조는 "내지 2페이지 이상"을 기준으로 두지만 코드는 감지 즉시 반려한다.
# 조장 결정(2026-08-14): 코드 기준을 정본으로 삼고 정책서를 개정한다.
# (현재 VLM이 젖은 페이지 수를 신뢰성 있게 세지 못해 2페이지 기준을 적용할 근거가 없다)
REJECT_CLAUSE_WET = "제3조 물젖음"
REJECT_CLAUSE_BINDING = "제3조 제본 파손"
BINDING_FATAL_RATIO = 15.0   # 제본 벌어짐이 이 면적을 넘으면 즉시 반려


# ─────────────────────────────────────────────────────────────
# 등급 산정 (정책서 제1조 최종 액션 우선순위)
# ─────────────────────────────────────────────────────────────
BASE_SCORE = 100
GRADE_THRESHOLDS = (
    (95, "S급 (MINT)"),
    (85, "A급 (GOOD)"),
    (65, "B급 (NORMAL)"),
)
REJECT_GRADE = "REJECT C급 (폐기)"
APPROVE_MIN_SCORE = 65


def grade_for(score: int) -> str:
    """점수를 등급 문자열로 변환한다."""
    for threshold, name in GRADE_THRESHOLDS:
        if score >= threshold:
            return name
    return REJECT_GRADE


def decision_for(score: int) -> str:
    return "APPROVE" if score >= APPROVE_MIN_SCORE else "REJECT"
