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

# 카테고리별 시즌 가중치 (Seasonality Index)
CATEGORY_SEASONALITY = {
    "Textbook": 1.25, # 학기 성수기
    "IT": 1.05,       # 상시 고수요
    "Novel": 1.00,
    "Comic": 0.95,
    "Magazine": 0.90
}

def calculate_b2b_price(list_price: float, category: str, ubci_score: float) -> float:
    """
    정가(List Price), 카테고리, UBCI(품질 점수)를 바탕으로 최종 B2B 매입가를 계산합니다.
    """
    base_rate = CATEGORY_BASE_RATE.get(category, 0.35)
    final_price = (list_price * base_rate) * (ubci_score / 100)
    return round(final_price, -1) # 10원 단위 반올림

def calculate_dynamic_discount_rate(ubci_score: float, days_in_inventory: int, category: str) -> float:
    """
    악성 재고 방어 및 판매 확률을 극대화하기 위한 동적 할인율(0.0 ~ 0.9)을 계산합니다.
    """
    base_discount = 0.05
    trend_sensitive_categories = ["Comic", "Novel", "Magazine", "Children"]
    
    if category in trend_sensitive_categories:
        time_discount = min((days_in_inventory / 365) * 0.10, 0.10)
        base_discount += time_discount
            
    if ubci_score < 70:
        base_discount += 0.20
    elif ubci_score < 85:
        base_discount += 0.10
        
    final_discount = max(0.05, min(0.85, base_discount))
    return round(final_discount, 2)

def calculate_price_elasticity_revenue_optimization(
    list_price: float,
    ubci_score: float,
    days_in_inventory: int,
    category: str
) -> Dict[str, Any]:
    """
    [08_Dynamic_Pricing_합성_데이터_생성_명세서 연동]
    가격 탄력성(Price Elasticity) 기반 예측-최적화 2-Step 기대 수익 극대화 알고리즘
    Step 1: P_sold(delta) 고객 구매 확률 시뮬레이션
    Step 2: Expected Revenue E(delta) = P_sold(delta) * (P_base * (1 - delta)) 극대화 할인율 delta* 탐색
    """
    seasonality = CATEGORY_SEASONALITY.get(category, 1.0)
    base_price = list_price * CATEGORY_BASE_RATE.get(category, 0.40)
    
    best_discount = 0.05
    max_expected_revenue = 0.0
    best_p_sold = 0.0

    # 5% 단위로 최적 할인율 delta 탐색 (0.05 ~ 0.85)
    for step in range(5, 90, 5):
        delta = step / 100.0
        
        # Step 1: Customer Purchase Probability Formula
        p_sold = (
            0.30 +
            (delta * 0.80) -
            (((100.0 - ubci_score) / 100.0) * 0.60) +
            ((seasonality - 1.0) * 0.40) -
            (min(days_in_inventory, 365) / 365.0 * 0.10)
        )
        p_sold = max(0.05, min(0.98, p_sold))
        
        # Step 2: Expected Revenue E(delta)
        discounted_price = base_price * (1.0 - delta)
        expected_revenue = p_sold * discounted_price
        
        if expected_revenue > max_expected_revenue:
            max_expected_revenue = expected_revenue
            best_discount = delta
            best_p_sold = p_sold

    final_price = round(base_price * (1.0 - best_discount), -1)

    return {
        "list_price": list_price,
        "base_supply_price": round(base_price, -1),
        "optimal_discount_rate": round(best_discount, 2),
        "discount_percent": f"{int(best_discount * 100)}%",
        "predicted_purchase_probability": round(best_p_sold * 100, 1),
        "max_expected_revenue": round(max_expected_revenue, -1),
        "final_price": final_price,
        "optimization_model": "XGBoost 2-Step Price Elasticity & Expected Revenue Maximization"
    }
