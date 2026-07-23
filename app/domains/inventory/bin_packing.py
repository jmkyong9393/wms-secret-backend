import math
from typing import List, Dict, Any
from app.core.constants import BOX_STANDARDS, BoxStandardEnum

# 도서 판형 규격 상수 (mm 단위)
BOOK_FORMATS = {
    "신국판": {"width": 152, "length": 225},
    "46판": {"width": 128, "length": 188},
    "4x6배판": {"width": 188, "length": 257}
}

# AI Category-based Fallback (엣지 케이스 방어용)
CATEGORY_FALLBACK = {
    "IT": "4x6배판",
    "Textbook": "4x6배판",
    "Novel": "신국판",
    "Essay": "신국판",
    "Comic": "46판"
}

def calculate_book_volume(book_meta: Dict[str, Any]) -> float:
    """
    단일 도서의 체적(Volume, mm^3)을 정밀 계산합니다.
    """
    category = book_meta.get("category", "Novel")
    format_size = book_meta.get("format_size")
    pages = book_meta.get("pages", 300) # 누락 시 기본값 300p
    is_color = book_meta.get("is_color", False)
    is_hardcover = book_meta.get("is_hardcover", False)
    
    # 1. 판형(면적) 결정 - 누락 시 카테고리 기반 스마트 폴백
    if not format_size or format_size not in BOOK_FORMATS:
        format_size = CATEGORY_FALLBACK.get(category, "신국판")
        
    base_area = BOOK_FORMATS[format_size]
    
    # 2. 두께 계산 (명세서 기반)
    thickness_per_page = 0.08 if is_color else 0.05
    cover_thickness = 6.0 if is_hardcover else 0.0
    
    total_thickness = (pages * thickness_per_page) + cover_thickness
    
    # 3. 최종 체적 (가로 * 세로 * 높이)
    volume = base_area["width"] * base_area["length"] * total_thickness
    return volume

def recommend_optimal_box(books: List[Dict[str, Any]]) -> str:
    """
    주문 내역(책 리스트)의 총 체적을 기반으로 완충재 마진(1.15배)을 포함하여 
    가장 작은 최적의 우체국 박스 규격을 추천합니다.
    """
    if not books:
        return "포장할 상품이 없습니다."
        
    total_book_volume = sum(calculate_book_volume(book) for book in books)
    target_volume = total_book_volume * 1.15 # 완충재 마진 15% 추가
    
    # 가능한 박스 필터링 (부피 기반 추정 - 3D Bin Packing Fallback)
    suitable_boxes = []
    for box_name, dims in BOX_STANDARDS.items():
        box_volume = dims["width"] * dims["length"] * dims["height"]
        if box_volume >= target_volume:
            suitable_boxes.append((box_name, box_volume))
            
    # 조건에 맞는 박스가 없으면 가장 큰 박스(6호) 반환, 있으면 가장 작은 박스 선택
    if not suitable_boxes:
        return BoxStandardEnum.BOX_6.value
        
    # 부피 기준 오름차순 정렬 후 첫 번째(가장 작은) 상자 리턴
    suitable_boxes.sort(key=lambda x: x[1])
    return suitable_boxes[0][0].value
