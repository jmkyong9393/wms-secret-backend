# -*- coding: utf-8 -*-
"""
비운영 LPN 형식 통일 — 운영 포맷을 벗어난 모든 LPN을 'LPN-260731-{존}{순번3자리}'로 재채번.

실행:  docker exec wms-api python app/scripts/migrate_demo_lpn.py

[배경]
정규 형식은 `LPN-YYMMDD-{존}{순번3자리}`(예: LPN-260806-B001)인데, 시드·테스트 과정에서
아래처럼 여러 변종이 쌓였다.
    LPN-DEMO-31-69F1        (뒤 4자리가 순번이 아니라 랜덤 해시)
    LPN-DEMO-HIST-00 / -HITL-00
    LPN-CHAOS-085358-03     (카오스 테스트 잔재)
    LPN-TEST-WBFVERIFY-01   (WBF 검증 잔재)
형식이 갈리면 존·순번 파싱이 필요한 화면과 채번 로직이 예외를 탄다.

대상 선정을 접두어 목록이 아니라 **정규 형식 불일치**로 잡는다. 접두어를 열거하면 다음에
생기는 변종을 또 놓치고, 이미 정규 형식인 행은 어차피 걸리지 않으므로 멱등성도 공짜로 얻는다.

[날짜를 260731로 고정하는 이유]
2026-07-31은 과거 날짜라 정상 운영(오늘 날짜로 채번)에서 다시 생성되지 않는다.
YYMMDD가 한 바퀴 도는 것은 2126년이므로 사실상 고유한 마커가 된다.
'LPN-DEMO-' 문자열 대신 이 날짜가 데모 시드 식별자 역할을 그대로 이어받는다.

[Zone A 회피]
Zone A는 조장이 직접 검수하는 실촬영 영역으로 예약돼 있다. 데모 시드가 A에 남아 있으면
실측 데이터와 생성 데이터가 섞여 구분이 불가능해지므로, A에 있던 건은 B~E로 재배치한다.

멱등하다 - 이미 변환된 건은 건너뛴다.
"""
import sys
from collections import defaultdict

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text
from sqlmodel import Session

from app.db.session import engine

SENTINEL_DATE = "260731"
SEED_ZONES = ["B", "C", "D", "E"]

# 운영 정규 형식. 이 패턴에 **맞지 않는** 행만 재채번 대상이다.
LPN_FORMAT = r"^LPN-[0-9]{6}-[A-Z][0-9]{3}$"
NOT_CONFORMING = f"lpn_barcode !~ '{LPN_FORMAT}'"
NOT_CONFORMING_JOB = (
    "agent_logs->>'lpn_barcode' IS NOT NULL "
    f"AND agent_logs->>'lpn_barcode' !~ '{LPN_FORMAT}'"
)


def main() -> None:
    with Session(engine) as db:
        # ── 백업 ────────────────────────────────────────────────────────
        # 이미 존재하면(1차 실행분) 이번 회차 대상만 덧붙인다 - 원본을 덮어쓰지 않는다.
        for tbl, sel in (
            ("_bak_20260806_demo_lpn",
             f"SELECT id, lpn_barcode, location_id FROM inventory_used_items WHERE {NOT_CONFORMING}"),
            ("_bak_20260806_demo_lpn_jobs",
             f"SELECT id, agent_logs FROM return_jobs WHERE {NOT_CONFORMING_JOB}"),
        ):
            exists = db.exec(text("SELECT to_regclass(:t)"), params={"t": tbl}).first()[0]
            if exists:
                db.exec(text(f"INSERT INTO {tbl} {sel}"))
            else:
                db.exec(text(f"CREATE TABLE {tbl} AS {sel}"))
        db.commit()

        # ── 존별 기존 최대 순번 (260731 네임스페이스) ──────────────────
        # 재고 테이블만 보면 안 된다. 검수이력의 LPN은 agent_logs(JSON) 안에 있어서
        # lpn_barcode 컬럼 스캔에 잡히지 않고, 그대로 두면 이미 이력에 부여한 번호를
        # 재고에 중복 발급하게 된다. 두 테이블 합집합에서 최대값을 구한다.
        rows = db.exec(text(
            r"SELECT substring(lpn from '([A-Z])[0-9]{3}$'),"
            r"       max(substring(lpn from '[A-Z]([0-9]{3})$')::int) FROM ("
            rf"  SELECT lpn_barcode AS lpn FROM inventory_used_items"
            rf"   WHERE lpn_barcode LIKE 'LPN-{SENTINEL_DATE}-%'"
            r"  UNION ALL"
            r"  SELECT agent_logs->>'lpn_barcode' FROM return_jobs"
            rf"   WHERE agent_logs->>'lpn_barcode' LIKE 'LPN-{SENTINEL_DATE}-%'"
            r") x GROUP BY 1"
        )).all()
        seq: dict[str, int] = defaultdict(int)
        for z, mx in rows:
            if z:
                seq[z] = int(mx or 0)

        # ── 1) 재고 항목: Zone A 탈출 + LPN 재채번 ─────────────────────
        items = db.exec(text(
            "SELECT i.id, i.lpn_barcode, l.zone "
            "FROM inventory_used_items i LEFT JOIN locations l ON i.location_id = l.id "
            f"WHERE i.{NOT_CONFORMING} ORDER BY i.lpn_barcode"
        )).all()

        # A에서 빼낼 항목에 배정할 로케이션 (B~E)
        alt_locs = db.exec(text(
            "SELECT id, zone FROM locations WHERE zone IN ('B','C','D','E') ORDER BY zone, rack, shelf"
        )).all()
        alt_by_zone: dict[str, list] = defaultdict(list)
        for lid, z in alt_locs:
            alt_by_zone[z].append(lid)

        # 재고와 검수이력이 같은 LPN을 공유하는 경우(같은 물건의 입고↔검수 기록)가 있다.
        # 양쪽을 따로 채번하면 연결이 끊기므로, 재고에서 정한 새 값을 이력이 물려받는다.
        remap: dict[str, str] = {}

        moved = renamed = 0
        rr = 0
        for item_id, old_lpn, zone in items:
            target_zone = zone
            # Zone A(또는 존 미상)에 있으면 B~E로 옮긴다
            if target_zone not in SEED_ZONES:
                target_zone = SEED_ZONES[rr % len(SEED_ZONES)]
                rr += 1
                pool = alt_by_zone.get(target_zone) or []
                if pool:
                    db.exec(
                        text("UPDATE inventory_used_items SET location_id = :l WHERE id = :i"),
                        params={"l": pool[moved % len(pool)], "i": item_id},
                    )
                    moved += 1

            seq[target_zone] += 1
            new_lpn = f"LPN-{SENTINEL_DATE}-{target_zone}{seq[target_zone]:03d}"
            db.exec(
                text("UPDATE inventory_used_items SET lpn_barcode = :n WHERE id = :i"),
                params={"n": new_lpn, "i": item_id},
            )
            remap[old_lpn] = new_lpn
            print(f"  {old_lpn:<24} ({zone or '-'}) -> {new_lpn}")
            renamed += 1

        # ── 2) 검수 이력(return_jobs.agent_logs.lpn_barcode) ───────────
        jobs = db.exec(text(
            "SELECT id, agent_logs->>'lpn_barcode' FROM return_jobs "
            f"WHERE {NOT_CONFORMING_JOB} ORDER BY 2"
        )).all()
        job_renamed = job_linked = 0
        for job_id, old_lpn in jobs:
            if old_lpn in remap:
                # 같은 물건의 재고 행이 이미 새 LPN을 받았다 - 그대로 따라간다.
                new_lpn = remap[old_lpn]
                job_linked += 1
            else:
                z = SEED_ZONES[job_renamed % len(SEED_ZONES)]
                seq[z] += 1
                new_lpn = f"LPN-{SENTINEL_DATE}-{z}{seq[z]:03d}"
            # JSONB 키 하나만 교체 (다른 키를 건드리지 않는다).
            # `:n::text` 형태는 SQLAlchemy 바인드 파서가 `::` 캐스트와 충돌해 구문 오류가
            # 나므로 CAST(...)를 쓴다.
            db.exec(
                text("UPDATE return_jobs SET agent_logs = CAST(jsonb_set("
                     "CAST(agent_logs AS jsonb), '{lpn_barcode}', to_jsonb(CAST(:n AS text))"
                     ") AS json) WHERE id = :i"),
                params={"n": new_lpn, "i": job_id},
            )
            tag = " (재고 연동)" if old_lpn in remap else ""
            print(f"  [job] {old_lpn:<24} -> {new_lpn}{tag}")
            job_renamed += 1

        db.commit()

    print()
    print(f"재고 {renamed}건 재채번 (그중 Zone A 탈출 {moved}건)")
    print(f"검수이력 {job_renamed}건 (그중 재고와 LPN 연동 유지 {job_linked}건)")
    print("백업: _bak_20260806_demo_lpn, _bak_20260806_demo_lpn_jobs")


if __name__ == "__main__":
    main()
