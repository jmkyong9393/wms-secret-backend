# -*- coding: utf-8 -*-
"""
books.category_type 정규화 — 알라딘 실조회 기반.

[배경] 대시보드 "카테고리별 재고 자산 보유 현황" 차트가 막대 10개로 흩어져 읽히지 않았다.
원인은 category_type에 세 종류의 값이 섞여 있었기 때문이다.

  1) 알라딘 실조회값 (정상)   : 컴퓨터/모바일, 대학교재/전문서적, 컴퓨터/IT
  2) 영문 시드 데이터 (비표준) : IT, Novel, Economy, Essay, Self-help, Textbook
  3) 미분류 폴백              : GENERAL (32건, 전체의 65%)
  4) 데이터 오염              : 책 제목이 통째로 카테고리에 들어간 행 1건

서빙 코드(app/domains/inbound/router.py)는 알라딘 categoryName의 **2단계**를 쓴다.
  "국내도서>컴퓨터/모바일>웹디자인/홈페이지>HTML/JavaScript"  ->  "컴퓨터/모바일"
이 스크립트도 동일 규칙을 적용해 저장값을 서빙 규칙과 일치시킨다.

[처리]
  - ISBN이 유효한 모든 도서를 알라딘에 재조회해 2단계 장르로 갱신
  - 조회 실패분만 영문 시드값을 알라딘 표기로 매핑 (아래 FALLBACK_MAP)
  - 실행 전 _bak_20260806_books_category 에 원본을 백업

멱등하다 - 여러 번 돌려도 결과가 같다.
"""
import asyncio
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text
from sqlmodel import Session, select

from app.db.session import engine
from app.domains.inbound.service import lookup_book_by_isbn
from app.models.wms import Book

# 알라딘 조회가 실패한 건에만 적용하는 최소 매핑.
# 영문 시드값을 알라딘 국내도서 2단계 표기로 옮긴다.
FALLBACK_MAP = {
    "IT": "컴퓨터/모바일",
    "컴퓨터/IT": "컴퓨터/모바일",
    "Textbook": "대학교재/전문서적",
    "Novel": "소설/시/희곡",
    "Economy": "경제경영",
    "Essay": "에세이",
    "Self-help": "자기계발",
}
UNCLASSIFIED = "미분류"


def parse_aladin_level2(category_name: str) -> str | None:
    """서빙 코드와 동일 규칙: categoryName의 2단계를 장르로 쓴다."""
    parts = [p.strip() for p in (category_name or "").split(">") if p.strip()]
    if len(parts) > 1:
        return parts[1]
    return parts[0] if parts else None


async def main() -> None:
    with Session(engine) as s:
        s.exec(text(
            "CREATE TABLE IF NOT EXISTS _bak_20260806_books_category AS "
            "SELECT id, isbn, title, category_type FROM books"
        ))
        s.commit()
        books = s.exec(select(Book)).all()
        print(f"대상 도서 {len(books)}권 (백업: _bak_20260806_books_category)")

    updated = fallback = failed = 0
    results: list[tuple[str, str, str]] = []

    for b in books:
        before = b.category_type or ""
        after = None

        isbn = (b.isbn or "").strip()
        if isbn.isdigit() and len(isbn) == 13:
            try:
                meta = await lookup_book_by_isbn(isbn) or {}
                after = parse_aladin_level2(meta.get("categoryName", ""))
            except Exception as e:
                print(f"  [조회 실패] {isbn} {e}")

        if not after:
            # 조회 실패 시에만 시드값 매핑. 매핑에도 없으면 미분류로 모은다.
            after = FALLBACK_MAP.get(before)
            if after:
                fallback += 1
            else:
                after = UNCLASSIFIED
                failed += 1

        if after and after != before:
            results.append((isbn, before, after))
            updated += 1

    with Session(engine) as s:
        for isbn, _before, after in results:
            s.exec(
                text("UPDATE books SET category_type = :c WHERE isbn = :i"),
                params={"c": after, "i": isbn},
            )
        s.commit()

    print()
    print(f"갱신 {updated}건 (알라딘 조회 {updated - fallback - failed} / 시드 매핑 {fallback} / 미분류 {failed})")
    for isbn, before, after in results[:15]:
        print(f"  {isbn}  {before[:24]:<26} -> {after}")
    if len(results) > 15:
        print(f"  ... 외 {len(results) - 15}건")


if __name__ == "__main__":
    asyncio.run(main())
