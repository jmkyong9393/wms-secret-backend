# -*- coding: utf-8 -*-
"""
쇼케이스 데모 시드 주입기 — 실행 즉시 고품질 합성 데이터를 주입한다.

무엇을 넣는가 (전부 마커 포함, 멱등 실행 안전):
  1) 7일 분산 중고 재고(IN_STOCK) + 동일 LPN으로 연결된 APPROVED 검수 기록
     - 로케이션은 하드코딩이 아니라 현행 배정 함수(recommend_optimal_warehouse_zone)를
       직접 호출해 결정한다 → 등급=Zone / 카테고리=Rack / 판형=Shelf, 라이브 로직과 항상 일치
     - 검수 기록에는 S3 실재 촬영 세트(CloudFront URL)와 defects(BBox 포함) agent_logs가 붙어
       재고 상세 화면의 이미지·진단 기록이 실제 검수 건과 동일하게 렌더링된다
  2) REJECTED 검수 기록 2건 (반려는 재고를 만들지 않는다)
  3) HITL 대기 2건 — 촬영 규격(앞/뒤/책등 3컷 이상) 실이미지 세트만 사용, 5건 이하 유지
  4) (데모) 마커 완료 주문 + 출고 시연용 PENDING 주문 2건
  5) 이번 주 weekly_insights 캐시 삭제 → 다음 대시보드 조회 때 라이브 재집계

마커 (삭제는 purge_showcase_demo.py):
  - 재고/검수기록: LPN 접두사 "LPN-260731-"  (과거 날짜 네임스페이스 — 운영 채번과 절대 충돌 없음)
  - 주문: customer_name 접두사 "(데모)"

실행:
  로컬:      python -m scripts.seed.seed_showcase_demo
  프로덕션:  (api 파드에서) cd /app && python scripts/seed/seed_showcase_demo.py
  DB 대상은 DATABASE_URL 환경변수를 따르고, 없으면 로컬 개발 DB를 쓴다.
"""
import os
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
from app.domains.inventory.service import recommend_optimal_warehouse_zone

DB_URL = os.environ.get("DATABASE_URL", "postgresql://admin:password@localhost:5432/wms_db")
engine = create_engine(DB_URL)

# ---- 마커 (purge 스크립트와 반드시 동일하게 유지) ----
SEED_LPN_PREFIX = "LPN-260731-"
SEED_ORDER_PREFIX = "(데모)"

# S3 버킷에 실재하는 촬영 세트만 사용한다. (프리픽스, 컷 수, 사진 속 실물 도서의 ISBN)
# 사진과 도서 정보가 반드시 일치해야 하므로 세트마다 ISBN을 고정 매핑한다 (전 세트 육안 식별 완료).
CF = "https://deao4fid6qoyp.cloudfront.net"
REAL_IMAGE_SETS = [
    ("inbound/20260806/job-9108363b", 5, "9788970507385"),  # 쉽게 풀어쓴 C언어 Express 개정판
    ("inbound/20260805/job-300067ef", 3, "9791156643050"),  # 컴퓨팅 사고력을 키우는 이산수학 개정판
    ("inbound/20260804/job-4f465c22", 4, "9791185553658"),  # 진짜 코딩하며 배우는 라즈베리파이 4
    ("inbound/20260804/job-6a374aa5", 5, "9791185553832"),  # AI 인공지능 자율주행 자동차
    ("inbound/20260804/job-e041720d", 5, "9788988474846"),  # SQL 자격검정 실전문제
    ("inbound/20260806/job-15b175b6", 3, "9791196461713"),  # 알기쉬운 선형대수 개정11판
    ("inbound/20260804/job-505a12e3", 3, "9791185553832"),  # AI 자율주행 (별도 촬영 세트)
    ("inbound/20260804/job-bbf60915", 3, "9791185553832"),  # AI 자율주행 (별도 촬영 세트)
]

random.seed(20260809)  # 재실행 시 동일 데이터 (촬영 리허설 재현성)
_lpn_seq: dict = defaultdict(int)


def set_urls(prefix: str, n: int) -> list:
    return [f"{CF}/{prefix}/raw_{i}.jpg" for i in range(n)]


def next_lpn(zone: str) -> str:
    _lpn_seq[zone] += 1
    return f"{SEED_LPN_PREFIX}{zone}{_lpn_seq[zone]:03d}"


def get_or_create_location(s: Session, zone: str, rack: str, shelf: str) -> Location:
    barcode = f"LOC-{zone}-{rack}-{shelf}"
    loc = s.exec(select(Location).where(Location.barcode == barcode)).first()
    if not loc:
        loc = Location(zone=zone, rack=rack, shelf=shelf, barcode=barcode)
        s.add(loc); s.commit(); s.refresh(loc)
    return loc


DEFECT_POOL = [
    ("DMG_EDGE_WEAR", "모서리 마모", 4),
    ("DMG_EXT_SCRATCH", "표지 미세 긁힘", 3),
    ("DMG_EXT_STICKER", "스티커 제거 자국", 3),
    ("DMG_INT_STAIN", "내지 경미한 얼룩", 5),
]


def build_agent_logs(lpn, grade, score, images_n, decision="ISSUE_REPORT", reason="OK"):
    logs = {
        "lpn_barcode": lpn,
        "suggested_grade": grade,
        "reason_code": reason,
        "supervisor_decision": decision,
        "summary": f"AI 자동 판정 완료 — UBCI {score}점 · {grade} 등급 확정",
    }
    if score < 95:
        n = 1 if score >= 85 else 2
        defects = []
        for i in range(n):
            code, label, ratio = DEFECT_POOL[(score + i) % len(DEFECT_POOL)]
            defects.append({
                "type": code, "ratio": ratio, "confidence": round(0.62 + 0.07 * i, 2),
                "image_index": i % images_n,
                "preliminary_deduction": 100 - score if n == 1 else (100 - score) // n,
                "bbox": {"xmin": 140 + 60 * i, "ymin": 220 + 40 * i,
                         "xmax": 390 + 60 * i, "ymax": 340 + 40 * i},
                "description": label,
            })
        logs["defects"] = defects
    return logs


def purge(s: Session) -> None:
    """이전 시드 정리 (마커 기준) — purge_showcase_demo.py와 동일 로직."""
    s.exec(text("DELETE FROM order_items WHERE order_id IN "
                "(SELECT id FROM orders WHERE customer_name LIKE :m)"),
           params={"m": f"{SEED_ORDER_PREFIX}%"})
    s.exec(text("DELETE FROM orders WHERE customer_name LIKE :m"),
           params={"m": f"{SEED_ORDER_PREFIX}%"})
    s.exec(text("DELETE FROM inventory_used_items WHERE lpn_barcode LIKE :m"),
           params={"m": f"{SEED_LPN_PREFIX}%"})
    s.exec(text("DELETE FROM return_jobs WHERE agent_logs->>'lpn_barcode' LIKE :m"),
           params={"m": f"{SEED_LPN_PREFIX}%"})
    iso = now_kst().isocalendar()
    s.exec(text("DELETE FROM weekly_insights WHERE report_week = :w"),
           params={"w": f"{iso[0]}-W{iso[1]:02d}"})
    s.commit()


def main() -> None:
    with Session(engine) as s:
        now = now_kst()

        purge(s)
        print("[0] 기존 시드 정리(멱등) + 이번 주 인사이트 캐시 삭제")

        # 검수 이미지와 도서 정보가 반드시 일치해야 하므로, 표본은 촬영 세트에 찍힌
        # 실물 도서(ISBN 고정 매핑)로만 구성한다.
        book_by_isbn = {}
        for (_, _, isbn) in REAL_IMAGE_SETS:
            if isbn not in book_by_isbn:
                b = s.exec(select(Book).where(Book.isbn == isbn)).first()
                if not b:
                    print(f"!! 카탈로그에 ISBN {isbn} 없음 — 세트 매핑을 확인하세요."); return
                book_by_isbn[isbn] = b

        # 1) 7일 분산 재고 + 동일 LPN 검수 기록 (세트 라운드로빈 — 사진 속 책과 1:1)
        used_count, img_i = 0, 0
        for day in range(7):
            day_ts = now - timedelta(days=day, hours=random.randint(1, 8))
            for _ in range(random.randint(2, 4)):
                prefix, n, isbn = REAL_IMAGE_SETS[img_i % len(REAL_IMAGE_SETS)]
                b = book_by_isbn[isbn]
                score = random.choice([96, 97, 92, 88, 85, 78, 72, 68])
                grade = "MINT" if score >= 95 else "GOOD" if score >= 85 else "NORMAL"
                zone, rack, shelf = recommend_optimal_warehouse_zone(
                    grade=grade, category=b.category_type,
                    base_price=b.base_price, standard_size=b.standard_size)
                loc = get_or_create_location(s, zone, rack, shelf)
                lpn = next_lpn(zone)
                img_i += 1
                # 검수 기록을 먼저 만들고, 재고가 source_job_id로 참조한다
                # (재고 상세 화면이 이 컬럼으로 검수 이미지·agent_logs를 로드한다)
                job = ReturnJob(
                    book_id=b.id, status="APPROVED", ubci_score=score,
                    image_urls=set_urls(prefix, n),
                    agent_logs=build_agent_logs(lpn, grade, score, n),
                    created_at=day_ts,
                )
                s.add(job); s.flush()
                s.add(InventoryUsedItem(
                    book_id=b.id, location_id=loc.id, lpn_barcode=lpn,
                    ubci_score=score, condition_grade=grade, item_status="IN_STOCK",
                    inspection_source="AI_AUTO", inspected_by="AI 자동 판정 (Nexus Vision AI)",
                    source_job_id=job.id,
                    created_at=day_ts,
                ))
                used_count += 1
        s.commit()
        print(f"[1] 재고 {used_count}건 + source_job_id로 연결된 검수기록 {used_count}건")

        # 2) REJECTED 2건
        for _ in range(2):
            prefix, n, isbn = REAL_IMAGE_SETS[img_i % len(REAL_IMAGE_SETS)]; img_i += 1
            b = book_by_isbn[isbn]
            score = random.choice([40, 55])
            s.add(ReturnJob(
                book_id=b.id, status="REJECTED", ubci_score=score,
                image_urls=set_urls(prefix, n),
                agent_logs=build_agent_logs(next_lpn("E"), "REJECT", score, n,
                                            reason="LOW_SCORE_REJECT"),
                created_at=now - timedelta(days=random.randint(0, 6), hours=random.randint(0, 10)),
            ))
        s.commit()
        print("[2] REJECTED 검수기록 2건")

        # 3) HITL 대기 2건 (실이미지 규격 세트, 5건 이하 유지)
        # (점수, 결함코드, 설명, 세트, 결함이 보이는 컷 index) — 세트의 실물 도서로 book 확정
        hitl_specs = [
            (62, "DMG_EDGE_WEAR", "표지 모서리 마모", REAL_IMAGE_SETS[0], 0),   # C언어 Express — 사진에 모서리 닳음 실재
            (64, "DMG_EXT_SCRATCH", "표지 미세 긁힘 의심", REAL_IMAGE_SETS[5], 0),  # 알기쉬운 선형대수 — 경계선 점수
        ]
        for idx, (score, code, label, (prefix, n, isbn), img_idx) in enumerate(hitl_specs, start=1):
            b = book_by_isbn[isbn]
            s.add(ReturnJob(
                book_id=b.id, status="HITL_REQUIRED", ubci_score=score,
                image_urls=set_urls(prefix, n),
                agent_logs={
                    "lpn_barcode": next_lpn("D"),
                    "reason_code": "AWAITING_HUMAN_REVIEW",
                    "suggested_grade": "NORMAL",
                    "primary_reason_code": code,
                    "supervisor_decision": "ESCALATE_HUMAN",
                    "supervisor_rationale": (
                        f"Critic 애매성 보고(BOUNDARY_AMBIGUOUS_HITL). Vision 결함 1건 / "
                        f"Policy UBCI {score}점(경계선 58~66)으로는 자동 확정이 부적절하여 "
                        f"관리자 수동 결재로 이관 결정."),
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
        print("[3] HITL 대기 2건")

        # 4) 주문
        order_count = 0
        for day in range(7):
            for _ in range(random.randint(1, 2)):
                s.add(Order(
                    customer_name=f"{SEED_ORDER_PREFIX}{random.choice(['교보문고 B2B', '영풍문고 종로', 'YES24 직영'])}",
                    type="B2B_ORDER", total_price=random.randint(8, 40) * 10000, status="SHIPPED",
                    created_at=now - timedelta(days=day, hours=random.randint(1, 8)),
                ))
                order_count += 1
        demo_used = s.exec(select(InventoryUsedItem).where(
            InventoryUsedItem.lpn_barcode.like(f"{SEED_LPN_PREFIX}%"),
            InventoryUsedItem.item_status == "IN_STOCK").limit(2)).all()
        for oi, u in enumerate(demo_used, start=1):
            o = Order(customer_name=f"{SEED_ORDER_PREFIX}출고시연 {oi}차 - 알라딘 B2B",
                      type="B2B_ORDER", total_price=0, status="PENDING",
                      created_at=now - timedelta(minutes=30 * oi))
            s.add(o); s.commit(); s.refresh(o)
            ub = s.get(Book, u.book_id); nb = random.choice(list(book_by_isbn.values()))
            s.add(OrderItem(order_id=o.id, book_id=u.book_id, quantity=1,
                            unit_price=(ub.base_price or 20000) * 0.6, condition_pref="USED"))
            s.add(OrderItem(order_id=o.id, book_id=nb.id, quantity=2,
                            unit_price=(nb.base_price or 20000) * 0.6, condition_pref="NEW"))
        s.commit()
        print(f"[4] 완료 주문 {order_count}건 + PENDING 출고시연 {len(demo_used)}건")

        # 5) 교차검증 (필수 — 하나라도 0이 아니면 실패로 간주하고 원인 확인)
        bad_zone = s.exec(text("""
            SELECT count(*) FROM inventory_used_items i JOIN locations l ON l.id=i.location_id
            WHERE i.lpn_barcode LIKE :m AND (
              (i.condition_grade='MINT' AND l.zone<>'B') OR
              (i.condition_grade='GOOD' AND l.zone<>'C') OR
              (i.condition_grade='NORMAL' AND l.zone<>'D'))"""),
            params={"m": f"{SEED_LPN_PREFIX}%"}).scalar()
        unlinked = s.exec(text("""
            SELECT count(*) FROM inventory_used_items i
            WHERE i.lpn_barcode LIKE :m AND (i.source_job_id IS NULL OR NOT EXISTS (
              SELECT 1 FROM return_jobs r WHERE r.id = i.source_job_id))"""),
            params={"m": f"{SEED_LPN_PREFIX}%"}).scalar()
        bad_urls = s.exec(text(
            "SELECT count(*) FROM return_jobs, jsonb_array_elements_text(image_urls) u "
            "WHERE agent_logs->>'lpn_barcode' LIKE :m AND u NOT LIKE :cf"),
            params={"m": f"{SEED_LPN_PREFIX}%", "cf": f"{CF}/%"}).scalar()
        print()
        print("=== 교차검증 ===")
        print(f"  등급↔Zone 불일치: {bad_zone}건 / 검수기록 미연결 재고: {unlinked}건 / "
              f"외부 이미지 참조: {bad_urls}건  (전부 0이어야 정상)")


if __name__ == "__main__":
    main()
