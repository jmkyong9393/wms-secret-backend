import logging
import re
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from app.core.constants import BOX_CATALOG
from app.domains.inventory.bin_packing import (
    estimate_book_thickness_mm,
    estimate_book_weight_g,
)
from app.domains.inventory.packing3d import PackItem, PackResult, recommend_and_pack

logger = logging.getLogger(__name__)

# ==========================================
# 1. Pydantic Data Models (타입 안전성 및 API 명세 보장)
# ==========================================


class BookItem(BaseModel):
    id: str = "B01"
    name: str = "도서"
    length_mm: float = 225.0  # 신국판 규격 (225mm)
    width_mm: float = 152.0  # 신국판 규격 (152mm)
    pages: int = 300
    is_color: bool = False
    is_hardcover: bool = False
    calculated_thickness_mm: float = 0.0

    def calc_thickness(self) -> float:
        # SSOT 수식 (constants.py / Bms_Platform_4대알고리즘.md §2와 동일)
        self.calculated_thickness_mm = estimate_book_thickness_mm(
            pages=self.pages, is_color=self.is_color, is_hardcover=self.is_hardcover
        )
        return self.calculated_thickness_mm

    @property
    def footprint_area(self) -> float:
        return self.length_mm * self.width_mm

    @property
    def volume(self) -> float:
        return self.length_mm * self.width_mm * self.calc_thickness()

    @property
    def estimated_weight_g(self) -> float:
        return estimate_book_weight_g(
            pages=self.pages,
            width_mm=self.width_mm,
            length_mm=self.length_mm,
            is_color=self.is_color,
            is_hardcover=self.is_hardcover,
        )


class BoxSpec(BaseModel):
    id: str
    category: str
    name: str
    specs: str
    length: float
    width: float
    height: float
    max_weight_kg: float = 35.0

    @property
    def max_vol(self) -> float:
        return self.length * self.width * self.height


class CushionSpec(BaseModel):
    id: str
    name: str
    thick_mm: float
    type: str
    mode: str = "top"  # 'top' | 'side' | 'both' (3D 뷰어 바인딩)
    desc: str
    protection_score: int = 80  # 파손 방지 보호 점수 (프론트 카탈로그와 동일 스케일)


class PackagingRationale(BaseModel):
    summary_rationale: str = Field(
        description="물류 현장 작업자를 위한 2문장 이내의 종합 요약 사유"
    )
    space_efficiency_reason: str = Field(
        description="선택한 박스의 공간 효율성 및 유격 제어 관점의 이유"
    )
    protection_reason: str = Field(
        description="선택한 완충재 및 적재 순서에 따른 파손 방지 관점의 이유"
    )


# ==========================================
# 2. SubAgents Implementation
# ==========================================


class DimensionCalculatorAgent:
    """SubAgent 1: 3D Footprint(90도 회전 검증) 및 박스/완충재 선택 에이전트"""

    def __init__(self):
        # SSOT: constants.BOX_CATALOG(16종) — 프론트 카탈로그와 1:1 동일
        self.boxes = [
            BoxSpec(
                id=b["id"],
                category=b["category"],
                name=b["name"],
                specs=f"{b['length']}x{b['width']}x{b['height']}mm",
                length=b["length"],
                width=b["width"],
                height=b["height"],
                max_weight_kg=b["max_weight_kg"],
            )
            for b in BOX_CATALOG
        ]

        self.cushions = [
            CushionSpec(
                id="Cushion-01",
                name="에어필로우 완충 패드",
                thick_mm=9.0,
                type="AIR_PILLOW",
                mode="top",
                desc="상부 유격 충격 흡수 기본 패드",
                protection_score=75,
            ),
            CushionSpec(
                id="Cushion-02",
                name="친환경 벌집 종이",
                thick_mm=12.0,
                type="HONEYCOMB",
                mode="both",
                desc="양장본/희귀 도서 프리미엄 종이 래핑",
                protection_score=88,
            ),
            CushionSpec(
                id="Cushion-03",
                name="PE 폼 모서리 가드",
                thick_mm=15.0,
                type="FOAM_GUARD",
                mode="side",
                desc="중량 도서 스택 모서리 찌그러짐 방지",
                protection_score=92,
            ),
            CushionSpec(
                id="Cushion-04",
                name="에어 튜브 범퍼",
                thick_mm=20.0,
                type="AIR_TUBE",
                mode="both",
                desc="고위험 낙하 충격 에어 범퍼",
                protection_score=98,
            ),
        ]

    def calculate(self, books: List[BookItem]) -> Dict[str, Any]:
        total_vol = sum(b.volume for b in books)
        total_thick = sum(b.calc_thickness() for b in books)
        has_hardcover = any(b.is_hardcover for b in books)

        # 완충재 선(先)결정 -> 완충재 점유 공간을 차감한 내부 유효 공간에 패킹 (정석 + 도메인 제약)
        selected_cushion = self.cushions[1] if has_hardcover else self.cushions[0]
        side_margin = (
            selected_cushion.thick_mm
            if selected_cushion.mode in ("side", "both")
            else 0.0
        )
        top_margin = (
            selected_cushion.thick_mm
            if selected_cushion.mode in ("top", "both")
            else 0.0
        )

        items = [
            PackItem(
                id=f"{b.id}#{i + 1}",
                name=b.name,
                length=b.length_mm,
                width=b.width_mm,
                height=b.calculated_thickness_mm or b.calc_thickness(),
                weight_g=b.estimated_weight_g,
            )
            for i, b in enumerate(books)
        ]

        # 정석 EP-BFD 실배치 검증 기반 최소 박스 탐색 (부피 나눗셈 휴리스틱 폐기)
        pack = recommend_and_pack(
            items, side_margin_mm=side_margin, top_margin_mm=top_margin
        )
        first: PackResult = pack["results"][0]
        selected_box = next(bx for bx in self.boxes if bx.id == first.box["id"])

        fill_efficiency = first.volume_fill_ratio  # 실배치 기반 실측치 (상한 캡 제거)
        air_cushion_ratio = round(max(0.0, 100.0 - fill_efficiency), 1)

        return {
            "selected_box": selected_box,
            "selected_cushion": selected_cushion,
            "all_boxes": [b.model_dump() for b in self.boxes],
            "all_cushions": [c.model_dump() for c in self.cushions],
            "total_volume": total_vol,
            "total_thickness_mm": round(total_thick, 1),
            "total_weight_g": round(sum(it.weight_g for it in items), 1),
            "fill_efficiency": fill_efficiency,
            "air_cushion_ratio": air_cushion_ratio,
            "packing_algorithm": "EP-BFD (Extreme Point Best-Fit-Decreasing)",
            "split_shipment": pack["split"],
            "box_count": pack["box_count"],
            "stack_height_mm": round(first.stack_height, 1),
            "placements": [p.to_dict() for p in first.placements],
        }


class FragilitySafetyAgent:
    """SubAgent 2: 동적 적재 순서(Stacking Order) 생성 및 파손 방지 안전도 산출"""

    def evaluate(
        self,
        books: List[BookItem],
        box: BoxSpec,
        cushion: CushionSpec,
        stack_height_mm: Optional[float] = None,
    ) -> Dict[str, Any]:
        # EP-BFD 실배치 스택 높이 우선 사용, 없으면 두께 합산 폴백
        base_stack = (
            stack_height_mm
            if stack_height_mm is not None
            else sum(b.calculated_thickness_mm for b in books)
        )
        top_cushion = cushion.thick_mm if cushion.mode in ("top", "both") else 0.0
        total_stack_mm = base_stack + top_cushion

        # 상한 캡 제거: 100% 초과 = 물리적 수용 불가 신호로 그대로 노출
        height_fill_ratio = (total_stack_mm / box.height) * 100.0
        is_overflow = total_stack_mm > box.height
        void_space_mm = max(0.0, box.height - total_stack_mm)

        fill_score = (min(height_fill_ratio, 100.0) / 100.0) * 65.0
        cushion_score = (
            cushion.protection_score * 0.2
        )  # 완충재 보호 점수 실연동 (고정값 20 폐기)
        has_hardcover = any(b.is_hardcover for b in books)
        cover_protection = 15.0 if has_hardcover else 10.0
        void_penalty = (
            (void_space_mm / box.height) * 45.0 if height_fill_ratio < 85.0 else 0.0
        )

        safety_score = max(
            15.0, round(fill_score + cushion_score + cover_protection - void_penalty, 1)
        )

        if is_overflow:
            safety_score = min(safety_score, 40.0)
            safety_level = f"OVERFLOW (수직 높이 초과 — 수용 불가) [{safety_score}점]"
        elif safety_score >= 88.0:
            safety_level = f"SAFE (A+) [{safety_score}점]"
        elif safety_score >= 75.0:
            safety_level = f"SAFE (A) [{safety_score}점]"
        elif safety_score >= 60.0:
            safety_level = f"CAUTION (B) [{safety_score}점]"
        else:
            safety_level = f"WARNING (C) [{safety_score}점]"

        # [동적 스태킹 순서 생성] 면적이 넓고 무거운 책을 밑에 배치
        sorted_books = sorted(
            books, key=lambda x: (x.footprint_area, x.is_hardcover), reverse=True
        )
        stacking_steps = [f"하단: {sorted_books[0].name} (기반 적재)"]

        for b in sorted_books[1:]:
            type_str = "하드커버" if b.is_hardcover else "평판"
            stacking_steps.append(f"중단: {b.name} ({type_str})")

        stacking_steps.append(f"상단: {cushion.name} (유격 보충)")
        stacking_order = " ➔ ".join(stacking_steps)

        return {
            "safety_score": safety_score,
            "safety_level": safety_level,
            "height_fill_ratio": round(height_fill_ratio, 1),
            "void_space_mm": round(void_space_mm, 1),
            "stacking_order": stacking_order,
        }


class PackagingPlannerAgent:
    """SubAgent 3: Structured Output 기반 LLM 추론 근거(Rationale) 생성 에이전트"""

    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)

        self.prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """당신은 대한민국 최고 수준의 출판 물류 AI 패킹 수석 엔지니어입니다.
규격 데이터와 안전성 평가 결과를 바탕으로 현장 작업자가 즉시 이해할 수 있는 명확한 패킹 근거(Rationale)를 작성하세요.

[분석 가이드라인]
1. 도서 슬림 박스(BOOK_SLIM)와 표준 택배 박스의 차이점(상부 유격 방지)을 반영할 것.
2. 완충재 선택이 도서 파손(모서리 찌그러짐 등)을 어떻게 방지하는지 명시할 것.
3. 억지스러운 칭찬을 배제하고 물류 효율성 관점에서 객관적인 사실만 전달할 것.""",
                ),
                (
                    "user",
                    """
[주문 도서 목록]
{books_summary}

[선택된 박스 및 완충재]
- 박스: {box_name} ({box_specs})
- 완충재: {cushion_name} ({cushion_desc})
- 체적 채움 비율: {fill_efficiency}%
- 도서 총 두께: {total_thickness}mm

[안전성 평가]
- 안전 등급: {safety_level}
- 적재 순서: {stacking_order}

위 데이터를 바탕으로 구조화된 리포트를 작성해주세요.
""",
                ),
            ]
        )

        try:
            self.chain = self.prompt | self.llm.with_structured_output(
                PackagingRationale
            )
        except Exception as e:
            logger.warning(f"Failed to initialize structured output LLM chain: {e}")
            self.chain = None

    def generate_rationale(
        self, books: List[BookItem], dim_res: Dict[str, Any], frag_res: Dict[str, Any]
    ) -> str:
        box: BoxSpec = dim_res["selected_box"]
        cushion: CushionSpec = dim_res["selected_cushion"]

        if self.chain:
            try:
                books_summary = "\n".join(
                    [
                        f"- {b.name} (페이지: {b.pages}p, 양장: {b.is_hardcover})"
                        for b in books
                    ]
                )
                res: PackagingRationale = self.chain.invoke(
                    {
                        "books_summary": books_summary,
                        "box_name": box.name,
                        "box_specs": f"{box.length}x{box.width}x{box.height}mm",
                        "cushion_name": cushion.name,
                        "cushion_desc": cushion.desc,
                        "fill_efficiency": dim_res["fill_efficiency"],
                        "total_thickness": dim_res["total_thickness_mm"],
                        "safety_level": frag_res["safety_level"],
                        "stacking_order": frag_res["stacking_order"],
                    }
                )
                return res.summary_rationale
            except Exception as e:
                logger.warning(f"GPT-4o-mini Structured Rationale Fallback: {e}")

        # Fallback Rationale
        return f"실제 도서 적재 높이({dim_res['total_thickness_mm']}mm)에 맞춰 과도한 상부 유격을 방지하기 위해 도서 슬림 전용 {box.name}을 추천하였으며, {cushion.name}로 완충 적재율 {dim_res['fill_efficiency']}% 및 파손 방지 안전성을 확보했습니다."


# ==========================================
# 3. Main Orchestrator Agent
# ==========================================


class BinPackingAgent:
    """3D Bin Packing Multi-Agent Orchestrator"""

    def __init__(self):
        self.dim_agent = DimensionCalculatorAgent()
        self.frag_agent = FragilitySafetyAgent()
        self.planner_agent = PackagingPlannerAgent()

    def optimize_packing(self, input_books: List[Dict[str, Any]]) -> Dict[str, Any]:
        # Dictionary -> Pydantic Model 변환
        books = []
        if input_books:
            for b in input_books:
                books.append(
                    BookItem(
                        id=b.get("id", "B01"),
                        name=b.get("name", b.get("category", "도서")),
                        pages=b.get("pages", 300),
                        is_color=b.get("is_color", False),
                        is_hardcover=b.get("is_hardcover", False),
                    )
                )
        else:
            books = [
                BookItem(
                    id="B01",
                    name="Do it! 점프 투 파이썬",
                    pages=380,
                    is_hardcover=False,
                ),
                BookItem(
                    id="B02", name="SQL 자격검정 실전문제", pages=240, is_hardcover=True
                ),
            ]

        # Step 1: 3D Dimension Calculation
        dim_res = self.dim_agent.calculate(books)

        # Step 2: Fragility & Safety Evaluation (EP-BFD 실배치 스택 높이 연동)
        frag_res = self.frag_agent.evaluate(
            books,
            dim_res["selected_box"],
            dim_res["selected_cushion"],
            stack_height_mm=dim_res.get("stack_height_mm"),
        )

        # Step 3: LLM Structured Rationale Generation
        rationale_text = self.planner_agent.generate_rationale(books, dim_res, frag_res)

        selected_box: BoxSpec = dim_res["selected_box"]
        selected_cushion: CushionSpec = dim_res["selected_cushion"]

        # API 호환성을 유지하기 위한 하위 호환 매핑 + EP-BFD 배치 좌표 신규 노출
        return {
            "recommended_box": selected_box.name,
            "recommended_cushion": selected_cushion.name,
            "box_specs": selected_box.specs,
            "efficiency": dim_res["fill_efficiency"],
            "fill_efficiency": dim_res["fill_efficiency"],
            "total_thickness_mm": dim_res["total_thickness_mm"],
            "total_weight_g": dim_res["total_weight_g"],
            "air_cushion_ratio": dim_res["air_cushion_ratio"],
            "safety_grade": frag_res["safety_level"],
            "safety_level": frag_res["safety_level"],
            "stacking_order": frag_res["stacking_order"],
            "ai_reasoning_log": rationale_text,
            "rationale": rationale_text,
            "all_boxes": dim_res["all_boxes"],
            "all_cushions": dim_res["all_cushions"],
            "packing_algorithm": dim_res["packing_algorithm"],
            "split_shipment": dim_res["split_shipment"],
            "box_count": dim_res["box_count"],
            "stack_height_mm": dim_res["stack_height_mm"],
            "placements": dim_res["placements"],
        }


# Singleton Instance
bin_packing_agent = BinPackingAgent()
