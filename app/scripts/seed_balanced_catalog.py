# -*- coding: utf-8 -*-
"""
카테고리 균형 카탈로그 시드 — 알라딘 분류별 베스트셀러 30권 기반.

실행 (컨테이너):
    docker exec wms-secret-api python app/scripts/seed_balanced_catalog.py
    docker exec wms-secret-api python app/scripts/seed_balanced_catalog.py --new-target 150 --used-target 30
    docker exec wms-secret-api python app/scripts/seed_balanced_catalog.py --dry-run

동작 원리 — 채우기(top-up) 방식:
  카테고리마다 목표치를 정하고 **현재 보유량과의 차이만큼만** 채운다. 무조건 N건씩
  더하면 이미 많은 카테고리가 더 커져 편중이 그대로 남는다. 목표를 이미 넘긴
  카테고리는 건드리지 않는다(줄이지도 않는다 - 실측 데이터가 섞여 있다).

도서 원천:
  알라딘 분류별 베스트셀러 **상위 30권**만 쓴다. 카테고리당 모집단이 30권이므로
  같은 도서에 여러 재고 행이 붙는데, 이는 창고 현실과 맞다(베스트셀러는 여러 권 보유).

UBCI 점수:
  NORMAL 등급(65점) 이상만 생성한다. 사유(결함 조합)를 먼저 뽑고 UBCI 매트릭스로
  점수를 역산하므로 "78점인데 결함 없음" 같은 모순이 생기지 않는다.
  감점 수치는 policy_agent와 같은 표를 쓴다.

LPN 채번:
  Zone A는 실촬영 검수 영역이라 침범하지 않는다. B·C·D·E를 순환 배정하며 존별
  기존 최대 순번을 이어받으므로 여러 번 돌려도 충돌하지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from collections import defaultdict
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
    ReturnJob,
    now_kst,
)

ALADIN_LIST_URL = "https://www.aladin.co.kr/ttb/api/ItemList.aspx"
TOP_N = 30  # 분류별 베스트셀러 상위 몇 권을 모집단으로 쓸지

# 알라딘 국내도서 분류(CID)와, 그 분류로 적재할 때 쓸 카테고리 명칭.
# 명칭은 알라딘 categoryName 2단계 값과 맞춰 서빙 경로가 저장하는 형식과 동일하게 둔다.
ALADIN_CATEGORIES: list[tuple[int, str]] = [
    (1, "소설/시/희곡"),
    (170, "경제경영"),
    (336, "자기계발"),
    (656, "인문학"),
    (351, "컴퓨터/모바일"),
    (987, "과학"),
    (1230, "역사"),
    (55889, "만화"),
    (74, "여행"),
    (517, "예술/대중문화"),
    (1196, "에세이"),
    (2551, "사회과학"),
    (1322, "외국어"),
    (1108, "어린이"),
]

SEED_ZONES = ["B", "C", "D", "E"]  # A=실촬영 전용, Z=HITL 격리 → 제외

# 시드가 남기는 검수 이력의 판정 주체. 실촬영 건과 구분되도록 표기를 고정한다.
SEED_WORKER_ID = "WM2699001"
SEED_INSPECTOR = "Nexus Vision AI (시드 생성)"

# 재고 편입 상태로 세지 않는 값들 (조회 API와 동일 기준)
NOT_STOCKED = ("HITL_PENDING", "HITL_REQUIRED", "PENDING_INSPECTION")

# ── UBCI 감점표 (policy_agent와 동일) ────────────────────────────────────
DEFECT_POOL: list[tuple[str, str, int]] = [
    ("DMG_EDGE_WEAR", "모서리 마모", 5),
    ("DMG_EXT_SCRATCH", "표지 긁힘/스크래치", 2),
    ("DMG_EXT_STICKER", "스티커/바코드 자국", 3),
    ("DMG_EXT_CRUSH", "표지 모서리 찍힘", 5),
    ("DMG_INT_DISCOLOR", "내지 황변/빛바램", 2),
    ("DMG_INT_STAIN", "내지 오염/이물질", 5),
    ("DMG_EXT_TEAR", "커버 찢어짐", 10),
    ("DMG_INT_DOODLE", "내부 손글씨/낙서", 10),
    ("DMG_SPINE_CRACK", "책등 갈라짐", 10),
    ("DMG_INT_STAIN_M", "내지 오염(중간)", 10),
    ("DMG_INT_DISCOLOR_H", "내지 황변(심함)", 10),
]

# 등급별 목표 감점 폭 (UBCI 경계: MINT>=95, GOOD>=85, NORMAL>=65).
# REJECT는 넣지 않는다 - 판매 가능 재고를 채우는 것이 목적이다.
GRADE_BUDGET = {"MINT": (0, 5), "GOOD": (6, 15), "NORMAL": (16, 35)}
GRADE_PLAN = ["MINT"] * 30 + ["GOOD"] * 45 + ["NORMAL"] * 25

# 반려 사유 (UBCI 규정상 물리적 재판매 불가 → 점수와 무관하게 REJECT)
FATAL_POOL: list[tuple[str, str]] = [
    ("DMG_EXT_WET", "액체 오염/습기/휨"),
    ("DMG_BINDING_LOOSE", "제본 완전 벌어짐"),
]

# HITL 이관 사유 (Supervisor/Critic 라우팅 코드와 동일)
HITL_REASONS = [
    ("NO_VALID_IMAGE_HITL", "촬영 4컷 전부 도서 미식별 - 판독 커버리지 미달"),
    ("CRITIC_INTEGRITY_VIOLATION", "Vision 결함 수와 Policy 감점 불일치"),
    ("CRITIC_RETRY_EXCEEDED", "재검수 루프 2회 초과"),
    ("SCORE_BOUNDARY", "NORMAL/REJECT 경계선 - 자동 확정 시 매입가 오차 큼"),
]


def build_defects_for_grade(target: str) -> tuple[list[dict], int]:
    """등급을 먼저 정하고 그 등급이 나오도록 결함 조합을 뽑는다. 사유가 원인, 점수가 결과."""
    lo, hi = GRADE_BUDGET[target]
    picked: list[dict] = []
    total = 0
    pool = DEFECT_POOL[:]
    random.shuffle(pool)

    for code, label, ded in pool:
        if total >= lo and (total + ded) > hi:
            continue
        if total + ded <= hi:
            # confidence는 파이프라인과 같은 0~1 실수다. 이 값이 없으면 검수 이력의
            # AI 신뢰도 열이 전부 "미기록"이 된다(평균 낼 근거가 없으므로).
            picked.append(
                {
                    "type": code,
                    "label": label,
                    "deduction": ded,
                    "confidence": round(random.uniform(0.62, 0.97), 4),
                }
            )
            total += ded
        if total >= lo and picked and random.random() < 0.45:
            break

    if target == "MINT" and random.random() < 0.6:
        picked, total = [], 0

    return picked, max(65, 100 - total)


async def fetch_top_bestsellers() -> dict[str, list[dict]]:
    """분류별 베스트셀러 상위 TOP_N권을 수집한다. 반환: {카테고리명: [도서메타]}"""
    result: dict[str, list[dict]] = {}
    seen_isbn: set[str] = set()

    async with httpx.AsyncClient(timeout=20.0) as client:
        for cid, cname in ALADIN_CATEGORIES:
            params = {
                "ttbkey": settings.ALADIN_TTB_KEY,
                "QueryType": "Bestseller",
                "MaxResults": TOP_N,
                "start": 1,
                "SearchTarget": "Book",
                "output": "js",
                "Version": "20131101",
                "CategoryId": cid,
            }
            try:
                r = await client.get(ALADIN_LIST_URL, params=params)
                items = (r.json() or {}).get("item", [])
            except Exception as e:
                print(f"  [조회 실패] cid={cid}({cname}): {e}")
                continue

            books = []
            for it in items:
                isbn = (it.get("isbn13") or "").strip()
                # ISBN이 여러 분류의 베스트셀러에 동시에 오르면 먼저 잡힌 분류에만 넣는다.
                # 한 도서가 두 카테고리에 걸리면 분포 집계가 이중 계상된다.
                if not isbn or isbn in seen_isbn:
                    continue
                seen_isbn.add(isbn)
                books.append(
                    {
                        "isbn": isbn,
                        "title": it.get("title") or "제목 미상",
                        "author": it.get("author"),
                        "publisher": it.get("publisher"),
                        "pubDate": it.get("pubDate"),
                        "price": int(
                            it.get("priceStandard") or it.get("priceSales") or 0
                        ),
                        "cover": it.get("cover"),
                        "description": it.get("description"),
                        "category": cname,
                    }
                )
            result[cname] = books
            print(f"  cid={cid:<6} {cname:<14} {len(books):>3}권")
    return result


def next_lpn_seq(db: Session) -> dict[str, int]:
    """존별 기존 LPN 최대 순번 (재실행 안전).

    재고 테이블만 보면 안 된다 - 검수 이력의 LPN은 return_jobs.agent_logs(JSON) 안에 있어
    컬럼 스캔에 잡히지 않는다. 두 테이블 합집합에서 최대값을 구한다.
    """
    rows = db.exec(
        text(
            r"SELECT substring(lpn from '([A-Z])[0-9]+$') AS z,"
            r"       max(substring(lpn from '[A-Z]([0-9]+)$')::int) AS mx FROM ("
            r"  SELECT lpn_barcode AS lpn FROM inventory_used_items"
            r"  UNION ALL"
            r"  SELECT agent_logs->>'lpn_barcode' FROM return_jobs"
            r"   WHERE agent_logs->>'lpn_barcode' IS NOT NULL"
            r") x WHERE lpn ~ '[A-Z][0-9]+$' GROUP BY 1"
        )
    ).all()
    seq = {z: 0 for z in SEED_ZONES}
    for z, mx in rows:
        if z in seq:
            seq[z] = int(mx or 0)
    return seq


def current_totals(db: Session) -> tuple[dict[str, int], dict[str, int]]:
    """카테고리별 현재 보유량 (중고 건수, 신품 수량)."""
    used = dict(
        db.exec(
            text(
                "SELECT coalesce(b.category_type,'미분류'), count(*) "
                "FROM inventory_used_items u JOIN books b ON b.id = u.book_id "
                "WHERE u.item_status IS NULL OR u.item_status NOT IN "
                "      ('HITL_PENDING','HITL_REQUIRED','PENDING_INSPECTION') "
                "GROUP BY 1"
            )
        ).all()
    )
    new = dict(
        db.exec(
            text(
                "SELECT coalesce(b.category_type,'미분류'), coalesce(sum(i.quantity),0) "
                "FROM inventory i JOIN books b ON b.id = i.book_id GROUP BY 1"
            )
        ).all()
    )
    return defaultdict(int, used), defaultdict(int, new)


def main() -> None:
    ap = argparse.ArgumentParser(description="카테고리 균형 카탈로그 시드")
    ap.add_argument(
        "--new-target", type=int, default=140, help="카테고리별 신품 목표 수량"
    )
    ap.add_argument(
        "--used-target", type=int, default=28, help="카테고리별 중고 목표 건수"
    )
    ap.add_argument(
        "--reject-jobs",
        type=int,
        default=38,
        help="반려 검수 이력 건수 (재고에는 편입되지 않음)",
    )
    ap.add_argument(
        "--hitl-jobs",
        type=int,
        default=22,
        help="HITL 이관 검수 이력 건수 (등급 미확정이라 재고 편입 안 됨)",
    )
    ap.add_argument("--dry-run", action="store_true", help="DB 변경 없이 계획만 출력")
    args = ap.parse_args()

    print(f"[1/4] 알라딘 분류별 베스트셀러 상위 {TOP_N}권 수집")
    by_category = asyncio.run(fetch_top_bestsellers())
    total_meta = sum(len(v) for v in by_category.values())
    print(f"      수집 완료: {len(by_category)}개 분류 / {total_meta}권\n")
    if not total_meta:
        print("중단 - 알라딘 응답이 비었습니다. TTB 키와 네트워크를 확인하세요.")
        return

    with Session(engine) as db:
        # ── 도서 마스터 업서트 ────────────────────────────────────────
        existing = {b.isbn: b for b in db.exec(select(Book)).all() if b.isbn}
        created = 0
        for cname, metas in by_category.items():
            for m in metas:
                if m["isbn"] in existing:
                    continue
                book = Book(
                    isbn=m["isbn"],
                    title=m["title"],
                    author=m["author"],
                    publisher=m["publisher"],
                    published_date=m["pubDate"],
                    base_price=float(m["price"] or 0),
                    description=m["description"],
                    cover_image_url=m["cover"],
                    category_type=cname,
                )
                db.add(book)
                existing[m["isbn"]] = book
                created += 1
        if not args.dry_run:
            db.commit()
        print(f"[2/4] 도서 마스터: 신규 {created}권 (누적 {len(existing)}권)\n")

        # 카테고리별 도서 풀 (방금 받은 베스트셀러 한정 - 재고를 여기에만 붙인다)
        pool: dict[str, list[Book]] = {}
        for cname, metas in by_category.items():
            books = [existing[m["isbn"]] for m in metas if m["isbn"] in existing]
            if books:
                pool[cname] = books

        used_now, new_now = current_totals(db)

        # ── 신품 재고 채우기 ─────────────────────────────────────────
        new_locs = db.exec(select(Location).where(Location.zone == "A")).all()
        if not new_locs:
            print("중단 - Zone A 로케이션이 없습니다 (신품 보관존).")
            return

        # (book_id, location_id) 유니크 제약이 있으므로 기존 행은 수량을 더한다.
        inv_index = {
            (i.book_id, i.location_id): i for i in db.exec(select(Inventory)).all()
        }
        new_added: dict[str, int] = {}

        # 목표를 넘긴 카테고리는 줄인다. 채우기만 하면 이미 많은 쪽이 그대로 남아
        # 편중이 해소되지 않는다. 신품은 묶음 수량이라 행을 지우거나 수량을 깎으면 된다.
        # 감축 대상은 **해당 카테고리의 모든 도서**다. 베스트셀러 풀로 좁히면 이전 시드나
        # AUTO_PO 승인으로 들어온 다른 도서의 재고가 남아 목표에 도달하지 못한다.
        book_ids_by_cat: dict[str, set] = defaultdict(set)
        for bid, cat in db.exec(select(Book.id, Book.category_type)).all():
            book_ids_by_cat[cat or "미분류"].add(bid)
        new_trimmed: dict[str, int] = {}
        for cname in pool:
            surplus = new_now[cname] - args.new_target
            if surplus <= 0:
                continue
            removed = 0
            for row in sorted(
                (r for r in inv_index.values() if r.book_id in book_ids_by_cat[cname]),
                key=lambda r: r.quantity,
                reverse=True,
            ):
                if removed >= surplus:
                    break
                take = min(row.quantity, surplus - removed)
                row.quantity -= take
                removed += take
                if row.quantity == 0:
                    db.delete(row)
                else:
                    db.add(row)
            new_trimmed[cname] = removed

        for cname, books in pool.items():
            deficit = args.new_target - new_now[cname]
            if deficit <= 0:
                continue
            added = 0
            while added < deficit:
                book = random.choice(books)
                loc = random.choice(new_locs)
                qty = min(random.randint(2, 6), deficit - added)
                key = (book.id, loc.id)
                if key in inv_index:
                    inv_index[key].quantity += qty
                    db.add(inv_index[key])
                else:
                    row = Inventory(book_id=book.id, location_id=loc.id, quantity=qty)
                    db.add(row)
                    inv_index[key] = row
                added += qty
            new_added[cname] = added

        # ── 중고 재고 채우기 ─────────────────────────────────────────
        used_locs = db.exec(select(Location).where(Location.zone.in_(SEED_ZONES))).all()
        if not used_locs:
            print("중단 - 시드용 로케이션(B~E)이 없습니다.")
            return
        loc_by_zone: dict[str, list[Location]] = defaultdict(list)
        for lc in used_locs:
            loc_by_zone[lc.zone].append(lc)

        seq = next_lpn_seq(db)
        today = now_kst()
        used_added: dict[str, int] = {}
        zone_cursor = 0

        for cname, books in pool.items():
            deficit = args.used_target - used_now[cname]
            if deficit <= 0:
                continue
            for _ in range(deficit):
                zone = SEED_ZONES[zone_cursor % len(SEED_ZONES)]
                zone_cursor += 1
                if not loc_by_zone.get(zone):
                    continue
                # LPN 순번은 3자리다. 넘기면 형식이 깨져 파싱·정렬이 모두 어긋난다.
                if seq[zone] >= 999:
                    print(
                        f"      [중단] Zone {zone} 일일 순번 999 소진 - 목표를 낮추거나 날짜를 나누세요."
                    )
                    break
                seq[zone] += 1

                target = random.choice(GRADE_PLAN)
                _defects, score = build_defects_for_grade(
                    target
                )  # agent_logs에 그대로 기록
                grade = {
                    "MINT": ConditionGradeEnum.MINT,
                    "GOOD": ConditionGradeEnum.GOOD,
                    "NORMAL": ConditionGradeEnum.NORMAL,
                }[target]

                inspected = today - timedelta(
                    days=random.randint(0, 13),
                    hours=random.randint(0, 23),
                    minutes=random.randint(0, 59),
                )
                book = random.choice(books)
                lpn = f"LPN-{today:%y%m%d}-{zone}{seq[zone]:03d}"

                # 검수 이력(원장)을 함께 남긴다. 재고 행만 만들면 대시보드 KPI와
                # 검수 처리 내역이 이 물량을 보지 못한다 - 실제 입고는 파이프라인을
                # 거치므로 두 테이블이 항상 짝을 이룬다.
                job = ReturnJob(
                    id=uuid4(),
                    book_id=book.id,
                    status="APPROVED",
                    mode="INBOUND",
                    ubci_score=score,
                    image_urls=[],
                    agent_logs={
                        "lpn_barcode": lpn,
                        "defects": _defects,
                        "inbound_worker_id": SEED_WORKER_ID,
                        "seed": True,
                    },
                    retry_count=0,
                    created_at=inspected,
                    updated_at=inspected,
                )
                db.add(job)
                # 재고 행이 source_job_id로 이 잡을 참조하므로 먼저 내보낸다.
                # flush 없이 두면 autoflush 순서에 따라 재고가 먼저 나가 FK 위반이 난다.
                db.flush()

                db.add(
                    InventoryUsedItem(
                        id=uuid4(),
                        book_id=book.id,
                        location_id=random.choice(loc_by_zone[zone]).id,
                        lpn_barcode=lpn,
                        condition_grade=grade.value,
                        ubci_score=score,
                        item_status="IN_STOCK",
                        source_job_id=job.id,
                        inspection_source="AI_AUTO",
                        inspected_by=SEED_INSPECTOR,
                        inspected_at=inspected,
                        created_at=inspected,
                        updated_at=inspected,
                    )
                )
            used_added[cname] = deficit

        # 반려·HITL 이력 생성.
        #
        # 재고 행을 만들지 않는다. 반려된 도서는 반송/폐기되어 창고에 남지 않고,
        # HITL 이관 건은 아직 등급이 확정되지 않아 적재 대상이 아니다.
        # 이 둘이 없으면 승인율이 100%에 수렴해 검수 실적이 비현실적으로 보인다.
        all_seed_books = [b for books in pool.values() for b in books]
        extra_jobs = 0

        for _ in range(max(0, args.reject_jobs)):
            code, label = random.choice(FATAL_POOL)
            moment = today - timedelta(
                days=random.randint(0, 13), hours=random.randint(0, 23)
            )
            db.add(
                ReturnJob(
                    id=uuid4(),
                    book_id=random.choice(all_seed_books).id,
                    status="REJECTED",
                    mode="INBOUND",
                    ubci_score=0,
                    image_urls=[],
                    agent_logs={
                        "defects": [
                            {
                                "type": code,
                                "label": label,
                                "deduction": 100,
                                "fatal": True,
                                "confidence": round(random.uniform(0.78, 0.99), 4),
                            }
                        ],
                        "reason_code": code,
                        "inbound_worker_id": SEED_WORKER_ID,
                        "seed": True,
                    },
                    created_at=moment,
                    updated_at=moment,
                )
            )
            extra_jobs += 1

        for _ in range(max(0, args.hitl_jobs)):
            code, detail = random.choice(HITL_REASONS)
            moment = today - timedelta(
                days=random.randint(0, 13), hours=random.randint(0, 23)
            )
            # 커버리지 미달 건은 점수를 내지 않는다(파이프라인이 None으로 남긴다).
            score = None if code == "NO_VALID_IMAGE_HITL" else random.randint(58, 66)
            db.add(
                ReturnJob(
                    id=uuid4(),
                    book_id=random.choice(all_seed_books).id,
                    status="HITL_REQUIRED",
                    mode="INBOUND",
                    ubci_score=score,
                    image_urls=[],
                    agent_logs={
                        "defects": [],
                        "reason_code": code,
                        "reason_detail": detail,
                        "inbound_worker_id": SEED_WORKER_ID,
                        "seed": True,
                    },
                    retry_count=random.randint(0, 2),
                    created_at=moment,
                    updated_at=moment,
                )
            )
            extra_jobs += 1

        print(
            f"[3/4] 반려 {args.reject_jobs}건 · HITL {args.hitl_jobs}건 이력 생성 (재고 미편입)"
        )
        print("      채울 수량 (목표 - 현재)")
        print(f"      {'카테고리':<16}{'신품+':>8}{'신품-':>8}{'중고+':>8}")
        for cname in sorted(pool):
            print(
                f"      {cname:<16}{new_added.get(cname, 0):>8}"
                f"{new_trimmed.get(cname, 0):>8}{used_added.get(cname, 0):>8}"
            )

        if args.dry_run:
            db.rollback()
            print("\n[4/4] dry-run - DB에 반영하지 않았습니다.")
            return

        db.commit()

        # 중고와 신품을 각각 집계한 뒤 합친다. 한 쿼리에서 두 테이블을 동시에 조인하면
        # 카테시안 곱이 생겨 수량이 부풀려진다.
        rows = db.exec(
            text(
                "WITH u AS ("
                "  SELECT coalesce(b.category_type,'미분류') AS c, count(*) AS n"
                "    FROM inventory_used_items i JOIN books b ON b.id = i.book_id"
                "   WHERE i.item_status IS NULL OR i.item_status NOT IN"
                "         ('HITL_PENDING','HITL_REQUIRED','PENDING_INSPECTION')"
                "   GROUP BY 1"
                "), v AS ("
                "  SELECT coalesce(b.category_type,'미분류') AS c, sum(i.quantity) AS n"
                "    FROM inventory i JOIN books b ON b.id = i.book_id GROUP BY 1"
                ")"
                "SELECT coalesce(u.c, v.c), coalesce(u.n,0), coalesce(v.n,0),"
                "       coalesce(u.n,0) + coalesce(v.n,0) AS total"
                "  FROM u FULL OUTER JOIN v ON u.c = v.c ORDER BY 4 DESC"
            )
        ).all()
        print("\n[4/4] 반영 후 카테고리 분포")
        print(f"      {'카테고리':<16}{'중고':>8}{'신품':>8}{'합계':>8}")
        for c, used, new, total in rows:
            print(f"      {c:<16}{used:>8}{new:>8}{total:>8}")


if __name__ == "__main__":
    main()
