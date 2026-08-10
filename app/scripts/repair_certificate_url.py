"""certificate_url 을 LPN 기반으로 교정한다.

실행:  docker exec wms-secret-api python app/scripts/repair_certificate_url.py [--apply]

보증서 라우트는 /certificate/[lpn] 이므로 CERT-* 형식 링크는 조회되지 않는다(404).
종전 생성 로직이 날짜까지 하드코딩(CERT-20260728-)해 실제 발급 ID와도 어긋났다.
"""
import argparse
import os
import re
import sys

from sqlalchemy import create_engine, text

BAD = re.compile(r"/certificate/CERT-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 갱신 (기본은 미리보기)")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL 이 없습니다.")
        return 1
    engine = create_engine(url.replace("+asyncpg", ""))

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, lpn_barcode, certificate_url FROM inventory_used_items "
            "WHERE certificate_url IS NOT NULL AND certificate_url LIKE '/certificate/CERT-%' "
            "ORDER BY lpn_barcode"
        )).fetchall()

        if not rows:
            print("교정 대상 없음 — 모든 certificate_url 이 LPN 기반입니다.")
            return 0

        print(f"교정 대상 {len(rows)}건" + ("" if args.apply else "  (미리보기 — 적용하려면 --apply)"))
        skipped = 0
        for rid, lpn, cur in rows:
            if not lpn or not str(lpn).startswith("LPN-"):
                # LPN이 없으면 만들 수 있는 정본 링크가 없다. 임의로 만들지 않는다.
                print(f"  건너뜀 (LPN 없음) {rid} {cur}")
                skipped += 1
                continue
            new = f"/certificate/{lpn}"
            print(f"  {lpn}  {cur}  ->  {new}")
            if args.apply:
                conn.execute(
                    text("UPDATE inventory_used_items SET certificate_url = :u WHERE id = :i"),
                    {"u": new, "i": rid},
                )

        done = len(rows) - skipped
        print(f"\n{'갱신 완료' if args.apply else '갱신 예정'}: {done}건 / 건너뜀 {skipped}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
