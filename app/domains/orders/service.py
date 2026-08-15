from typing import Dict, Any, List, Optional

# [수정 이력] 이 모듈에 CATEGORY_BASE_RATE / calculate_b2b_price /
# calculate_dynamic_discount_rate가 pricing.py와 **완전히 중복 정의**되어 있었다.
# orders/router.py는 pricing.py 쪽을 import하고 이 파일은 자기 사본을 쓰는 이중 상태여서,
# 요율을 한쪽만 고치면 경로에 따라 서로 다른 가격이 나오는 구조였다.
# 단일 소스(pricing.py)로 통일하고 여기서는 재노출만 한다.
from app.ml.pricing_predictor import predict_p_sold_batch, model_label
from app.domains.orders.pricing import (  # noqa: F401  (기존 import 경로 호환 유지)
    CATEGORY_BASE_RATE,
    CATEGORY_SEASONALITY,
    calculate_b2b_price,
    calculate_dynamic_discount_rate,
    normalize_category,
)

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
    # 요율 조회 전 카테고리를 표준 키로 정규화한다. DB에는 한글 분류("컴퓨터/모바일")와
    # "GENERAL"이 대부분이라, 정규화 없이 조회하면 거의 전 건이 기본값 폴백으로 떨어져
    # 카테고리별 차등이 사라진다.
    canonical_category = normalize_category(category)
    seasonality = CATEGORY_SEASONALITY.get(canonical_category, 1.0)
    base_price = list_price * CATEGORY_BASE_RATE.get(canonical_category, CATEGORY_BASE_RATE["General"])
    
    # Non-perishable Book Dwell Defense (-10% max penalty)
    dwell_decay = round(min(days_in_inventory, 365) / 365.0 * 0.10, 3)
    trend_badge_text = f"비부패성 보관료 방어: -{round(dwell_decay*100, 1)}% ({days_in_inventory}일 체류)"

    best_discount = 0.05
    max_expected_revenue = 0.0
    best_p_sold = 0.0

    # Step 1: 학습된 XGBoost 모델이 할인율 후보별 구매 성사 확률을 예측한다.
    # 후보 전체를 한 번에 넘겨 그리드 탐색 1회당 추론도 1회만 일어나게 한다.
    # 모델 파일이 없으면 predictor가 기존 선형 산식으로 자동 폴백한다.
    candidates = [step / 100.0 for step in range(5, 90, 5)]
    p_sold_list = predict_p_sold_batch(candidates, ubci_score, seasonality, days_in_inventory)

    # Step 2: 기대매출 E(delta) = P(구매) x 할인 적용가 가 최대인 할인율을 고른다.
    for delta, p_sold in zip(candidates, p_sold_list):
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
        "optimization_model": model_label()
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


# ==========================================
# 주문 라인 해석 · 송장 채번 · 도서 물성 조회
# [2026-08-14] router.py에서 이관. 라우터는 HTTP 입출력만 맡고
# 업무 규칙은 이 파일이 갖는다 (슬라이스 내부 계층 분리).
# ==========================================
from datetime import datetime
from functools import lru_cache
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, select

from app.models.wms import (
    Book, InventoryUsedItem, ItemStatusEnum, PickingInstruction, now_kst,
)
from app.domains.orders.schemas import OrderLineRequest


def _resolve_order_lines(session: Session, items: List[OrderLineRequest]) -> List[Dict[str, Any]]:
    """
    프론트 선택 항목을 (book, is_new, quantity, ubci, days) 라인으로 해석.
    같은 book의 중고 LPN 여러 개는 개별 라인(각 1권)으로 유지해 LPN별 UBCI 가격을 보존한다.
    """
    lines: List[Dict[str, Any]] = []
    now = now_kst()
    for line in items:
        qty = max(1, line.quantity)
        if line.id.startswith("NEW-BOOK-"):
            book = session.get(Book, UUID(line.id.replace("NEW-BOOK-", "")))
            if not book:
                raise HTTPException(404, f"신품 도서를 찾을 수 없습니다: {line.id}")

            # [2026-08-10 신설] 신품 재고 검증. 중고는 아래 분기에서 IN_STOCK을 확인하는데
            # 신품에는 검증이 전혀 없어, 재고 0인 책도 주문에 실리고 피킹 지시서까지 발행됐다.
            # 신품은 발주로 채울 수 있으므로 주문 자체를 막지는 않되(발주 제안이 자동 생성된다),
            # 보유 수량을 넘는 요청은 여기서 거절한다 - 없는 물건을 팔기로 확정할 수는 없다.
            from app.domains.inventory.service import get_new_stock_qty

            available = get_new_stock_qty(session, book.id)
            if qty > available:
                raise HTTPException(
                    409,
                    f"'{book.title}' 재고 부족: 요청 {qty}권 / 가용 {available}권. "
                    f"발주(SCM) 승인으로 재고를 채운 뒤 주문해 주세요.",
                )

            lines.append({
                "book": book, "is_new": True, "quantity": qty,
                "ubci_score": None, "days_in_inventory": 1,
                "used_item": None,
            })
        else:
            used = session.get(InventoryUsedItem, UUID(line.id))
            if not used:
                raise HTTPException(404, f"중고 재고(LPN)를 찾을 수 없습니다: {line.id}")
            if used.item_status != ItemStatusEnum.IN_STOCK.value:
                raise HTTPException(409, f"이미 할당/출고된 LPN입니다: {used.lpn_barcode}")
            book = session.get(Book, used.book_id)
            days = max(1, (now - used.created_at).days) if used.created_at else 120
            lines.append({
                "book": book, "is_new": False, "quantity": 1,
                "ubci_score": used.ubci_score, "days_in_inventory": days,
                # 고른 LPN을 그대로 들고 간다. 여기서 흘리면 할당 엔진이 같은 책의
                # 다른 개체를 FIFO로 다시 골라, 주문한 책과 다른 책이 출고된다.
                "used_item": used,
            })
    return lines


def _issue_waybill_no(session: Session) -> str:
    issued = session.exec(
        select(PickingInstruction).where(PickingInstruction.cj_waybill_no.is_not(None))
    ).all()
    return f"CJ-2026-{datetime.now().strftime('%m%d')}-{len(issued) + 1:04d}"


def fetch_aladin_real_packing_spec(isbn: str) -> dict:
    """
    1순위: 알라딘 TTB Open API에서 실제 물리 규격(가로, 세로, 두께, 무게, 페이지) 최우선 조회 (0ms 메모리 캐시 파이프라인)
    """
    import urllib.request
    import json
    from app.core.config import settings

    # [수정 이력] 이 키는 설정값(settings.ALADIN_TTB_KEY)과 다른 하드코딩된 키를 쓰고 있어
    # 알라딘 API 호출이 조용히 실패(빈 dict 폴백)하고 있었다 - 실제 발급된 키로 교정.
    ttb_key = settings.ALADIN_TTB_KEY
    # http는 알라딘 서버가 https로 301 리다이렉트한다 - 리다이렉트 왕복 지연(0.8s 타임아웃 내
    # 예산 초과 위험)을 피하기 위해 https를 직접 호출한다.
    url = f"https://www.aladin.co.kr/ttb/api/ItemLookUp.aspx?ttbkey={ttb_key}&itemIdType=ISBN13&ItemId={isbn}&output=js&Version=20130701&OptResult=packing"
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=0.8) as response:
            data = json.loads(response.read().decode('utf-8'))
            items = data.get("item", [])
            if items:
                item = items[0]
                sub_info = item.get("subInfo", {})
                packing = sub_info.get("packing", {})
                
                size_w = packing.get("sizeWidth")
                size_d = packing.get("sizeHeight")
                size_h = packing.get("sizeDepth")
                weight = packing.get("weight")
                item_page = sub_info.get("itemPage") or item.get("itemPage")
                
                res = {}
                if size_w and int(size_w) > 0: res["width_mm"] = float(size_w)
                if size_d and int(size_d) > 0: res["depth_mm"] = float(size_d)
                if size_h and int(size_h) > 0: res["thickness_mm"] = float(size_h)
                if weight and int(weight) > 0: res["weight_g"] = float(weight)
                if item_page and int(item_page) > 0: res["page_count"] = int(item_page)
                if res:
                    res["source"] = "ALADIN_API_1ST_PRIORITY"
                    return res
    except Exception:
        pass
    return {}
