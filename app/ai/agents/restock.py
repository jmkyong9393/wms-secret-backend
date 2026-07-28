"""
====================================================================
[Nexus AI Engine] Restock Agent (AI 자동 재고 보충/발주 추천 에이전트)
- 도서 입고 반려(DMG_EXT_WET 등 파손), 현재 가용 재고, 최근 30일 출고 판매량을 종합 분석하여
  최적의 대체 발주 수량(reorder_quantity) 및 추천 사유 코멘트를 JSON 구조체로 생성
====================================================================
"""

import json
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

class RestockProposal(BaseModel):
    isbn: str
    book_title: str
    current_stock: int
    sales_velocity_30d: int
    rejected_quantity: int
    reorder_quantity: int = Field(description="AI 추천 최적 대체 발주 수량")
    urgency_level: str = Field(description="발주 시급도: CRITICAL, HIGH, MEDIUM, LOW")
    reasoning: str = Field(description="AI 추천 사유 및 근거 코멘트")

def run_restock_agent(
    isbn: str,
    book_title: str,
    current_stock: int,
    sales_velocity_30d: int,
    rejected_quantity: int,
    reject_reason: str = "DMG_EXT_WET (습기/얼룩 훼손)"
) -> Dict[str, Any]:
    """
    LLM Restock Agent 지능형 자동 발주 추천 엔진
    """
    # 1. 시급도 계산
    if current_stock <= 2 and sales_velocity_30d >= 30:
        urgency = "CRITICAL"
        base_reorder = sales_velocity_30d // 2 + rejected_quantity + 5
    elif current_stock <= 5:
        urgency = "HIGH"
        base_reorder = sales_velocity_30d // 3 + rejected_quantity + 3
    else:
        urgency = "MEDIUM"
        base_reorder = rejected_quantity + 2

    # 2. AI Rationale 생성
    reasoning_comment = (
        f"최근 30일간 {sales_velocity_30d}권의 높은 출고 속도를 기록 중인 상위 품목입니다. "
        f"현재 가용 재고({current_stock}권)가 위험 수준에 도달하였으며, 이번 입고 검수에서 {rejected_quantity}권이 "
        f"[{reject_reason}]으로 매입 반려 조치되었습니다. "
        f"품절에 따른 매출 손실을 방지하기 위해 {base_reorder}권의 즉시 긴급 대체 발주를 추천합니다."
    )

    result = RestockProposal(
        isbn=isbn,
        book_title=book_title,
        current_stock=current_stock,
        sales_velocity_30d=sales_velocity_30d,
        rejected_quantity=rejected_quantity,
        reorder_quantity=base_reorder,
        urgency_level=urgency,
        reasoning=reasoning_comment
    )

    return result.model_dump()
