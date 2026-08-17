# -*- coding: utf-8 -*-
"""동시성 부하 테스트 — 재고 정합성과 분산 락을 실제 요청으로 검증한다.

문서에 "1,000건 동시 요청 시 Race Condition 0건"이 적혀 있었으나 근거는 4건 규모
카오스 테스트뿐이었다. 그 공백을 메운다.

## 두 축

**A. 재고 증가 경합** — `POST /inbound/fasttrack`을 같은 ISBN으로 N건 동시 전송.
   LLM을 타지 않으므로 비용 0이고, 재고 수량이 정확히 N만큼 늘었는지로
   lost update(갱신 유실)를 검출한다.

**B. 분산 락 경합** — 같은 `return_job_id`를 N회 동시 재검수 큐잉.
   Redlock이 1건만 통과시키고 나머지를 SKIPPED로 되돌리는지 본다.
   LLM 비용은 1건분만 발생한다.

## 왜 "서로 다른 1,000건"이 아닌가

서로 다른 건을 1,000개 넣으면 GPT-4o가 1,000회 호출된다(약 $36). 게다가 그것은
처리량 측정이지 **경합 측정이 아니다.** 락과 재고 갱신은 *같은 자원*을 동시에
건드릴 때만 깨진다.

**로컬 스택 전용.** 운영에 돌리면 실제 재고가 늘어난다.
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.getenv("LOAD_BASE", "http://localhost:8000")
if "localhost" not in BASE and "127.0.0.1" not in BASE:
    raise SystemExit(f"로컬 전용이다. 운영({BASE})에 실행하면 실제 재고가 변한다.")


def _req(method, path, body=None, cookie=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}", None


def login():
    st, _, hdrs = _req("POST", "/api/v1/auth/login",
                       {"employee_id": "WM2608001", "password": "1234"})
    if st != 200:
        raise SystemExit(f"로그인 실패 HTTP {st}")
    cookies = hdrs.get_all("Set-Cookie") or []
    return "; ".join(c.split(";")[0] for c in cookies)


def fire(n, worker, label):
    """n개 요청을 동시에 쏘고 (상태코드 분포, 소요초)를 돌려준다."""
    results = [None] * n
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()          # 전원이 준비될 때까지 대기 → 실제 동시 발사
        results[i] = worker(i)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    sec = time.perf_counter() - t0
    dist = Counter(results)
    print(f"\n[{label}] {n}건 동시 발사 — {sec:.2f}초 ({n/sec:.1f} req/s)")
    for code, cnt in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"    HTTP {code} : {cnt}건")
    return dist, sec


def stock_of(isbn):
    """해당 ISBN의 신품 재고 합계."""
    from sqlalchemy import text
    from app.db.session import engine
    with engine.connect() as c:
        return c.execute(text(
            "select coalesce(sum(i.quantity),0) from inventory i "
            "join books b on b.id = i.book_id where b.isbn = :isbn"
        ), {"isbn": isbn}).scalar() or 0


def test_a(n, isbn, cookie):
    print("=" * 72)
    print(f"A. 재고 증가 경합 — 같은 ISBN({isbn})에 fasttrack {n}건 동시")
    print("=" * 72)
    before = stock_of(isbn)
    print(f"  검사 전 재고: {before}권")

    dist, sec = fire(n, lambda i: _req(
        "POST", "/api/v1/inbound/fasttrack",
        {"isbn": isbn, "qty": 1, "worker_id": "LOADTEST"}, cookie)[0], "A")

    time.sleep(2)  # 커밋 반영 여유
    after = stock_of(isbn)
    ok = dist.get(200, 0) + dist.get(201, 0)
    delta = after - before
    print(f"  검사 후 재고: {after}권  (증가 {delta} / 성공 응답 {ok})")
    verdict = "정합" if delta == ok else f"불일치 — lost update {ok - delta}건"
    print(f"  판정: {verdict}")
    return {"before": before, "after": after, "delta": delta, "ok": ok,
            "dist": dict(dist), "sec": round(sec, 2), "verdict": verdict}


def test_b(n, job_id, cookie):
    print("\n" + "=" * 72)
    print(f"B. 분산 락 경합 — 같은 job({job_id[:8]}…) 재검수 {n}회 동시")
    print("=" * 72)
    dist, sec = fire(n, lambda i: _req(
        "POST", f"/api/v1/admin/hitl/{job_id}/re-inspect", None, cookie)[0], "B")
    print("  ※ 큐잉 응답이다. 실제 중복 차단은 워커 로그의 SKIPPED로 확인한다.")
    return {"dist": dict(dist), "sec": round(sec, 2)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=100, help="동시 요청 수")
    ap.add_argument("--isbn", default="9788966262281")
    ap.add_argument("--job", default=None, help="락 경합 테스트용 return_job_id")
    a = ap.parse_args()

    ck = login()
    out = {"base": BASE, "n": a.n, "A": test_a(a.n, a.isbn, ck)}
    if a.job:
        out["B"] = test_b(min(a.n, 20), a.job, ck)
    print("\n" + json.dumps(out, ensure_ascii=False, indent=2))
