# -*- coding: utf-8 -*-
"""
카탈로그 시드 — 알라딘 베스트셀러 실조회 기반 신품/중고 대량 적재.

실행 (컨테이너):
    docker exec wms-secret-api python app/scripts/seed_catalog.py
    docker exec wms-secret-api python app/scripts/seed_catalog.py --new 1000 --used 200
    docker exec wms-secret-api python app/scripts/seed_catalog.py --used 50 --new 0   # 중고만 추가

[왜 만들었나]
종전 시드는 하드코딩 50권(scripts/seed/seed_50_books_script.py)이라 대시보드 차트가
빈약했고, category_type에 영문 시드값(IT/Novel/Economy...)이 들어가 알라딘 실조회값과
섞여 카테고리 분포가 깨졌다. 이 스크립트는 **알라딘 API 실조회만** 쓰므로 서빙 경로가
저장하는 값과 100% 같은 형식이 된다.

[LPN 채번 규칙]
    LPN-YYMMDD-{존}{순번3자리}     예: LPN-260806-B001

  - **Zone A는 쓰지 않는다.** A는 조장이 직접 검수하는 실촬영 영역으로 예약돼 있어,
    시드가 침범하면 실측 데이터와 생성 데이터가 섞여 구분이 불가능해진다.
  - 시드는 B·C·D·E 순환 배정. 존별로 **기존 최대 순번을 이어받아** 채번하므로
    여러 번 돌려도 LPN이 충돌하지 않는다.

[UBCI 점수와 사유의 정합성]
점수를 먼저 뽑고 사유를 따로 붙이면 "78점인데 결함 없음" 같은 모순이 생긴다.
그래서 **사유(결함 조합)를 먼저 뽑고 UBCI 매트릭스로 점수를 역산**한다.
감점 수치는 policy_agent와 동일한 표를 쓴다 (2026-08-06 확정 STAIN/DISCOLOR 포함).
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import datetime, timedelta
from uuid import uuid4

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.config import settings
from app.db.session import engine
from app.models.wms import (
    Book,
    ConditionGradeEnum,
    Inventory,
    InventoryUsedItem,
    Location,
    now_kst,
)

ALADIN_LIST_URL = "https://www.aladin.co.kr/ttb/api/ItemList.aspx"

# 알라딘 국내도서 주요 분류 (CID). 베스트셀러를 분류별로 받아야 카테고리 분포가 생긴다.
# 한 CID만 쓰면 전량 같은 장르가 되어 차트가 막대 하나로 붕괴한다.
ALADIN_CATEGORIES: list[tuple[int, str]] = [
    (0, "전체"),
    (1, "소설/시/희곡"),
    (170, "경제경영"),
    (336, "자기계발"),
    (656, "인문학"),
    (351, "컴퓨터/모바일"),
    (2917, "고전"),
    (55889, "만화"),
    (987, "과학"),
    (74, "여행"),
    (517, "예술/대중문화"),
    (1230, "역사"),
    (798, "종교/역학"),
    (1196, "에세이"),
    (2551, "사회과학"),
]

# 시드가 사용할 존. A는 실촬영 전용이라 제외하고, Z(HITL 격리)도 제외한다.
SEED_ZONES = ["B", "C", "D", "E"]

# ── UBCI 감점표 (policy_agent와 동일) ────────────────────────────────────
# (사유코드, 표기, 감점) — 감점은 정책상 확정값이며 임의로 바꾸지 않는다.
DEFECT_POOL: list[tuple[str, str, int]] = [
    ("DMG_EDGE_WEAR",    "모서리 마모",        5),
    ("DMG_EXT_SCRATCH",  "표지 긁힘/스크래치",  2),
    ("DMG_EXT_STICKER",  "스티커/바코드 자국",  3),
    ("DMG_EXT_CRUSH",    "표지 모서리 찍힘",    5),
    ("DMG_INT_DISCOLOR", "내지 황변/빛바램",    2),   # level 1
    ("DMG_INT_STAIN",    "내지 오염/이물질",    5),   # ratio < 5%
    ("DMG_EXT_TEAR",     "커버 찢어짐",        10),
    ("DMG_INT_DOODLE",   "내부 손글씨/낙서",   10),
    ("DMG_SPINE_CRACK",  "책등 갈라짐",        10),
    ("DMG_INT_STAIN_M",  "내지 오염(중간)",    10),   # ratio 5~15%
    ("DMG_INT_DISCOLOR_H", "내지 황변(심함)",  10),   # level 3
]
# 즉시 반려 사유 (UBCI 규정상 물리적 사용 불가)
FATAL_POOL: list[tuple[str, str]] = [
    ("DMG_EXT_WET", "액체 오염/습기/휨"),
    ("DMG_BINDING_LOOSE", "제본 완전 벌어짐"),
]


def build_defects_for_grade(target: str) -> tuple[list[dict], int]:
    """등급을 먼저 정하고, 그 등급이 나오도록 결함 조합을 뽑는다.

    점수를 먼저 만들고 사유를 붙이면 둘이 어긋난다. 여기서는 **사유가 원인, 점수가 결과**다.
    반환: (결함 목록, UBCI 점수)
    """
    if target == "REJECT":
        code, label = random.choice(FATAL_POOL)
        return ([{"type": code, "label": label, "deduction": 100, "fatal": True}], 0)

    # 등급별 목표 감점 폭 (UBCI 경계: S>=95, A>=85, B>=65)
    budget = {"MINT": (0, 5), "GOOD": (6, 15), "NORMAL": (16, 35)}[target]
    lo, hi = budget
    picked: list[dict] = []
    total = 0
    pool = DEFECT_POOL[:]
    random.shuffle(pool)

    for code, label, ded in pool:
        if total >= lo and (total + ded) > hi:
            continue
        if total + ded <= hi:
            picked.append({"type": code, "label": label, "deduction": ded})
            total += ded
        if total >= lo and len(picked) >= 1 and random.random() < 0.45:
            break

    # MINT는 결함 0건도 자연스럽다 (오히려 그게 정상)
    if target == "MINT" and random.random() < 0.6:
        picked, total = [], 0

    return picked, max(0, 100 - total)


async def fetch_aladin_bestsellers(target_count: int) -> list[dict]:
    """분류별 베스트셀러를 모아 target_count권까지 수집 (ISBN13 중복 제거)."""
    collected: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        for cid, cname in ALADIN_CATEGORIES:
            if len(collected) >= target_count:
                break
            for start in (1, 2, 3, 4, 5):          # 회당 50건, 최대 250건/분류
                if len(collected) >= target_count:
                    break
                params = {
                    "ttbkey": settings.ALADIN_TTB_KEY,
                    "QueryType": "Bestseller",
                    "MaxResults": 50,
                    "start": start,
                    "SearchTarget": "Book",
                    "output": "js",
                    "Version": "20131101",
                    "CategoryId": cid,
                }
                try:
                    r = await client.get(ALADIN_LIST_URL, params=params)
                    items = (r.json() or {}).get("item", [])
                except Exception as e:
                    print(f"  [조회 실패] cid={cid}({cname}) start={start}: {e}")
                    break
                if not items:
                    break
                for it in items:
                    isbn = (it.get("isbn13") or "").strip()
                    if not isbn or isbn in collected:
                        continue
                    parts = [p.strip() for p in (it.get("categoryName") or "").split(">") if p.strip()]
                    collected[isbn] = {
                        "isbn": isbn,
                        "title": it.get("title") or "제목 미상",
                        "author": it.get("author"),
                        "publisher": it.get("publisher"),
                        "pubDate": it.get("pubDate"),
                        "price": int(it.get("priceStandard") or it.get("priceSales") or 0),
                        "cover": it.get("cover"),
                        "description": it.get("description"),
                        # 서빙 경로(inbound/router.py)와 동일하게 2단계를 장르로 쓴다
                        "category": parts[1] if len(parts) > 1 else (parts[0] if parts else "미분류"),
                    }
            print(f"  cid={cid:<6} {cname:<14} 누적 {len(collected)}권")
    return list(collected.values())[:target_count]


def next_lpn_seq(db: Session) -> dict[str, int]:
    """존별 기존 LPN 최대 순번을 읽어 이어붙일 시작점을 만든다 (재실행 안전).

    재고 테이블만 보면 안 된다 - 검수이력의 LPN은 `return_jobs.agent_logs`(JSON) 안에 있어
    `lpn_barcode` 컬럼 스캔에 잡히지 않고, 그대로 두면 이력에 이미 부여된 번호를 재고에
    중복 발급한다(2026-08-06에 실제로 발생). 두 테이블 합집합에서 최대값을 구한다.
    """
    rows = db.exec(text(
        r"SELECT substring(lpn from '([A-Z])[0-9]+$') AS z,"
        r"       max(substring(lpn from '[A-Z]([0-9]+)$')::int) AS mx FROM ("
        r"  SELECT lpn_barcode AS lpn FROM inventory_used_items"
        r"  UNION ALL"
        r"  SELECT agent_logs->>'lpn_barcode' FROM return_jobs"
        r"   WHERE agent_logs->>'lpn_barcode' IS NOT NULL"
        r") x WHERE lpn ~ '[A-Z][0-9]+$' GROUP BY 1"
    )).all()
    seq = {z: 0 for z in SEED_ZONES}
    for z, mx in rows:
        if z in seq:
            seq[z] = int(mx or 0)
    return seq


def main() -> None:
    ap = argparse.ArgumentParser(description="알라딘 베스트셀러 기반 카탈로그 시드")
    ap.add_argument("--new", type=int, default=1000, help="신품 도서 권수 (books 테이블)")
    ap.add_argument("--used", type=int, default=200, help="중고 재고 건수 (inventory_used_items)")
    args = ap.parse_args()

    print(f"[1/3] 알라딘 베스트셀러 수집 (목표 {args.new}권)")
    books_meta = asyncio.run(fetch_aladin_bestsellers(args.new)) if args.new > 0 else []
    print(f"      수집 완료: {len(books_meta)}권")

    with Session(engine) as db:
        # ── 신품: books 업서트 ──────────────────────────────────────────
        existing = {b.isbn: b for b in db.exec(select(Book)).all() if b.isbn}
        created = 0
        for m in books_meta:
            if m["isbn"] in existing:
                continue
            db.add(Book(
                isbn=m["isbn"],
                title=m["title"],
                author=m["author"],
                publisher=m["publisher"],
                published_date=m["pubDate"],
                base_price=float(m["price"] or 0),
                description=m["description"],
                cover_image_url=m["cover"],
                category_type=m["category"],
            ))
            created += 1
        db.commit()
        print(f"[2/3] 신품 등록: 신규 {created}권 / 기존 {len(existing)}권")

        if args.used <= 0:
            print("[3/3] 중고 시드 생략 (--used 0)")
            return

        all_books = db.exec(select(Book)).all()
        locations = db.exec(select(Location).where(Location.zone.in_(SEED_ZONES))).all()
        if not all_books or not locations:
            print("[3/3] 중단 - 도서 또는 로케이션이 없습니다.")
            return

        loc_by_zone: dict[str, list[Location]] = {z: [] for z in SEED_ZONES}
        for lc in locations:
            loc_by_zone.setdefault(lc.zone, []).append(lc)

        seq = next_lpn_seq(db)
        print(f"[3/3] 중고 {args.used}건 생성 (존별 시작 순번: "
              + ", ".join(f"{z}{seq[z]+1:03d}" for z in SEED_ZONES) + ")")

        # 실제 창고 분포에 가깝게: 대부분 양호, 소수만 반려
        grade_plan = (["MINT"] * 25 + ["GOOD"] * 45 + ["NORMAL"] * 25 + ["REJECT"] * 5)
        today = now_kst()
        made = 0

        for i in range(args.used):
            zone = SEED_ZONES[i % len(SEED_ZONES)]
            if not loc_by_zone.get(zone):
                continue
            seq[zone] += 1
            target = random.choice(grade_plan)
            defects, score = build_defects_for_grade(target)
            grade = {"MINT": ConditionGradeEnum.MINT, "GOOD": ConditionGradeEnum.GOOD,
                     "NORMAL": ConditionGradeEnum.NORMAL, "REJECT": ConditionGradeEnum.REJECT}[target]

            book = random.choice(all_books)
            inspected = today - timedelta(days=random.randint(0, 13),
                                          hours=random.randint(0, 23),
                                          minutes=random.randint(0, 59))
            db.add(InventoryUsedItem(
                id=uuid4(),
                book_id=book.id,
                location_id=random.choice(loc_by_zone[zone]).id,
                lpn_barcode=f"LPN-{today:%y%m%d}-{zone}{seq[zone]:03d}",
                condition_grade=grade.value,
                ubci_score=score,
                inspected_by="SEED",
                inspection_source="SEED",
                created_at=inspected,
                updated_at=inspected,
                defect_details=defects,
            ))
            made += 1

        db.commit()
        print(f"      생성 완료: {made}건")

        dist = db.exec(text(
            "SELECT condition_grade, count(*), min(ubci_score), max(ubci_score) "
            "FROM inventory_used_items WHERE inspection_source='SEED' GROUP BY 1 ORDER BY 1"
        )).all()
        print()
        print("      등급 분포 (SEED 한정)")
        for g, c, mn, mx in dist:
            print(f"        {g:<8} {c:>4}건   UBCI {mn}~{mx}")


if __name__ == "__main__":
    main()
