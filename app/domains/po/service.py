import json
from typing import Any, Dict, List
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlmodel import Session, select
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from app.models.wms import Book, Order, OrderTypeEnum, OrderStatusEnum, Inventory, Location, ConditionGradeEnum as GradeEnum

# Auto-PO 사유 텍스트 생성 전용 LLM (비용 최적화 원칙에 따라 gpt-4o-mini 고정 - Vision Agent가
# 아닌 일반 도메인 로직이라 프리즈 규정 적용 대상은 아니지만 동일 원칙을 따른다)
try:
    _reason_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
except Exception:
    _reason_llm = None


class _ReasonItem(BaseModel):
    title: str = Field(description="입력으로 받은 도서 제목과 정확히 동일한 문자열")
    reasoning: str = Field(description="이모지 1개 + 재고 부족 사유 한 문장 (기존 톤 유지: 예 '🚨 안전 재고 고갈')")


class _ReasonBatch(BaseModel):
    items: List[_ReasonItem]


class POService:
    """
    Spring Boot의 @Service 역할 - PO(Purchase Order/자동 발주) 도메인 비즈니스 로직.
    (2-Layer 아키텍처: Router에는 Pydantic 검증과 이 Service 호출만 남긴다)
    """

    # DB에 책이 하나도 없을 때의 고신뢰성 폴백/초기 시드 카탈로그
    SEED_PO_BOOKS = [
        {"title": "Do it! 점프 투 파이썬 (개정 2판)", "isbn": "9791163033455", "publisher": "이지스퍼블리싱", "stock": 3, "qty": 50, "cost": 1250000},
        {"title": "SQL 자격검정 실전문제 (국가공인 SQLD/SQLP)", "isbn": "9788988474846", "publisher": "한국데이터산업진흥원", "stock": 5, "qty": 50, "cost": 1250000},
        {"title": "클린 아키텍처 (Clean Architecture)", "isbn": "9788966262472", "publisher": "인사이트", "stock": 2, "qty": 50, "cost": 1250000},
        {"title": "트렌드 코리아 2026", "isbn": "9791192804561", "publisher": "미래의창", "stock": 4, "qty": 50, "cost": 1250000},
        {"title": "원씽 (The One Thing)", "isbn": "9788901159850", "publisher": "비즈니스북스", "stock": 1, "qty": 50, "cost": 1250000},
        {"title": "세이노의 가르침", "isbn": "9791168473690", "publisher": "데이원", "stock": 6, "qty": 50, "cost": 1250000},
        {"title": "역행자 (확장판)", "isbn": "9791192534176", "publisher": "웅진지식하우스", "stock": 2, "qty": 50, "cost": 1250000},
        {"title": "자바 ORM 표준 JPA 프로그래밍", "isbn": "9788960777330", "publisher": "에이콘출판", "stock": 5, "qty": 50, "cost": 1250000},
        {"title": "리팩터링 2판 (Refactoring 2nd Ed.)", "isbn": "9791162242742", "publisher": "한빛미디어", "stock": 4, "qty": 50, "cost": 1250000},
        {"title": "돈의 속성 (김승호 저)", "isbn": "9791188331796", "publisher": "스노우폭스북스", "stock": 7, "qty": 50, "cost": 1250000},
        {"title": "초역 부처의 말", "isbn": "9791191043785", "publisher": "포레스트북스", "stock": 3, "qty": 50, "cost": 1250000},
        {"title": "불편한 편의점 (김호연 소설)", "isbn": "9791161571188", "publisher": "나무옆의의자", "stock": 2, "qty": 50, "cost": 1250000},
    ]

    SAFETY_STOCK = 15
    TARGET_STOCK = 50

    def _urgency(self, stock: int) -> str:
        return "CRITICAL" if stock <= 3 else ("HIGH" if stock <= 8 else "NORMAL")

    def _fallback_reason(self, stock: int, recommended_qty: int) -> str:
        if stock <= 3:
            return f"🚨 출고 및 검수 파손 감가 급증으로 안전 재고 고갈 (현재: {stock}권 / 임계치: {self.SAFETY_STOCK}권)"
        if stock <= 5:
            return f"🔥 S등급/MINT 최상급 출고 주문 폭주 (현재: {stock}권 / 임계치: {self.SAFETY_STOCK}권)"
        if stock <= 8:
            return f"⚠️ 신기능 입고 도서 재고 부족 경고 (현재: {stock}권 / 임계치: {self.SAFETY_STOCK}권)"
        return f"📈 교재/도서 정기 자동 재발주 권장 (현재: {stock}권 / 권장: +{recommended_qty}권)"

    def _generate_reasons_llm(self, candidates: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        수량/긴급도는 이미 결정론적으로 계산된 뒤이므로(candidates에 포함), LLM은 그 사유 문장만
        생성한다 - 재고 수량 같은 비즈니스 숫자를 LLM이 새로 판단/환각하게 두지 않는다.
        모든 후보를 한 번의 배치 호출로 처리해 도서 수만큼 API를 반복 호출하지 않는다
        (/po/suggested는 페이지 로드마다 호출될 수 있는 GET 엔드포인트라 N회 동기 LLM 호출은
        지연/비용 문제가 크다).
        """
        if not _reason_llm or not candidates:
            return {}
        try:
            structured = _reason_llm.with_structured_output(_ReasonBatch)
            prompt = f"""당신은 B2B 도서 물류센터의 재고 담당 AI입니다.
아래 도서별 재고 현황을 보고, 각 도서마다 왜 재발주가 필요한지 한국어 한 문장으로 설명하세요.
이모지 1개로 시작하고(위험도가 높을수록 🚨, 중간이면 🔥/⚠️, 낮으면 📈 등), 현재 재고 수량과
안전 재고 임계치({self.SAFETY_STOCK}권)를 문장에 포함하세요. 수량 계산은 이미 끝났으니
사유 문장만 작성하고, title은 입력받은 문자열과 정확히 동일하게 반환하세요.

도서 목록(JSON): {json.dumps(candidates, ensure_ascii=False)}
"""
            result: _ReasonBatch = structured.invoke([HumanMessage(content=prompt)])
            return {item.title: item.reasoning for item in result.items}
        except Exception as e:
            print(f"[PO Service] LLM 사유 생성 실패, 결정론적 템플릿으로 폴백: {e}")
            return {}

    def get_suggested_po(self, db: Session) -> List[Dict[str, Any]]:
        statement = select(Book).order_by(Book.virtual_stock.asc()).limit(15)
        books = db.exec(statement).all()

        output = []
        if books:
            candidates = []
            rows = []
            for idx, b in enumerate(books):
                stock = b.virtual_stock if (b.virtual_stock is not None and b.virtual_stock >= 0) else ((idx * 3 + 2) % 10)
                recommended_qty = max(10, self.TARGET_STOCK - stock)
                base_price = b.base_price if (b.base_price and b.base_price > 0) else 25000.0
                unit_cost = int(base_price * 0.6)  # Wholesale price (60%)
                estimated_cost = unit_cost * recommended_qty
                urgency = self._urgency(stock)
                trigger_date = b.updated_at.strftime("%Y-%m-%d %H:%M") if b.updated_at else "2026-07-31 09:00"

                rows.append({
                    "id": f"PO-20260731-{str(idx + 1).zfill(2)}",
                    "book_id": str(b.id), "isbn": b.isbn, "title": b.title,
                    "author": b.author or "저자 미상", "publisher": b.publisher or "출판사 미상",
                    "currentStock": stock, "safetyStock": self.SAFETY_STOCK,
                    "recommendedQty": recommended_qty, "estimatedCost": estimated_cost,
                    "urgency": urgency, "status": "PENDING", "triggerDate": trigger_date,
                    "_fallback_reason": self._fallback_reason(stock, recommended_qty),
                })
                candidates.append({"title": b.title, "current_stock": stock, "urgency": urgency, "recommended_qty": recommended_qty})

            llm_reasons = self._generate_reasons_llm(candidates)
            for row in rows:
                row["reason"] = llm_reasons.get(row["title"], row.pop("_fallback_reason"))
                row.pop("_fallback_reason", None)
                output.append(row)
            return output

        # Fallback if DB has no books
        for idx, item in enumerate(self.SEED_PO_BOOKS):
            output.append({
                "id": f"PO-20260731-{str(idx + 1).zfill(2)}",
                "book_id": f"seed-book-{idx + 1}",
                "isbn": item["isbn"], "title": item["title"], "author": "Nexus AI Engine",
                "publisher": item["publisher"], "currentStock": item["stock"], "safetyStock": self.SAFETY_STOCK,
                "recommendedQty": item["qty"], "estimatedCost": item["cost"],
                "urgency": "CRITICAL" if item["stock"] < 5 else "HIGH",
                "reason": "AI 가상 재고 고갈 경고 (긴급도: CRITICAL)",
                "status": "PENDING", "triggerDate": "2026-07-31 09:00"
            })
        return output

    def deduct_stock_simulation(self, db: Session, book_id: str, deduct_qty: int) -> Dict[str, Any]:
        try:
            book_uuid = UUID(book_id)
            book_item = db.get(Book, book_uuid)
            if book_item:
                book_item.virtual_stock = max(0, (book_item.virtual_stock or 10) - deduct_qty)
                db.add(book_item)
                db.commit()
                db.refresh(book_item)
                return {
                    "message": "success", "book_id": str(book_item.id), "title": book_item.title,
                    "remaining_stock": book_item.virtual_stock, "deducted_qty": deduct_qty,
                    "po_trigger_needed": True
                }
        except Exception as e:
            print(f"Deduct stock simulation error: {e}")

        return {"message": "success", "remaining_stock": 3, "po_trigger_needed": True}

    def approve_po(self, db: Session, book_ids: List[str]) -> Dict[str, Any]:
        created_orders = []
        created_inventories = []

        loc_stmt = select(Location).where(Location.zone == "Zone A").limit(1)
        zone_a_loc = db.exec(loc_stmt).first()
        if not zone_a_loc:
            zone_a_loc = db.exec(select(Location).limit(1)).first()

        for book_id_str in book_ids:
            try:
                if book_id_str.startswith("seed-book"):
                    continue
                book_uuid = UUID(book_id_str)
                book_item = db.get(Book, book_uuid)
                if not book_item:
                    continue

                curr_stock = book_item.virtual_stock if book_item.virtual_stock is not None else 5
                rec_qty = max(10, self.TARGET_STOCK - curr_stock)
                base_price = book_item.base_price if (book_item.base_price and book_item.base_price > 0) else 25000.0
                cost = int(base_price * 0.6 * rec_qty)

                new_order = Order(
                    customer_name="Nexus AI Auto PO (자동발주)",
                    type=OrderTypeEnum.AUTO_PO.value,
                    total_price=float(cost),
                    status=OrderStatusEnum.COMPLETED.value
                )
                db.add(new_order)
                db.commit()
                db.refresh(new_order)
                created_orders.append(str(new_order.id))

                book_item.virtual_stock = curr_stock + rec_qty
                db.add(book_item)

                if zone_a_loc:
                    new_inv = Inventory(
                        lpn_barcode=f"LPN-PO-{str(uuid4())[:8].upper()}",
                        book_id=book_item.id,
                        location_id=zone_a_loc.id,
                        grade=GradeEnum.MINT,
                        ubci_score=100.0,
                        quantity=rec_qty
                    )
                    db.add(new_inv)
                    db.commit()
                    db.refresh(new_inv)
                    created_inventories.append(str(new_inv.lpn_barcode))
            except Exception as e:
                print(f"Error processing inventory inbound: {e}")

        return {
            "message": "success",
            "approved_count": len(book_ids),
            "created_order_ids": created_orders,
            "created_lpns": created_inventories
        }

    def cancel_po(self, book_ids: List[str]) -> Dict[str, Any]:
        return {"message": "cancelled", "cancelled_count": len(book_ids)}


po_service = POService()
