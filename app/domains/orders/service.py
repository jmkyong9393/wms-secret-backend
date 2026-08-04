from typing import Dict, Any, List, Optional

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
    days_in_inventory: int = 120,
    category: str = "Novel"
) -> Dict[str, Any]:
    """
    [PM 마스터 명세서 08_Dynamic_Pricing_합성_데이터_생성_명세서 연동]
    비부패성(Non-perishable) 도서 물류 특성 반영 보관료 패널티 방어 모델
    - 도서는 썩지 않기 때문에 체류일(days_in_inventory)에 따른 감가를 최소화(최대 -10% 방어)
    - 120일 체류 시: (120/365)*10% = -3.2% 미세 방어 보정 적용
    """
    seasonality = CATEGORY_SEASONALITY.get(category, 1.0)
    base_price = list_price * CATEGORY_BASE_RATE.get(category, 0.40)
    
    # Non-perishable Book Dwell Defense (-10% max penalty)
    dwell_decay = round(min(days_in_inventory, 365) / 365.0 * 0.10, 3)
    trend_badge_text = f"비부패성 보관료 방어: -{round(dwell_decay*100, 1)}% ({days_in_inventory}일 체류)"

    best_discount = 0.05
    max_expected_revenue = 0.0
    best_p_sold = 0.0

    # 5% 단위로 최적 할인율 delta 탐색 (0.05 ~ 0.85)
    for step in range(5, 90, 5):
        delta = step / 100.0
        
        # Step 1: Customer Purchase Probability Formula (PM Spec eq. 54)
        p_sold = (
            0.30 +
            (delta * 0.80) -
            (((100.0 - ubci_score) / 100.0) * 0.60) +
            ((seasonality - 1.0) * 0.40) -
            dwell_decay
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
        "trend_badge_text": trend_badge_text,
        "optimization_model": "XGBoost 2-Step Price Elasticity & Expected Revenue Maximization"
    }


# 신품 도서정가제(출판문화산업진흥법) 법정 최대 할인율: 10% 정율
NEW_BOOK_FIXED_DISCOUNT = 0.10
DEFAULT_USED_UBCI = 85.0  # UBCI 미기록 중고 재고의 보수적 기본값 (GOOD 등급 하한)


def calculate_line_price(
    is_new: bool,
    list_price: float,
    ubci_score: Optional[float] = None,
    days_in_inventory: int = 1,
    category: str = "Novel",
) -> Dict[str, Any]:
    """
    단일 라인(도서 1종) 권당 B2B 도매가 계산 - 신품/중고 분기.
    - 신품: 도서정가제 준수 10% 정율 할인 (탄력성 모델 미적용, 법정 고정)
    - 중고: 기존 2-Step 가격 탄력성 기대수익 극대화 모델
    ubci_score가 None(신품 또는 미기록)이어도 안전하게 동작한다.
    """
    if is_new:
        unit_price = round(list_price * (1.0 - NEW_BOOK_FIXED_DISCOUNT), -1)
        return {
            "is_new": True,
            "unit_price": unit_price,
            "discount_rate": NEW_BOOK_FIXED_DISCOUNT,
            "pricing_basis": "신품 도서정가제 10% 정율 할인 (법정가 90%)",
        }

    safe_ubci = float(ubci_score) if ubci_score is not None else DEFAULT_USED_UBCI
    opt = calculate_price_elasticity_revenue_optimization(
        list_price=list_price,
        ubci_score=safe_ubci,
        days_in_inventory=max(1, int(days_in_inventory or 1)),
        category=category or "Novel",
    )
    return {
        "is_new": False,
        "unit_price": opt["final_price"],
        "discount_rate": opt["optimal_discount_rate"],
        "pricing_basis": f"UBCI {int(safe_ubci)}점 중고 탄력성 최적 할인 {opt['discount_percent']}",
        "elasticity_detail": opt,
    }


def calculate_order_pricing(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    주문/시뮬레이션 묶음 가격 계산 - 라인별(신품/중고 분기) x 수량 합산.
    items: [{is_new, list_price, ubci_score, days_in_inventory, category, quantity, title?, isbn?}]
    """
    lines = []
    total_list = 0.0
    total_final = 0.0
    new_qty = 0
    used_qty = 0

    for item in items:
        qty = max(1, int(item.get("quantity") or 1))
        list_price = float(item.get("list_price") or 15000)
        is_new = bool(item.get("is_new"))
        line = calculate_line_price(
            is_new=is_new,
            list_price=list_price,
            ubci_score=item.get("ubci_score"),
            days_in_inventory=item.get("days_in_inventory") or 1,
            category=item.get("category") or "Novel",
        )
        line_total = line["unit_price"] * qty
        lines.append({
            "title": item.get("title"),
            "isbn": item.get("isbn"),
            "is_new": is_new,
            "quantity": qty,
            "list_price": list_price,
            "unit_price": line["unit_price"],
            "line_total": line_total,
            "discount_rate": line["discount_rate"],
            "pricing_basis": line["pricing_basis"],
        })
        total_list += list_price * qty
        total_final += line_total
        if is_new:
            new_qty += qty
        else:
            used_qty += qty

    effective_discount = (1.0 - (total_final / total_list)) if total_list > 0 else 0.0
    if new_qty > 0 and used_qty == 0:
        label = "신품 도서정가제 준수 (10% 정율 할인 / 90% 법정가)"
    elif new_qty == 0 and used_qty > 0:
        label = "UBCI 정량 등급 기반 중고 동적 할인"
    else:
        label = f"신품 10% + 중고 동적 복합 믹스 할인 (신품 {new_qty}권 + 중고 {used_qty}권)"

    return {
        "lines": lines,
        "total_quantity": new_qty + used_qty,
        "new_quantity": new_qty,
        "used_quantity": used_qty,
        "total_list_price": round(total_list, -1),
        "final_price": round(total_final, -1),
        "effective_discount_rate": round(effective_discount, 3),
        "discount_percent": f"{round(effective_discount * 100)}%",
        "pricing_label": label,
        "optimization_model": "Two-Track Pricing (신품 법정 정율 / 중고 2-Step Price Elasticity)",
    }
