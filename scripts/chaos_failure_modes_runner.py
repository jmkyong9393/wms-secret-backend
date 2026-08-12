# -*- coding: utf-8 -*-
"""카오스 테스트 — 실패 유형 확장 러너.

`chaos_test_runner.py`는 워커 SIGTERM 단일 유형만 다룬다. 같은 킬을 반복해도
검증되는 메커니즘(acks_late + 원장 스위퍼)이 같아 증거가 늘지 않으므로,
**끊는 지점을 바꿔** 서로 다른 복구 경로를 확인한다.

유형:
  worker   : 워커 컨테이너 재시작 (기존 유형, 회귀 확인용)
  redis    : 브로커(Redis) 재시작 — 큐 자체가 사라졌을 때 원장 스위퍼가 복구하는지
  postgres : 원장 DB 재시작 — 커넥션 단절 중 태스크가 죽지 않고 재시도되는지
  late     : 워커를 늦게(추론 진행 후) 끊음 — 초기 수신 직후가 아닌 처리 중반 킬

실행:  .venv/Scripts/python.exe scripts/chaos_failure_modes_runner.py <출력.json> [유형=redis] [건수=2] [킬지연초=4]
전제:  docker compose 스택 기동, .env OPENAI 키 유효, app/experiment_data/ 이미지 존재.
주의:  실 GPT-4o 호출이 발생한다(건당 비용).
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

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, ".")

from sqlalchemy import create_engine, text

API = "http://localhost:8000"
DB_URL = "postgresql://admin:password@localhost:5432/wms_db"
IMAGE_DIR = Path("app/experiment_data")
TERMINAL = {"APPROVED", "REJECTED", "HITL_REQUIRED", "FAILED"}
POLL_TIMEOUT_SEC = 600
engine = create_engine(DB_URL, pool_pre_ping=True)

# 유형별 대상 컨테이너 — 끊는 계층이 곧 검증하려는 복구 경로다
TARGET = {
    "worker": "wms-secret-worker",
    "redis": "wms-secret-redis",
    "postgres": "wms-secret-postgres",
    "late": "wms-secret-worker",
}


def load_test_images() -> list[str]:
    imgs = sorted(IMAGE_DIR.glob("job-*/raw_*.jpg"))[:2]
    if len(imgs) < 2:
        raise SystemExit("테스트 이미지 2장이 필요합니다 (app/experiment_data)")
    return [base64.b64encode(p.read_bytes()).decode() for p in imgs]


def enqueue(n: int, mode: str) -> list[dict]:
    images = load_test_images()
    with engine.connect() as c:
        isbn = c.execute(text("SELECT isbn FROM books ORDER BY created_at LIMIT 1")).scalar()
    jobs = []
    for i in range(n):
        body = json.dumps({
            "lpn": f"LPN-CHAOS{mode[:3].upper()}-{datetime.now():%H%M%S}-{i+1:02d}",
            "images": images,
            "book_metadata": {"isbn": isbn, "title": f"카오스[{mode}] {i+1}"},
        }).encode()
        req = urllib.request.Request(f"{API}/api/v1/inbound/evaluate", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=30) as res:
            job_id = json.loads(res.read())["job_id"]
        jobs.append({"job_id": job_id})
        print(f"투입 {i+1}/{n}: job={job_id}")
    return jobs


def disrupt(mode: str) -> str:
    target = TARGET[mode]
    print(f">>> [{mode}] {target} 재시작")
    t0 = time.time()
    subprocess.run(["docker", "restart", target], check=True, capture_output=True)
    took = time.time() - t0
    # 브로커/DB는 기동 완료까지 몇 초 더 필요하다 — 그 사이 워커의 재연결 동작도 관찰 대상이다
    if mode in ("redis", "postgres"):
        time.sleep(8)
    return f"{target} restart took {took:.1f}s"


def poll(jobs: list[dict]) -> dict:
    ids = [j["job_id"] for j in jobs]
    deadline = time.time() + POLL_TIMEOUT_SEC
    states: dict = {}
    while time.time() < deadline:
        try:
            with engine.connect() as c:
                rows = c.execute(text(
                    "SELECT id::text, status, "
                    "EXTRACT(EPOCH FROM (updated_at - created_at)) AS dur "
                    "FROM return_jobs WHERE id::text = ANY(:ids)"), {"ids": ids}).fetchall()
        except Exception as e:
            # postgres 유형에서는 폴링 자체가 실패할 수 있다 — 재연결까지 기다린다
            print(f"  (DB 폴링 대기: {type(e).__name__})")
            time.sleep(5)
            continue
        states = {r[0]: {"status": r[1], "duration_sec": round(float(r[2] or 0), 2)} for r in rows}
        done = sum(1 for s in states.values() if s["status"] in TERMINAL)
        print(f"  진행: {done}/{len(ids)} 터미널 ({sorted({s['status'] for s in states.values()})})")
        if done == len(ids):
            return states
        time.sleep(10)
    return states


def dlq_length() -> int:
    out = subprocess.run(
        ["docker", "exec", "wms-secret-redis", "redis-cli", "LLEN", "wms:dlq:inspection_tasks"],
        capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


def worker_log_evidence() -> list[str]:
    out = subprocess.run(["docker", "logs", "wms-secret-worker", "--since", "15m"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    keys = ("Restoring", "unacknowledged", "스위퍼", "sweeper", "reconnect", "Connected to redis",
            "process_inspection", "OperationalError", "Redlock")
    lines = [ln for ln in (out.stdout + out.stderr).splitlines()
             if any(k.lower() in ln.lower() for k in keys)]
    return lines[-40:]


def main() -> None:
    out_path = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "redis"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    kill_delay = float(sys.argv[4]) if len(sys.argv) > 4 else 4.0
    if mode not in TARGET:
        raise SystemExit(f"유형은 {list(TARGET)} 중 하나여야 합니다")

    report: dict = {
        "started_at": datetime.now().isoformat(),
        "failure_mode": mode,
        "target_container": TARGET[mode],
        "jobs_requested": n,
        "kill_delay_sec": kill_delay,
    }
    dlq_before = dlq_length()
    jobs = enqueue(n, mode)
    time.sleep(kill_delay)
    report["disruption"] = disrupt(mode)
    states = poll(jobs)

    terminal = sum(1 for s in states.values() if s["status"] in TERMINAL)
    report["results"] = states
    report["dlq_before"] = dlq_before
    report["dlq_after"] = dlq_length()
    report["verdict"] = {
        "jobs_enqueued": len(jobs),
        "jobs_rows_present": len(states),
        "jobs_terminal": terminal,
        "jobs_lost": len(jobs) - len(states),
        "jobs_stuck": len(states) - terminal,
        "dlq_delta": report["dlq_after"] - dlq_before,
        "zero_data_loss": (len(jobs) == len(states) == terminal),
    }
    report["worker_log_evidence"] = worker_log_evidence()
    report["finished_at"] = datetime.now().isoformat()

    Path(out_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[{mode}] 판정: {report['verdict']}")
    print(f"리포트 저장: {out_path}")


if __name__ == "__main__":
    main()
