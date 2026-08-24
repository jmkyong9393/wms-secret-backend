from datetime import timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import Session, select, func

from app.core.exceptions import BadRequestException
from app.core.settings_service import (
    DEFAULT_SAFETY_STOCK_THRESHOLD,
    SAFETY_STOCK_SETTING_KEY,
    get_int_setting,
)
from app.models.wms import (
    Book,
    InventoryLog,
    InventoryUsedItem,
    Order,
    OrderItem,
    OrderProposal,
    OrderStatusEnum,
    OrderTypeEnum,
    now_kst,
)


class POService:
    """
    PO(Purchase Order/자동 발주) 도메인 비즈니스 로직 - SCM 칸반보드의 백엔드.

    [구조 - 2026-08-04 하드코딩 제거 리팩토링]
    - 제안 생성: Restock 판정 그래프(app/ai/agents/restock.py)가 반려 이벤트(워커 훅) 또는
      저재고 스캔으로 order_proposals에 PENDING 카드를 적재한다. 과거처럼 GET 요청마다
      시드 카탈로그/가상 수치로 목록을 지어내지 않는다 (LLM 호출도 write-time으로 이동).
    - 집행: 관리자가 칸반에서 승인하면 이 서비스가 Order(AUTO_PO) 생성 + 신품 Fast-Track
      입고(Zone A upsert + virtual_stock 가산)를 집행한다. 신품은 LPN을 발급하지 않는다 -
      "발주 승인 즉시 MINT 중고 LPN 생성"이던 기존 오류 경로는 제거되었다.
    """

    SCAN_LIMIT = 8               # 스캔 1회당 최대 제안 생성 수 (LLM 비용 상한)
    # [2026-08-09 리팩토링] 저재고 스캔 기준(신품+중고 합산 가용 재고)이 코드 상수라 UI에서
    # 바꿀 수 없었다 (app/ai/agents/restock.py의 발주수량 바닥값 MIN_SAFETY_STOCK과 서로
    # 다른 값으로 따로 놀던 버그도 이게 원인). system_settings 테이블의 단일 값으로 통합했다
    # - GET/PUT /api/v1/admin/settings, app/core/settings_service.py 참고.

    # ------------------------------------------------------------------
    # 조회 (칸반보드)
    # ------------------------------------------------------------------

    def list_proposals(self, db: Session, status: Optional[str] = None) -> List[Dict[str, Any]]:
        statement = select(OrderProposal).order_by(OrderProposal.created_at.desc())
        if status:
            statement = statement.where(OrderProposal.status == status.upper())
        proposals = db.exec(statement.limit(200)).all()

        book_ids = {p.book_id for p in proposals}
        books = {}
        if book_ids:
            for b in db.exec(select(Book).where(Book.id.in_(book_ids))).all():
                books[b.id] = b

        return [self._to_card(p, books.get(p.book_id)) for p in proposals]

    def _to_card(self, p: OrderProposal, book: Optional[Book]) -> Dict[str, Any]:
        """칸반 카드 1장의 프론트 응답 형태 (camelCase)."""
        return {
            "id": str(p.id),
            "bookId": str(p.book_id),
            "isbn": p.isbn,
            "title": p.title,
            "author": (book.author if book else None) or "저자 미상",
            "publisher": (book.publisher if book else None) or "출판사 미상",
            "coverImageUrl": book.cover_image_url if book else None,
            "triggerType": p.trigger_type,
            "rejectReasonCode": p.reject_reason_code,
            "currentStock": p.current_stock,
            "salesVelocity30d": p.sales_velocity_30d,
            "rejectedQuantity": p.rejected_quantity,
            "baselineQuantity": p.baseline_quantity,
            "proposedQuantity": p.proposed_quantity,
            "urgency": p.urgency,
            "reasoning": p.reasoning,
            "aiSource": p.ai_source,
            "unitCost": p.unit_cost,
            "estimatedCost": p.estimated_cost,
            "status": p.status,
            "decidedBy": p.decided_by,
            "decidedAt": p.decided_at.isoformat() if p.decided_at else None,
            "orderId": str(p.order_id) if p.order_id else None,
            "createdAt": p.created_at.isoformat() if p.created_at else None,
        }

    # ------------------------------------------------------------------
    # 결재 (승인 / 기각)
    # ------------------------------------------------------------------

    def approve_proposals(
        self, db: Session, proposal_ids: List[str], decided_by: str, worker_employee_id: str = None
    ) -> Dict[str, Any]:
        """
        제안 승인 집행: Order(AUTO_PO)+OrderItem 생성 → 신품 Fast-Track 입고.
        데모 환경에서는 도매 리드타임을 0으로 압축하므로 승인 즉시 입고까지 완료 처리한다.
        """
        from app.domains.inventory.service import fasttrack_new_stock_inbound

        approved, skipped = [], []
        for pid in proposal_ids:
            proposal = self._get_pending(db, pid)
            if not proposal:
                skipped.append(pid)
                continue
            book = db.get(Book, proposal.book_id)
            if not book:
                skipped.append(pid)
                continue

            qty = max(1, int(proposal.proposed_quantity or 0))
            total_price = float(proposal.unit_cost or 0.0) * qty

            new_order = Order(
                customer_name="Nexus AI Auto PO (자동발주)",
                type=OrderTypeEnum.AUTO_PO.value,
                total_price=total_price,
                status=OrderStatusEnum.SHIPPED.value,  # 리드타임 0 압축 - 발주와 동시에 입고 완료
            )
            db.add(new_order)
            db.flush()
            db.add(OrderItem(
                order_id=new_order.id,
                book_id=book.id,
                quantity=qty,
                unit_price=float(proposal.unit_cost or 0.0),
                condition_pref="NEW",
            ))

            # 신품 입고는 현장 스캔 입고와 동일한 Fast-Track 관문을 통과한다 (LPN 미발급).
            # 이 경로는 현장 촬영이 아니라 발주 결재로 들어오는 입고이므로 결재자를 기록한다.
            # worker_id는 사번만 넣는다 - 표시용 라벨(`WM2608001 (장문경)`)을 넣으면 사용자
            # 조회가 실패해 화면에 그 문자열이 그대로 노출된다.
            fasttrack_new_stock_inbound(db, book, qty, worker_id=worker_employee_id or None)

            proposal.status = "APPROVED"
            proposal.decided_by = decided_by
            proposal.decided_at = now_kst()
            proposal.order_id = new_order.id
            proposal.updated_at = now_kst()
            db.add(proposal)
            db.commit()
            db.refresh(proposal)
            approved.append({
                "proposalId": str(proposal.id),
                "orderId": str(new_order.id),
                "title": book.title,
                "quantity": qty,
                "zone": "A-1-1",
            })

        return {
            "status": "success",
            "approvedCount": len(approved),
            "approved": approved,
            "skipped": skipped,
        }

    def dismiss_proposals(self, db: Session, proposal_ids: List[str], decided_by: str) -> Dict[str, Any]:
        dismissed, skipped = [], []
        for pid in proposal_ids:
            proposal = self._get_pending(db, pid)
            if not proposal:
                skipped.append(pid)
                continue
            proposal.status = "DISMISSED"
            proposal.decided_by = decided_by
            proposal.decided_at = now_kst()
            proposal.updated_at = now_kst()
            db.add(proposal)
            dismissed.append(str(proposal.id))
        db.commit()
        return {"status": "success", "dismissedCount": len(dismissed), "skipped": skipped}

    def delete_proposals(self, db: Session, proposal_ids: List[str]) -> Dict[str, Any]:
        """
        결재가 끝난 제안 카드를 보드에서 삭제한다.

        PENDING 카드는 삭제하지 않는다. 결재 대기 건을 지우면 승인도 기각도 아닌 채로
        기록이 사라져, 왜 발주하지 않았는지 설명할 근거가 남지 않는다. 보드에서 치우려면
        먼저 기각(DISMISSED) 처리해야 한다.
        """
        deleted, skipped = [], []
        for pid in proposal_ids:
            try:
                parsed = UUID(pid)
            except (ValueError, TypeError):
                raise BadRequestException(f"Invalid proposal id format: {pid}")

            proposal = db.get(OrderProposal, parsed)
            if not proposal or proposal.status == "PENDING":
                skipped.append(pid)
                continue

            db.delete(proposal)
            deleted.append(pid)

        db.commit()
        return {"status": "success", "deletedCount": len(deleted), "skipped": skipped}

    def _get_pending(self, db: Session, proposal_id: str) -> Optional[OrderProposal]:
        try:
            parsed = UUID(proposal_id)
        except (ValueError, TypeError):
            raise BadRequestException(f"Invalid proposal id format: {proposal_id}")
        proposal = db.get(OrderProposal, parsed)
        if not proposal or proposal.status != "PENDING":
            return None
        return proposal

    # ------------------------------------------------------------------
    # 저재고 스캔 (수동 트리거)
    # ------------------------------------------------------------------

    def scan_safety_stock(self, db: Session) -> Dict[str, Any]:
        """
        수요 이력이 있는 도서만 대상으로 Restock 판정 그래프로 제안을 생성한다.

        1순위(최상단): 가용 재고 0 + 출고 이력 존재(기간 무관) - 품절로 출고가 끊겨
          30일 윈도에서 수요 신호가 소멸한 도서. 판매 기회가 새는 중이므로 최우선.
        2순위: 재고 >0 + 최근 30일 OUTBOUND 이력 + 안전선(수요 도서의 최소 보충선) 미만.
        제외: 출고 이력이 전무한 도서(등록만 된 책) - 수요 근거가 없다.

        이미 PENDING 카드가 있는 도서는 건너뛰어 칸반 중복을 막고,
        1회 스캔당 생성 수를 제한해 LLM 비용을 상한한다.
        """
        from app.ai.agents.restock import generate_and_store_proposal

        pending_book_ids = set(db.exec(
            select(OrderProposal.book_id).where(OrderProposal.status == "PENDING")
        ).all())

        used_counts = dict(db.exec(
            select(InventoryUsedItem.book_id, func.count(InventoryUsedItem.id))
            .where(InventoryUsedItem.item_status == "IN_STOCK")
            .group_by(InventoryUsedItem.book_id)
        ).all())

        # 실보유 수량은 Inventory 합으로 계산한다. Book.virtual_stock은
        # 위치 없는 중복 기록이라 실재고를 반영하지 못했다.
        from app.domains.inventory.service import get_new_stock_map

        new_stock_map = get_new_stock_map(db)

        since = now_kst() - timedelta(days=30)
        recent_demand_ids = set(db.exec(
            select(InventoryLog.book_id).distinct().where(
                InventoryLog.transaction_type == "OUTBOUND",
                InventoryLog.created_at >= since,
            )
        ).all())
        ever_sold_ids = set(db.exec(
            select(InventoryLog.book_id).distinct().where(
                InventoryLog.transaction_type == "OUTBOUND",
            )
        ).all())

        safety_stock_threshold = get_int_setting(
            db, SAFETY_STOCK_SETTING_KEY, DEFAULT_SAFETY_STOCK_THRESHOLD
        )

        def _available(book: Book) -> int:
            return new_stock_map.get(book.id, 0) + int(used_counts.get(book.id, 0))

        # 후보 = (재고 0 ∧ 전 기간 출고 이력) ∪ (30일 출고 이력 ∧ 안전선 미만).
        # 품절 복구(전자)를 최상단에 두어 SCAN_LIMIT에 밀려나지 않게 한다.
        candidates = []
        for book in db.exec(select(Book).where(Book.is_active == True)).all():
            available = _available(book)
            if available == 0 and book.id in ever_sold_ids:
                candidates.append((0, available, book))
            elif book.id in recent_demand_ids and available < safety_stock_threshold:
                candidates.append((1, available, book))
        candidates.sort(key=lambda t: (t[0], t[1]))

        created = []
        for _, _, book in candidates:
            if len(created) >= self.SCAN_LIMIT:
                break
            if book.id in pending_book_ids:
                continue
            proposal = generate_and_store_proposal(
                db, book,
                trigger_type="SAFETY_STOCK",
            )
            if proposal:
                created.append({
                    "proposalId": str(proposal.id),
                    "title": book.title,
                    "currentStock": proposal.current_stock,
                    "proposedQuantity": proposal.proposed_quantity,
                    "urgency": proposal.urgency,
                })

        return {"status": "success", "createdCount": len(created), "created": created}


po_service = POService()
