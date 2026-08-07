# -*- coding: utf-8 -*-
"""
실전 카오스 테스트 러너 — "워커가 죽어도 검수는 유실되지 않는다"의 실측 증명.

절차:
  1) 실제 검수 작업 N건을 /api/v1/inbound/evaluate로 투입 (실 GPT-4o 호출 발생, 비용 소모)
  2) 태스크 처리 중 `docker restart wms-secret-worker`로 워커 강제 재시작 (SIGTERM)
  3) 전 건이 터미널 상태(APPROVED/REJECTED/HITL_REQUIRED/FAILED)에 도달하는지 폴링
  4) Redis DLQ 잔량과 워커 재전달 로그를 수집해 JSON 리포트 저장

실행:  .venv/Scripts/python.exe scripts/chaos_test_runner.py [결과.json] [N=4]
전제:  docker compose 스택 기동(wms-secret-api :8000, wms-secret-worker, wms-secret-redis, wms-secret-postgres),
       .env OPENAI 키 유효, app/experiment_data/ 에 테스트 이미지 존재.

주의: 과거 scripts/debug/test_chaos.py는 print 시뮬레이터였다(실측 아님).
      본 러너가 실측 정본이며, 결과 JSON을 발표 증거로 사용한다.
"""
import base64
import io
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from sqlalchemy import create_engine, text

API = "http://localhost:8000"
DB_URL = "postgresql://admin:password@localhost:5432/wms_db"
IMAGE_DIR = Path("app/experiment_data")
TERMINAL = {"APPROVED", "REJECTED", "HITL_REQUIRED", "FAILED"}
POLL_TIMEOUT_SEC = 420
engine = create_engine(DB_URL)


def load_test_images() -> list[str]:
    imgs = sorted(IMAGE_DIR.glob("job-*/raw_*.jpg"))[:2]
    if len(imgs) < 2:
        raise SystemExit("테스트 이미지 2장이 필요합니다 (app/experiment_data)")
    return [base64.b64encode(p.read_bytes()).decode() for p in imgs]


def enqueue(n: int) -> list[dict]:
    images = load_test_images()
    with engine.connect() as c:
        isbn = c.execute(text("SELECT isbn FROM books ORDER BY created_at LIMIT 1")).scalar()
    jobs = []
    for i in range(n):
        body = json.dumps({
            "lpn": f"LPN-CHAOS-{datetime.now():%H%M%S}-{i+1:02d}",
            "images": images,
            "book_metadata": {"isbn": isbn, "title": f"카오스 테스트 {i+1}"},
        }).encode()
        req = urllib.request.Request(f"{API}/api/v1/inbound/evaluate", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=30) as res:
            job_id = json.loads(res.read())["job_id"]
        jobs.append({"job_id": job_id, "enqueued_at": t0})
        print(f"투입 {i+1}/{n}: job={job_id}")
    return jobs


def kill_worker() -> str:
    print(">>> 워커 강제 재시작 (docker restart wms-secret-worker)")
    t0 = time.time()
    subprocess.run(["docker", "restart", "wms-secret-worker"], check=True, capture_output=True)
    return f"restart took {time.time()-t0:.1f}s"


def poll(jobs: list[dict]) -> dict:
    ids = [j["job_id"] for j in jobs]
    deadline = time.time() + POLL_TIMEOUT_SEC
    while time.time() < deadline:
        with engine.connect() as c:
            rows = c.execute(text(
                "SELECT id::text, status, "
                "EXTRACT(EPOCH FROM (updated_at - created_at)) AS dur "
                "FROM return_jobs WHERE id::text = ANY(:ids)"), {"ids": ids}).fetchall()
        states = {r[0]: {"status": r[1], "duration_sec": round(float(r[2] or 0), 2)} for r in rows}
        done = sum(1 for s in states.values() if s["status"] in TERMINAL)
        print(f"  진행: {done}/{len(ids)} 터미널 도달 "
              f"({ {r[1] for r in rows} })")
        if done == len(ids):
            return states
        time.sleep(10)
    return states  # 타임아웃 시 현재 상태 반환


def dlq_length() -> int:
    out = subprocess.run(
        ["docker", "exec", "wms-secret-redis", "redis-cli", "LLEN", "wms:dlq:inspection_tasks"],
        capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


def worker_log_evidence() -> list[str]:
    out = subprocess.run(
        ["docker", "logs", "wms-secret-worker", "--since", "10m"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    keywords = ("Redlock", "already locked", "acks", "Restoring", "unacknowledged",
                "process_inspection", "restart", "Connected to redis")
    lines = [ln for ln in (out.stdout + out.stderr).splitlines()
             if any(k.lower() in ln.lower() for k in keywords)]
    return lines[-40:]


def main() -> None:
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    report: dict = {"started_at": datetime.now().isoformat(), "jobs_requested": n}

    dlq_before = dlq_length()
    jobs = enqueue(n)
    time.sleep(4)  # 워커가 태스크를 물기 시작할 시간
    report["kill"] = kill_worker()
    states = poll(jobs)

    report["results"] = states
    report["dlq_before"] = dlq_before
    report["dlq_after"] = dlq_length()
    terminal_count = sum(1 for s in states.values() if s["status"] in TERMINAL)
    report["verdict"] = {
        "jobs_enqueued": len(jobs),
        "jobs_terminal": terminal_count,
        "jobs_lost": len(jobs) - len(states),           # DB row 자체가 없으면 유실
        "jobs_stuck": len(states) - terminal_count,      # 타임아웃까지 비터미널
        "dlq_delta": report["dlq_after"] - dlq_before,
        "zero_data_loss": (len(jobs) == len(states) == terminal_count),
        "durations_sec": [s["duration_sec"] for s in states.values()],
    }
    report["worker_log_evidence"] = worker_log_evidence()
    report["finished_at"] = datetime.now().isoformat()

    out_text = json.dumps(report, ensure_ascii=False, indent=2)
    print(out_text[:2000])
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(out_text, encoding="utf-8")
        print(f"\n리포트 저장: {sys.argv[1]}")


if __name__ == "__main__":
    main()
