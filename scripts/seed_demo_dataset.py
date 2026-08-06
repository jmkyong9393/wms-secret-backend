# -*- coding: utf-8 -*-
"""
데모 영상 촬영용 원클릭 시딩 스크립트 (2026-08-04).

실행:  .venv/Scripts/python.exe scripts/seed_demo_dataset.py
반복 실행 안전(idempotent): '(데모)' / 'LPN-260731-' 마커가 붙은 이전 시드를 먼저 지우고 다시 심는다.
실촬영 항목(입고 스캔, FDS 스캔, 주간 인사이트 생성 등)은 시드하지 않고 라이브로 남긴다.

[2026-08-06 변경] LPN 형식을 운영과 동일한 `LPN-YYMMDD-{존}{순번3자리}`로 통일했다.
종전 'LPN-DEMO-{일}{i}-{해시4}' 형식은 뒤 4자리가 순번이 아니어서 존·순번 파싱이 필요한
화면과 채번 로직에서 예외를 탔다. 날짜를 과거 날짜 260731로 고정해 정상 운영 채번
(오늘 날짜)과 절대 겹치지 않게 하고, 이 날짜가 종전 'DEMO' 문자열의 시드 마커 역할을
그대로 이어받는다.
Zone A는 조장 실촬영 전용으로 예약돼 있어 시드에서 제외한다(생성/실측 데이터 혼입 방지).

시드 내용:
  1) 대시보드 배경 물량: 최근 7일 분산 중고 재고 + 완료 주문 (차트가 그림이 되도록)
  2) 검수 이력: APPROVED 10건 / REJECTED 2건 (승인율 KPI가 건강하게 보이도록)
  3) HITL 대기 2건: supervisor_rationale/제안등급/실촬영 이미지 포함 (관리자 결재 장면 확정용)
  4) 출고 시연용 PENDING 주문 2건 (신품+중고 혼합 order_items) - 피킹 지시서 생성은 라이브
  5) 이번 주 weekly_insights 캐시 삭제 (촬영 중 Insight Agent 라이브 생성 장면용)
"""
import sys
import random
from collections import defaultdict
from datetime import timedelta

sys.path.insert(0, ".")

from sqlalchemy import create_engine, text
from sqlmodel import Session, select
from app.models.wms import (
    Book, Location, Order, OrderItem, InventoryUsedItem, ReturnJob, now_kst,
)

DB_URL = "postgresql://admin:password@localhost:5432/wms_db"
engine = create_engine(DB_URL)

SAMPLE_IMAGES = [
    f"http://localhost:8000/experiment_data/job-0c2929a0/raw_{i}.jpg" for i in range(5)
]

random.seed(20260804)  # 재실행 시 동일 데이터 (촬영 리허설 재현성)

# 데모 시드 LPN 네임스페이스. 과거 날짜라 오늘 날짜로 채번하는 정상 운영과 겹치지 않는다.
SEED_LPN_DATE = "260731"
# Zone A는 조장 실촬영 전용이라 제외한다.
SEED_LOCATIONS = [("B", "1", "1"), ("B", "2", "1"), ("C", "1", "1"), ("D", "1", "1")]

_lpn_seq: dict[str, int] = defaultdict(int)


def next_lpn(zone: str) -> str:
    """`LPN-260731-B001` 형태로 존별 순번을 채번한다 (운영 채번과 동일 포맷)."""
    _lpn_seq[zone] += 1
    return f"LPN-{SEED_LPN_DATE}-{zone}{_lpn_seq[zone]:03d}"


def get_or_create_location(s: Session, zone: str, rack: str, shelf: str) -> Location:
    barcode = f"LOC-{zone}-{rack}-{shelf}"
    loc = s.exec(select(Location).where(Location.barcode == barcode)).first()
    if not loc:
        loc = Location(zone=zone, rack=rack, shelf=shelf, barcode=barcode)
        s.add(loc)
        s.commit()
        s.refresh(loc)
    return loc


def main() -> None:
    with Session(engine) as s:
        now = now_kst()

        # ---------- 0) 이전 데모 시드 정리 ----------
        s.exec(text("DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE customer_name LIKE '(데모)%')"))
        s.exec(text("DELETE FROM orders WHERE customer_name LIKE '(데모)%'"))
        s.exec(text("DELETE FROM inventory_used_items WHERE lpn_barcode LIKE :m"),
               params={"m": f"LPN-{SEED_LPN_DATE}-%"})
        s.exec(text("DELETE FROM return_jobs WHERE agent_logs->>'lpn_barcode' LIKE :m"),
               params={"m": f"LPN-{SEED_LPN_DATE}-%"})
        iso = now.isocalendar()
        s.exec(text("DELETE FROM weekly_insights WHERE report_week = :w"),
               params={"w": f"{iso[0]}-W{iso[1]:02d}"})
        s.commit()
        print("[0] 이전 데모 시드 정리 + 이번 주 인사이트 캐시 삭제 완료")

        # ---------- 준비: 도서 풀 ----------
        books = s.exec(select(Book).where(Book.is_active == True).limit(12)).all()  # noqa: E712
        if len(books) < 4:
            print(f"!! 도서가 {len(books)}권뿐입니다. 먼저 Fast-Track으로 몇 권 입고해 주세요.")
            return
        locs = [get_or_create_location(s, *z) for z in SEED_LOCATIONS]

        # ---------- 1) 7일 분산 중고 재고 + 완료 주문 ----------
        used_count, order_count = 0, 0
        for day in range(7):
            day_ts = now - timedelta(days=day, hours=random.randint(1, 8))
            for _ in range(random.randint(2, 4)):
                b = random.choice(books)
                score = random.choice([96, 92, 88, 85, 78, 72, 68])
                grade = "MINT" if score >= 95 else "GOOD" if score >= 85 else "NORMAL"
                # LPN의 존 문자는 실제 적재 로케이션과 일치해야 한다 (먼저 뽑고 파생)
                loc = random.choice(locs)
                s.add(InventoryUsedItem(
                    book_id=b.id,
                    location_id=loc.id,
                    lpn_barcode=next_lpn(loc.zone),
                    ubci_score=score,
                    condition_grade=grade,
                    item_status="IN_STOCK",
                    inspection_source="AI_AUTO",
                    inspected_by="AI 자동 판정 (Nexus Vision AI)",
                    created_at=day_ts,
                ))
                used_count += 1
            for _ in range(random.randint(1, 2)):
                s.add(Order(
                    customer_name=f"(데모){random.choice(['교보문고 B2B', '영풍문고 종로', 'YES24 직영'])}",
                    type="B2B_ORDER",
                    total_price=random.randint(8, 40) * 10000,
                    status="SHIPPED",
                    created_at=day_ts,
                ))
                order_count += 1
        s.commit()
        print(f"[1] 7일 분산 시드: 중고 재고 {used_count}건 / 완료 주문 {order_count}건")

        # ---------- 2) 검수 이력 (KPI용) ----------
        for i in range(12):
            b = random.choice(books)
            approved = i < 10
            score = random.choice([90, 86, 82, 75]) if approved else random.choice([40, 55])
            s.add(ReturnJob(
                book_id=b.id,
                status="APPROVED" if approved else "REJECTED",
                ubci_score=score,
                image_urls=SAMPLE_IMAGES[:2],
                agent_logs={
                    "lpn_barcode": next_lpn(random.choice(locs).zone),
                    "suggested_grade": "GOOD" if approved else "REJECT",
                    "reason_code": "OK",
                    "supervisor_decision": "ISSUE_REPORT",
                },
                created_at=now - timedelta(days=random.randint(0, 6), hours=random.randint(0, 10)),
            ))
        s.commit()
        print("[2] 검수 이력 시드: APPROVED 10 / REJECTED 2")

        # ---------- 3) HITL 대기 2건 (결재 장면 확정용) ----------
        hitl_specs = [
            (62, "DMG_INT_STAIN", "내지 오염/이물질", 2),
            (60, "DMG_EXT_TEAR", "커버 모서리 미세 찢어짐", 3),
        ]
        for idx, (score, code, label, img_idx) in enumerate(hitl_specs, start=1):
            b = books[idx]
            s.add(ReturnJob(
                book_id=b.id,
                status="HITL_REQUIRED",
                ubci_score=score,
                image_urls=SAMPLE_IMAGES,
                agent_logs={
                    "lpn_barcode": next_lpn(random.choice(locs).zone),
                    "reason_code": "AWAITING_HUMAN_REVIEW",
                    "suggested_grade": "NORMAL",
                    "primary_reason_code": code,
                    "supervisor_decision": "ESCALATE_HUMAN",
                    "supervisor_rationale": (
                        f"Critic 애매성 보고(BOUNDARY_AMBIGUOUS_HITL). Vision 결함 1건 / "
                        f"Policy UBCI {score}점(경계선 58~66)으로는 자동 확정이 부적절하여 "
                        f"관리자 수동 결재로 이관 결정."
                    ),
                    "defects": [{
                        "type": code, "ratio": 6, "confidence": 0.71,
                        "image_index": img_idx, "preliminary_deduction": 15,
                        "bbox": {"xmin": 120, "ymin": 210, "xmax": 380, "ymax": 330},
                        "description": label,
                    }],
                },
                created_at=now - timedelta(hours=idx),
            ))
        s.commit()
        print("[3] HITL 대기 2건 시드 (경계선 62/60점, Supervisor 이관 사유 포함)")

        # ---------- 4) 출고 시연용 PENDING 주문 ----------
        # 중고 order_item이 스캔 매칭할 IN_STOCK LPN 확보
        demo_used = s.exec(
            select(InventoryUsedItem)
            .where(InventoryUsedItem.lpn_barcode.like(f"LPN-{SEED_LPN_DATE}-%"),
                   InventoryUsedItem.item_status == "IN_STOCK")
            .limit(2)
        ).all()
        for oi, u in enumerate(demo_used, start=1):
            o = Order(
                customer_name=f"(데모)출고시연 {oi}차 - 알라딘 B2B",
                type="B2B_ORDER",
                total_price=0,
                status="PENDING",
                created_at=now - timedelta(minutes=30 * oi),
            )
            s.add(o)
            s.commit()
            s.refresh(o)
            ub = s.get(Book, u.book_id)
            nb = random.choice(books)
            s.add(OrderItem(order_id=o.id, book_id=u.book_id, quantity=1,
                            unit_price=(ub.base_price or 20000) * 0.6, condition_pref="USED"))
            s.add(OrderItem(order_id=o.id, book_id=nb.id, quantity=2,
                            unit_price=(nb.base_price or 20000) * 0.6, condition_pref="NEW"))
            s.commit()
        print(f"[4] 출고 시연용 PENDING 주문 {len(demo_used)}건 (신품+중고 혼합 order_items)")

        print()
        print("=== 시딩 완료. 라이브로 남겨둔 것 ===")
        print("  - 입고 스캔/Fast-Track/AI 검수: 실물 책으로 촬영")
        print("  - FDS [전체 스캔 실행]: /admin/fds 에서 라이브 (Analyst Agent 실시간 생성)")
        print("  - 주간 인사이트: 대시보드 첫 진입 시 Insight Agent 라이브 생성")
        print("  - 피킹 지시서 발행: /admin/orders 에서 (데모)출고시연 주문으로 라이브")


if __name__ == "__main__":
    main()
