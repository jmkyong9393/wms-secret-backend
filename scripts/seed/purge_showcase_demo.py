# -*- coding: utf-8 -*-
"""
쇼케이스 데모 시드 삭제기 — seed_showcase_demo.py가 넣은 합성 데이터만 마커로 골라 지운다.

삭제 대상 (마커 기준 — 실사용 데이터는 건드리지 않는다):
  - 재고:        lpn_barcode LIKE 'LPN-260731-%'
  - 검수 기록:   agent_logs->>'lpn_barcode' LIKE 'LPN-260731-%'
  - 주문(+품목): customer_name LIKE '(데모)%'
  - 이번 주 weekly_insights 캐시 (다음 대시보드 조회 때 실데이터만으로 재집계)

실행:
  로컬:      python -m scripts.seed.purge_showcase_demo
  프로덕션:  (api 파드에서) cd /app && python scripts/seed/purge_showcase_demo.py
"""
import os
import sys

sys.path.insert(0, ".")

from sqlalchemy import create_engine, text
from sqlmodel import Session

from app.models.wms import now_kst

DB_URL = os.environ.get("DATABASE_URL", "postgresql://admin:password@localhost:5432/wms_db")
engine = create_engine(DB_URL)

# seed_showcase_demo.py와 반드시 동일하게 유지
SEED_LPN_PREFIX = "LPN-260731-"
SEED_ORDER_PREFIX = "(데모)"


def main() -> None:
    with Session(engine) as s:
        r1 = s.exec(text("DELETE FROM order_items WHERE order_id IN "
                         "(SELECT id FROM orders WHERE customer_name LIKE :m)"),
                    params={"m": f"{SEED_ORDER_PREFIX}%"})
        r2 = s.exec(text("DELETE FROM orders WHERE customer_name LIKE :m"),
                    params={"m": f"{SEED_ORDER_PREFIX}%"})
        r3 = s.exec(text("DELETE FROM inventory_used_items WHERE lpn_barcode LIKE :m"),
                    params={"m": f"{SEED_LPN_PREFIX}%"})
        r4 = s.exec(text("DELETE FROM return_jobs WHERE agent_logs->>'lpn_barcode' LIKE :m"),
                    params={"m": f"{SEED_LPN_PREFIX}%"})
        iso = now_kst().isocalendar()
        r5 = s.exec(text("DELETE FROM weekly_insights WHERE report_week = :w"),
                    params={"w": f"{iso[0]}-W{iso[1]:02d}"})
        s.commit()
        print(f"삭제 완료 — 주문품목 {r1.rowcount} / 주문 {r2.rowcount} / "
              f"재고 {r3.rowcount} / 검수기록 {r4.rowcount} / 인사이트캐시 {r5.rowcount}")

        # 교차검증: 마커 잔재가 0이어야 정상
        left_inv = s.exec(text("SELECT count(*) FROM inventory_used_items WHERE lpn_barcode LIKE :m"),
                          params={"m": f"{SEED_LPN_PREFIX}%"}).scalar()
        left_job = s.exec(text("SELECT count(*) FROM return_jobs WHERE agent_logs->>'lpn_barcode' LIKE :m"),
                          params={"m": f"{SEED_LPN_PREFIX}%"}).scalar()
        left_ord = s.exec(text("SELECT count(*) FROM orders WHERE customer_name LIKE :m"),
                          params={"m": f"{SEED_ORDER_PREFIX}%"}).scalar()
        print(f"잔재 검증 — 재고 {left_inv} / 검수기록 {left_job} / 주문 {left_ord} (전부 0이어야 정상)")


if __name__ == "__main__":
    main()
