"""
중고 도서 가격 산정 엔진 (Dynamic Pricing)

용어 구분 (혼동 주의):
  - B2B 매입가 : 우리가 판매자/협력사에게 지급하는 금액. calculate_b2b_price()
  - 중고 판매가 : 우리가 최종 고객에게 파는 금액. calculate_used_retail_price()
    (고객 보증서 화면에 노출되는 값이며, 당연히 매입가보다 높다)
"""
from typing import Any, Dict, Optional

# ==========================================
# 카테고리 정규화
# ==========================================
#
# [수정 이력] 요율 사전의 키는 영문("IT", "Novel")인데 books.category_type에 실제로 적재되는
# 값은 알라딘 API가 주는 한글 분류("컴퓨터/모바일", "대학교재/전문서적")와 "GENERAL"이
# 대부분이었다. 그 결과 거의 모든 도서가 .get(category, 기본값) 폴백으로 떨어져
# **카테고리별 차등이 사실상 한 번도 적용되지 않았다.**
# 한글/영문/변형 표기를 내부 표준 키로 모으는 정규화 계층을 둔다.
_CATEGORY_ALIASES = {
    "IT": [
        "it", "컴퓨터", "컴퓨터/모바일", "컴퓨터/it", "it/컴퓨터", "모바일",
        "프로그래밍", "그래픽/멀티미디어", "네트워크", "os/데이터베이스",
    ],
    "Textbook": [
        "textbook", "교재", "대학교재", "대학교재/전문서적", "수험서", "수험서/자격증",
        "고등학교참고서", "중학교참고서", "초등학교참고서", "외국어", "자격증",
    ],
    "Economy": ["economy", "경제경영", "경제/경영", "경영", "재테크", "투자"],
    "Self-help": ["self-help", "자기계발", "인문학", "인문", "심리", "철학"],
    "Novel": ["novel", "소설", "소설/시/희곡", "문학", "시", "희곡", "장르소설"],
    "Essay": ["essay", "에세이", "산문", "여행"],
    "Children": ["children", "어린이", "유아", "청소년", "아동"],
    "Comic": ["comic", "만화", "만화/웹툰", "웹툰", "라이트노벨"],
    "Magazine": ["magazine", "잡지", "월간지", "정기간행물"],
}

# 역인덱스: 별칭 -> 표준 키
_ALIAS_TO_CANONICAL = {
    alias: canonical
    for canonical, aliases in _CATEGORY_ALIASES.items()
    for alias in aliases
}

DEFAULT_CATEGORY = "General"


def normalize_category(raw: Optional[str]) -> str:
    """
    DB에 적재된 자유 형식 카테고리를 요율표의 표준 키로 정규화한다.

    매칭 순서:
      1) 정확히 일치 (소문자 비교)
      2) 부분 포함 (예: "국내도서>컴퓨터/모바일>그래픽" 처럼 경로가 섞여 들어온 경우)
      3) 실패 시 General

    [주의] category_type에 도서 제목이 통째로 들어간 오염 행이 실제로 존재한다
    (인바060 카테고리 파싱 실패 잔재). 그런 값은 어떤 별칭에도 걸리지 않으므로
    자연히 General로 떨어져 보수적 요율이 적용된다.
    """
    if not raw:
        return DEFAULT_CATEGORY

    key = str(raw).strip().lower()
    if not key:
        return DEFAULT_CATEGORY

    if key in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[key]

    # 알라딘 categoryName이 통째로 들어온 경우("국내도서>컴퓨터/모바일>...") 등을 위한 부분 매칭.
    # 긴 별칭부터 검사해 "컴퓨터/모바일"이 "컴퓨터"보다 먼저 매칭되게 한다.
    for alias in sorted(_ALIAS_TO_CANONICAL, key=len, reverse=True):
        if alias in key:
            return _ALIAS_TO_CANONICAL[alias]

    return DEFAULT_CATEGORY


# ==========================================
# 요율표
# ==========================================

# B2B 매입 방어율: 정가 대비 우리가 지급할 수 있는 상한 비율
CATEGORY_BASE_RATE: Dict[str, float] = {
    "IT": 0.55,
    "Textbook": 0.55,
    "Self-help": 0.45,
    "Economy": 0.45,
    "Novel": 0.40,
    "Essay": 0.40,
    "Children": 0.40,
    "Comic": 0.30,
    "Magazine": 0.30,
    "General": 0.35,
}

# 중고 판매가율: 무결점(UBCI 100) 기준으로 정가 대비 몇 %에 판매하는가.
# 재판매 수요가 높고 개정 주기가 긴 카테고리일수록 높게 잡는다.
# [핵심] 100점(MINT)이어도 신품 정가와 같아질 수 없다 - 중고라는 사실 자체가 감가 요인이다.
USED_RETAIL_BASE_RATE: Dict[str, float] = {
    "IT": 0.70,
    "Textbook": 0.72,
    "Self-help": 0.62,
    "Economy": 0.62,
    "Novel": 0.58,
    "Essay": 0.58,
    "Children": 0.55,
    "Comic": 0.48,
    "Magazine": 0.40,
    "General": 0.55,
}

# 유행/개정에 민감해 장기 체류 시 추가 감가를 받는 카테고리
TREND_SENSITIVE_CATEGORIES = ["Comic", "Novel", "Magazine", "Children"]

# 카테고리별 시즌 가중치 (Seasonality Index, 1.0 기준)
# [명세] 08_Dynamic_Pricing §2-② — 교재는 학기/방학 수요 절벽이 크고, IT 서적은 비수기가 없다.
# [수정 이력] 명세서에는 정의되어 있었으나 실제 판매가 산정에는 반영된 적이 없었다.
CATEGORY_SEASONALITY: Dict[str, float] = {
    "Textbook": 1.25,   # 학기 성수기
    "IT": 1.05,         # 상시 고수요
    "Economy": 1.02,
    "Self-help": 1.00,
    "Novel": 1.00,
    "Essay": 1.00,
    "Children": 0.98,
    "Comic": 0.95,
    "Magazine": 0.90,
    "General": 1.00,
}

# 시즌 가중치가 판매가를 무한정 밀어올리지 않도록 반영 강도를 제한한다.
# (index 1.25 -> 실제 가격 반영은 +12.5%)
SEASONALITY_INFLUENCE = 0.5

# 도서 설명(NLP)에서 희소성 신호를 찾아 붙이는 프리미엄.
# [명세] 08_Dynamic_Pricing §3 `description_premium` (절판/한정판 등 +5~10%)
DESCRIPTION_PREMIUM_KEYWORDS = {
    "절판": 0.10,
    "한정판": 0.10,
    "초판": 0.07,
    "개정판": 0.05,
    "리커버": 0.05,
    "양장": 0.03,
}
MAX_DESCRIPTION_PREMIUM = 0.15  # 프리미엄 상한 (+15%)


def calculate_description_premium(description: Optional[str], title: Optional[str] = None) -> float:
    """
    도서 설명/제목에서 희소성 키워드를 찾아 프리미엄 계수(0.0 ~ 0.15)를 산출한다.
    키워드 기반 결정론적 연산이며 LLM을 쓰지 않는다(같은 도서는 항상 같은 값).
    """
    text = f"{title or ''} {description or ''}"
    if not text.strip():
        return 0.0

    premium = 0.0
    for keyword, weight in DESCRIPTION_PREMIUM_KEYWORDS.items():
        if keyword in text:
            premium += weight

    return round(min(premium, MAX_DESCRIPTION_PREMIUM), 4)


def _condition_factor(ubci_score: float) -> float:
    """
    상태 보정 계수. UBCI 100점 -> 1.0, 0점 -> 0.60.

    UBCI를 그대로 비율로 쓰면(score/100) 저품질 도서가 지나치게 헐값이 되어
    재판매 자체가 성립하지 않는다. 하한 0.60을 두어 완만하게 감가한다.
    """
    score = max(0.0, min(100.0, float(ubci_score if ubci_score is not None else 85)))
    return 0.60 + 0.40 * (score / 100.0)


def calculate_b2b_price(list_price: float, category: str, ubci_score: float) -> float:
    """
    정가·카테고리·UBCI를 바탕으로 B2B 매입가(우리가 지급하는 금액)를 계산한다.
    """
    canonical = normalize_category(category)
    base_rate = CATEGORY_BASE_RATE.get(canonical, CATEGORY_BASE_RATE["General"])
    score = float(ubci_score if ubci_score is not None else 85)

    final_price = (float(list_price or 0.0) * base_rate) * (score / 100.0)
    return round(final_price, -1)  # 10원 단위 반올림


def calculate_used_retail_price(
    list_price: float,
    category: str,
    ubci_score: float,
    days_in_inventory: int = 0,
    description: Optional[str] = None,
    title: Optional[str] = None,
) -> float:
    """
    고객에게 노출되는 중고 판매가를 계산한다 (보증서 화면의 "중고 판매가").

        Price = 정가
              × 카테고리 판매가율        (재판매 수요)
              × 상태 보정                (UBCI)
              × 시즌 가중치              (Seasonality Index, 반영 강도 50%)
              × (1 + 희소성 프리미엄)    (절판/한정판 등, 상한 +15%)
              × (1 - 체류 감가)          (유행 민감 카테고리만, 상한 -10%)

    [명세] 08_Dynamic_Pricing_합성_데이터_생성_명세서 §2, §3
    [수정 이력]
      1) 종전에는 프론트가 `정가 × UBCI/100`으로 직접 계산했다. UBCI 100점(MINT)이면
         보정 계수가 1.0이 되어 중고 판매가가 신품 정가와 완전히 동일하게 표시됐다
         (정가 20,000원 / 중고 판매가 20,000원).
      2) 명세서에 정의된 seasonality_index와 description_premium이 코드 어디에도
         반영되어 있지 않았다. 두 항을 산식에 편입해 명세와 구현을 일치시킨다.

    이 함수는 결정론적이다. 같은 입력이면 항상 같은 가격이 나오며, LLM이나 검색 결과가
    가격에 개입하지 않는다(등급·가격은 매입 정산의 근거이므로 재현성이 필수).
    """
    canonical = normalize_category(category)
    base_rate = USED_RETAIL_BASE_RATE.get(canonical, USED_RETAIL_BASE_RATE["General"])

    price = float(list_price or 0.0) * base_rate * _condition_factor(ubci_score)

    # 시즌 가중치 - 지수를 그대로 곱하면 교재가 +25% 튀므로 반영 강도를 절반으로 눌러 적용
    seasonality = CATEGORY_SEASONALITY.get(canonical, 1.0)
    price *= 1.0 + (seasonality - 1.0) * SEASONALITY_INFLUENCE

    # 희소성 프리미엄 (절판/한정판/초판 등)
    price *= 1.0 + calculate_description_premium(description, title)

    # 유행 민감 카테고리만 장기 체류 감가를 받는다 (도서는 비부패성 자산이므로 상한 10%).
    if canonical in TREND_SENSITIVE_CATEGORIES and days_in_inventory > 0:
        dwell_discount = min(days_in_inventory / 365.0 * 0.10, 0.10)
        price *= (1.0 - dwell_discount)

    # 정가를 넘는 중고가는 어떤 조합에서도 성립하지 않는다 (희소본이어도 정가 상한 유지).
    price = min(price, float(list_price or 0.0))

    return round(price, -2)  # 100원 단위 반올림 (소비자 가격 표기 관행)


def build_pricing_breakdown(
    list_price: float,
    category: str,
    ubci_score: float,
    days_in_inventory: int = 0,
    description: Optional[str] = None,
    title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    화면이 그대로 렌더할 수 있는 가격 산정 내역을 반환한다.
    프론트가 가격을 자체 계산하지 않도록 각 계수와 근거까지 함께 내려준다
    (관리자가 "왜 이 가격인가"를 화면에서 바로 추적할 수 있어야 한다).
    """
    canonical = normalize_category(category)
    retail = calculate_used_retail_price(
        list_price, category, ubci_score, days_in_inventory, description, title
    )
    b2b = calculate_b2b_price(list_price, category, ubci_score)
    list_price = float(list_price or 0.0)

    seasonality = CATEGORY_SEASONALITY.get(canonical, 1.0)
    premium = calculate_description_premium(description, title)
    dwell_discount = (
        min(days_in_inventory / 365.0 * 0.10, 0.10)
        if canonical in TREND_SENSITIVE_CATEGORIES and days_in_inventory > 0
        else 0.0
    )

    return {
        "list_price": round(list_price),
        "used_retail_price": retail,
        "b2b_supply_price": b2b,
        "category_raw": category,
        "category_normalized": canonical,
        "category_retail_rate": USED_RETAIL_BASE_RATE.get(canonical, USED_RETAIL_BASE_RATE["General"]),
        "category_b2b_rate": CATEGORY_BASE_RATE.get(canonical, CATEGORY_BASE_RATE["General"]),
        "condition_factor": round(_condition_factor(ubci_score), 4),
        "seasonality_index": seasonality,
        "seasonality_applied": round(1.0 + (seasonality - 1.0) * SEASONALITY_INFLUENCE, 4),
        "description_premium": premium,
        "days_in_inventory": days_in_inventory,
        "dwell_discount": round(dwell_discount, 4),
        # 정가 대비 총 감가율 (고객 화면에 "정가 대비 -30%"로 표기)
        "discount_rate_vs_list": round(1.0 - (retail / list_price), 4) if list_price > 0 else 0.0,
    }


def calculate_dynamic_discount_rate(ubci_score: float, days_in_inventory: int, category: str) -> float:
    """
    악성 재고 방어 및 판매 확률 극대화를 위한 동적 할인율(0.05 ~ 0.85)을 계산한다.
    도서는 비부패성 자산이므로 유행 민감 장르에 한해서만 체류 페널티를 부과한다.
    """
    canonical = normalize_category(category)
    base_discount = 0.05

    if canonical in TREND_SENSITIVE_CATEGORIES:
        # 유행 장르: 365일 보관 시 최대 10% 할인, 그 이상은 캡 유지
        base_discount += min((days_in_inventory / 365) * 0.10, 0.10)
    # 타임리스 장르: 시간에 의한 감가 없음 (조장 피드백 반영)

    score = float(ubci_score if ubci_score is not None else 85)
    if score < 70:
        base_discount += 0.20   # FAIR / POOR
    elif score < 85:
        base_discount += 0.10   # NORMAL

    return round(max(0.05, min(0.85, base_discount)), 2)
