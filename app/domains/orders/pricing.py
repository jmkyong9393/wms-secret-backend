from typing import Dict, Any

# 카테고리별 매입 방어율 (Category Base Rate)
CATEGORY_BASE_RATE = {
    "IT": 0.55,
    "Textbook": 0.55,
    "Self-help": 0.45,
    "Economy": 0.45,
    "Novel": 0.40,
    "Essay": 0.40,
    "Children": 0.40,
    "Comic": 0.30,
    "Magazine": 0.30
}

def calculate_b2b_price(list_price: float, category: str, ubci_score: float) -> float:
    """
    정가(List Price), 카테고리, UBCI(품질 점수)를 바탕으로 최종 B2B 매입가를 계산합니다.
    (Bms_Platform_4대알고리즘 명세서 기준)
    """
    # 매핑되지 않은 카테고리는 보수적으로 35% 적용
    base_rate = CATEGORY_BASE_RATE.get(category, 0.35)
    
    # Final Price = (List Price * Category Base Rate) * (UBCI Score / 100)
    final_price = (list_price * base_rate) * (ubci_score / 100)
    return round(final_price, -1) # 10원 단위 반올림

def calculate_dynamic_discount_rate(ubci_score: float, days_in_inventory: int, category: str) -> float:
    """
    악성 재고 방어 및 판매 확률을 극대화하기 위한 동적 할인율(0.0 ~ 0.9)을 계산합니다.
    (도서 특성상 유행을 타는 장르에 한해서만 장기 체류 페널티를 강하게 부과합니다.)
    """
    base_discount = 0.05 # 기본 5% 할인
    
    # 유행 민감 카테고리 정의
    trend_sensitive_categories = ["Comic", "Novel", "Magazine", "Children"]
    
    # 1. 장기 재고 타겟팅 로직 (조장님 피드백: 365일 기준 최대 10%까지만 할인 적용)
    if category in trend_sensitive_categories:
        # 유행 장르: 1년(365일) 보관 시 최대 10%(0.10) 할인. 그 이상 보관해도 10% 캡(cap) 유지
        time_discount = min((days_in_inventory / 365) * 0.10, 0.10)
        base_discount += time_discount
    else:
        # 타임리스 장르: 조장님 피드백에 따라 시간에 의한 감가(할인) 아예 없음 (0%)
        pass
            
    # 2. 품질(UBCI) 기반 할인 (상태가 안 좋을수록 할인 폭 증가)
    if ubci_score < 70:
        base_discount += 0.20 # FAIR, POOR 등급
    elif ubci_score < 85:
        base_discount += 0.10 # NORMAL 등급
        
    # 할인율을 5% ~ 85% 사이로 제한 (안전 마진 캡)
    final_discount = max(0.05, min(0.85, base_discount))
    return round(final_discount, 2)
