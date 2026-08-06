# -*- coding: utf-8 -*-
"""
발표용 정량 지표 집계 스크립트 (S14 성과 슬라이드 재료).

실행:  .venv/Scripts/python.exe scripts/measure_demo_metrics.py [출력파일.json]

시연 DB(return_jobs / inventory_used_items / orders)에서 다음을 실측 집계한다:
  - 검수 상태 분포, AI 자동 승인율, HITL 이관율, 반려율
  - 검수 소요 시간(터미널 상태 도달 기준): 평균/중앙값/P95
  - 재고·주문 규모, UBCI 등급 분포
  - LLM 비용: 계측 데이터가 없어(agent_logs에 usage 부재) 근거 명시형 추정치로 산출

주의: LLM 비용의 단가·토큰 가정은 ASSUMPTIONS 딕셔너리에 명시 — 발표에 쓸 때
"추정치"임을 반드시 병기하고, 정밀값이 필요하면 파이프라인에 usage 계측을 먼저 추가할 것.
"""
import io
import json
import statistics
import sys
from datetime import datetime

# Windows 콘솔(cp949)에서도 안전하게 출력
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")

from sqlalchemy import create_engine, text

DB_URL = "postgresql://admin:password@localhost:5432/wms_db"
engine = create_engine(DB_URL)

# LLM 비용 추정 가정 (발표 시 병기 필수)
ASSUMPTIONS = {
    "gpt4o_input_per_1m_usd": 2.50,
    "gpt4o_output_per_1m_usd": 10.00,
    "gpt4o_mini_input_per_1m_usd": 0.15,
    "gpt4o_mini_output_per_1m_usd": 0.60,
    "usd_krw": 1400,
    # 검수 1건 가정: Vision(GPT-4o) 이미지 2장+프롬프트 ≈ 2,600 in / 700 out,
    # Critic Stage B·Report(4o-mini 각 1회) ≈ 1,500 in / 500 out 합산
    "per_book_gpt4o_in": 2600,
    "per_book_gpt4o_out": 700,
    "per_book_mini_in": 1500,
    "per_book_mini_out": 500,
    "note": "결함 0건(MINT)이면 Critic Stage B 미호출이라 실제 평균은 이보다 낮음(상한 추정)",
}


def q(sql: str):
    with engine.connect() as conn:
        return conn.execute(text(sql)).fetchall()


def main() -> None:
    out: dict = {"measured_at": datetime.now().isoformat(), "source_db": "wms_db (시연 DB)"}

    # 1) 검수 상태 분포 및 비율
    rows = q("SELECT status, count(*) FROM return_jobs GROUP BY status")
    dist = {r[0]: r[1] for r in rows}
    terminal = {k: v for k, v in dist.items() if k in ("APPROVED", "REJECTED", "FAILED")}
    total_done = sum(terminal.values())
    hitl = dist.get("HITL_REQUIRED", 0)
    total_judged = total_done + hitl
    out["inspection"] = {
        "status_distribution": dist,
        "auto_approval_rate_pct": round(dist.get("APPROVED", 0) / total_judged * 100, 1) if total_judged else None,
        "hitl_escalation_rate_pct": round(hitl / total_judged * 100, 1) if total_judged else None,
        "reject_rate_pct": round(dist.get("REJECTED", 0) / total_judged * 100, 1) if total_judged else None,
        "definition": "auto_approval=APPROVED/(터미널+HITL), hitl=HITL_REQUIRED/(터미널+HITL)",
    }

    # 2) 검수 소요 시간 (터미널 상태 도달 기준, created_at→updated_at)
    rows = q("""
        SELECT EXTRACT(EPOCH FROM (updated_at - created_at)) AS sec
        FROM return_jobs
        WHERE status IN ('APPROVED','REJECTED')
          AND updated_at > created_at
          AND updated_at - created_at < interval '10 minutes'
    """)
    secs = sorted(float(r[0]) for r in rows)
    if secs:
        out["inspection_duration_sec"] = {
            "n": len(secs),
            "avg": round(statistics.mean(secs), 2),
            "median": round(statistics.median(secs), 2),
            "p95": round(secs[max(0, int(len(secs) * 0.95) - 1)], 2),
            "min": round(secs[0], 2),
            "max": round(secs[-1], 2),
            "caveat": "created_at→updated_at 구간이라 큐 대기·HITL 대기 이전까지 포함. 시드 데이터 제외 필요 시 lpn NOT LIKE 'LPN-260731-%' 필터 재실행",
        }

    # 3) 규모 지표
    out["scale"] = {
        "used_inventory_items": q("SELECT count(*) FROM inventory_used_items")[0][0],
        "books_master": q("SELECT count(*) FROM books")[0][0],
        "orders": q("SELECT count(*) FROM orders")[0][0],
        "grade_distribution": {r[0]: r[1] for r in q(
            "SELECT condition_grade, count(*) FROM inventory_used_items GROUP BY condition_grade")},
        "ubci_avg": float(q("SELECT round(avg(ubci_score),1) FROM inventory_used_items WHERE ubci_score IS NOT NULL")[0][0] or 0),
    }

    # 4) LLM 비용 추정 (계측 부재 — 상한 가정 명시)
    a = ASSUMPTIONS
    cost_usd = (
        a["per_book_gpt4o_in"] / 1e6 * a["gpt4o_input_per_1m_usd"]
        + a["per_book_gpt4o_out"] / 1e6 * a["gpt4o_output_per_1m_usd"]
        + a["per_book_mini_in"] / 1e6 * a["gpt4o_mini_input_per_1m_usd"]
        + a["per_book_mini_out"] / 1e6 * a["gpt4o_mini_output_per_1m_usd"]
    )
    out["llm_cost_estimate"] = {
        "per_book_usd": round(cost_usd, 4),
        "per_book_krw": round(cost_usd * a["usd_krw"], 1),
        "assumptions": a,
        "status": "ESTIMATE — agent_logs에 토큰 usage 계측 없음. 정밀값은 usage 계측 추가 후 재산출",
    }

    text_out = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    print(text_out)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as f:
            f.write(text_out)


if __name__ == "__main__":
    main()
