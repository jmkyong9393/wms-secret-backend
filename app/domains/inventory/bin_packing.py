from typing import Any, Dict, List, Optional

from app.core.constants import (
    BOOK_FORMATS,
    BOX_CATALOG,
    CATEGORY_FALLBACK,
    COVER_WEIGHT_G,
    CUSHION_MARGIN_RATIO,
    HARDCOVER_EXTRA_MM,
    MIN_BOOK_THICKNESS_MM,
    PAGE_THICKNESS_MM,
    PAPER_GRAMMAGE_GSM,
)


def resolve_book_format(book_meta: Dict[str, Any]) -> Dict[str, float]:
    """판형(가로x세로 mm)을 결정한다. 누락 시 카테고리 기반 스마트 폴백."""
    category = book_meta.get("category", "Novel")
    format_size = book_meta.get("format_size")
    if not format_size or format_size not in BOOK_FORMATS:
        format_size = CATEGORY_FALLBACK.get(category, "신국판")
    return BOOK_FORMATS[format_size]


def estimate_book_thickness_mm(
    pages: int, is_color: bool = False, is_hardcover: bool = False
) -> float:
    """
    페이지 수 기반 두께 추정 (SSOT 수식):
    t = pages x (컬러 0.08 | 흑백 0.05) + (양장 +6.0mm), 하한 3.0mm
    """
    per_page = PAGE_THICKNESS_MM["color"] if is_color else PAGE_THICKNESS_MM["mono"]
    cover = HARDCOVER_EXTRA_MM if is_hardcover else 0.0
    return round(max(MIN_BOOK_THICKNESS_MM, (pages * per_page) + cover), 1)


def estimate_book_weight_g(
    pages: int,
    width_mm: float,
    length_mm: float,
    is_color: bool = False,
    is_hardcover: bool = False,
) -> float:
    """
    중량 추정 (SSOT 수식): 1장(leaf)=2페이지, 평량(g/m²) 기준
    w = (pages/2) x (W_m x L_m) x 평량 + 커버 중량(양장 150g | 일반 50g)
    """
    area_m2 = (width_mm / 1000.0) * (length_mm / 1000.0)
    grammage = PAPER_GRAMMAGE_GSM["color"] if is_color else PAPER_GRAMMAGE_GSM["mono"]
    cover_g = COVER_WEIGHT_G["hard"] if is_hardcover else COVER_WEIGHT_G["soft"]
    return round((pages / 2.0) * area_m2 * grammage + cover_g, 1)


def calculate_book_volume(book_meta: Dict[str, Any]) -> float:
    """단일 도서의 체적(Volume, mm^3)을 정밀 계산합니다."""
    base_area = resolve_book_format(book_meta)
    total_thickness = estimate_book_thickness_mm(
        pages=book_meta.get("pages", 300),
        is_color=book_meta.get("is_color", False),
        is_hardcover=book_meta.get("is_hardcover", False),
    )
    return base_area["width"] * base_area["length"] * total_thickness


def _fits_footprint(item_l: float, item_w: float, box: Dict[str, Any]) -> bool:
    """90도 회전을 고려하여 도서 바닥면이 박스 바닥면에 수용되는지 검증"""
    direct = (item_l <= box["length"]) and (item_w <= box["width"])
    rotated = (item_l <= box["width"]) and (item_w <= box["length"])
    return direct or rotated


def recommend_optimal_box(books: List[Dict[str, Any]]) -> str:
    """
    주문 내역(책 리스트)에 대해 3중 제약(체적 x1.15 완충 마진, 2D Footprint 90도 회전,
    최대 허용 중량)을 모두 만족하는 가장 작은 박스를 BOX_CATALOG(16종 SSOT)에서 추천합니다.
    """
    if not books:
        return "포장할 상품이 없습니다."

    total_volume = sum(calculate_book_volume(book) for book in books)
    target_volume = total_volume * CUSHION_MARGIN_RATIO

    total_weight_g = 0.0
    max_item_l = 0.0
    max_item_w = 0.0
    for book in books:
        fmt = resolve_book_format(book)
        max_item_l = max(max_item_l, fmt["length"])
        max_item_w = max(max_item_w, fmt["width"])
        total_weight_g += estimate_book_weight_g(
            pages=book.get("pages", 300),
            width_mm=fmt["width"],
            length_mm=fmt["length"],
            is_color=book.get("is_color", False),
            is_hardcover=book.get("is_hardcover", False),
        )

    # 부피 오름차순 전수 탐색 → 3중 제약 만족하는 최소 박스 선택
    sorted_boxes = sorted(
        BOX_CATALOG, key=lambda b: b["length"] * b["width"] * b["height"]
    )
    for box in sorted_boxes:
        box_volume = box["length"] * box["width"] * box["height"]
        if (
            _fits_footprint(max_item_l, max_item_w, box)
            and box_volume >= target_volume
            and total_weight_g <= box["max_weight_kg"] * 1000.0
        ):
            return box["name"]

    # 단일 박스 수용 불가 → 최대 규격(마스터 카톤) 반환 (분할 출고 권장 대상)
    return sorted_boxes[-1]["name"]
