# -*- coding: utf-8 -*-
"""
Vision Agent 판독기 A/B — 현행 GPT-4o vs Claude Sonnet 5.

실행:
    docker exec -e ANTHROPIC_API_KEY=<키> wms-api python app/scripts/ab_vision_sonnet.py <job_id_prefix>

[무엇을 확인하려는가]
GPT-4o가 YOLO 제보를 그대로 결함으로 승격시키는 현상이 실측됐다(job b7b34ae1: 후보 5건과
확정 결함 5건의 BBox가 픽셀 단위로 100% 일치, 기각·추가 0건). 프롬프트에서 confidence를
제거하자 확신도 복사는 멈췄으나 좌표는 여전히 제보를 그대로 따랐다.

그래서 묻는 질문은 하나다 — **같은 이미지와 같은 프롬프트를 주면 다른 모델은 기각하거나
추가하는가?** 모델을 바꾸자는 제안이 아니라, 판독 실패가 모델 특성인지 프롬프트/제보 구조
탓인지를 가르기 위한 측정이다.

[공정한 비교를 위해 고정한 것]
- 이미지: 원본 검수에 쓰인 로컬 파일 3장을 그대로 사용 (재촬영·재압축 없음)
- 프롬프트: vision_agent가 쓰는 것과 동일한 텍스트를 import해서 사용 (재작성 금지)
- YOLO 제보: DB에 저장된 그 실행의 후보를 그대로 전달, confidence는 동일하게 제거
- 출력 스키마: 같은 VisionResult

[읽는 법]
'제보 대비' 줄이 핵심이다. 채택/기각/추가가 전부 0/0/0이면 그 모델도 통과 도장을 찍은 것이고,
기각이나 추가가 나오면 실제로 이미지를 본 것이다.
"""

import base64
import json
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text
from sqlmodel import Session

from app.db.session import engine
from app.ai.agents import VisionResult

MODEL = "claude-sonnet-5"  # 조장 지정. 기본값은 opus지만 이번 비교 대상은 sonnet이다.


def load_job(prefix: str):
    with Session(engine) as db:
        row = db.exec(
            text(
                "SELECT id::text, agent_logs::text FROM return_jobs "
                "WHERE id::text LIKE :p ORDER BY updated_at DESC LIMIT 1"
            ),
            params={"p": f"{prefix}%"},
        ).first()
    if not row:
        raise SystemExit(f"job을 찾지 못했습니다: {prefix}")
    return row[0], json.loads(row[1] or "{}")


def iou(a, b) -> float:
    """두 BBox의 겹침 비율. 좌표가 같은 곳을 가리키는지 판단하는 기준."""
    ix1, iy1 = max(a["xmin"], b["xmin"]), max(a["ymin"], b["ymin"])
    ix2, iy2 = min(a["xmax"], b["xmax"]), min(a["ymax"], b["ymax"])
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = (a["xmax"] - a["xmin"]) * (a["ymax"] - a["ymin"])
    area_b = (b["xmax"] - b["xmin"]) * (b["ymax"] - b["ymin"])
    return inter / float(area_a + area_b - inter)


def compare_to_candidates(defects, candidates, label: str) -> None:
    """확정 결함이 제보를 그대로 따랐는지 대조한다 (IoU 0.9 이상이면 동일 좌표로 본다)."""
    matched_cand = set()
    adopted = 0
    for d in defects:
        db_ = d.get("bbox") or {}
        hit = None
        for i, c in enumerate(candidates):
            cb = c.get("bbox") or {}
            if db_ and cb and iou(db_, cb) >= 0.9:
                hit = i
                break
        if hit is not None:
            matched_cand.add(hit)
            adopted += 1
    rejected = len(candidates) - len(matched_cand)
    added = len(defects) - adopted
    verdict = (
        "통과 도장 (판독 아님)" if (rejected == 0 and added == 0) else "실제 판독함"
    )
    print(
        f"  제보 대비 → 채택 {adopted} / 기각 {rejected} / 추가 {added}   ⇒ {verdict}"
    )


def main() -> None:
    prefix = sys.argv[1] if len(sys.argv) > 1 else "b7b34ae1"
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key and not auth_token:
        raise SystemExit(
            "인증 정보가 없습니다. 둘 중 하나로 실행하세요.\n"
            "  ① API 키:  docker exec -e ANTHROPIC_API_KEY=<키> wms-api python app/scripts/ab_vision_sonnet.py\n"
            "  ② CLI 로그인 토큰:\n"
            "     ant auth login\n"
            "     docker exec -e ANTHROPIC_AUTH_TOKEN=$(ant auth print-credentials --access-token) \\\n"
            "       wms-api python app/scripts/ab_vision_sonnet.py"
        )

    import anthropic
    from app.ai.agents import (
        VISION_PROMPT_BASE,
        build_yolo_hint,
    )  # 현행 원문 (복제 아님)

    job_id, logs = load_job(prefix)
    paths = (
        json.loads(logs.get("local_image_paths") or "[]")
        if isinstance(logs.get("local_image_paths"), str)
        else (logs.get("local_image_paths") or [])
    )
    candidates = logs.get("yolo_candidates") or []
    gpt_defects = logs.get("defects") or []

    print(f"대상 job : {job_id}")
    print(f"LPN      : {logs.get('lpn_barcode')}")
    print(f"이미지   : {len(paths)}장")
    print(f"YOLO 제보: {len(candidates)}건\n")

    print(f"[기준] 현행 GPT-4o — 결함 {len(gpt_defects)}건")
    compare_to_candidates(gpt_defects, candidates, "gpt-4o")

    # --- Claude 호출 ---
    # 제보에서 confidence를 빼는 것은 vision_agent와 동일한 처리다 (베끼기 방지).
    hint_items = [{k: v for k, v in c.items() if k != "confidence"} for c in candidates]
    yolo_hint = build_yolo_hint(hint_items)
    content = [{"type": "text", "text": VISION_PROMPT_BASE + yolo_hint}]
    for i, p in enumerate(paths):
        if not os.path.exists(p):
            print(f"  !! 이미지 없음: {p}")
            continue
        with open(p, "rb") as f:
            content.append({"type": "text", "text": f"[이미지 index={i}]"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(f.read()).decode(),
                    },
                }
            )

    # OAuth 토큰(`ant auth login`)은 x-api-key가 아니라 Authorization: Bearer로 가고,
    # oauth 베타 헤더를 함께 보내야 /v1/messages가 받는다. API 키면 그냥 기본 경로다.
    if api_key:
        client = anthropic.Anthropic(api_key=api_key)
    else:
        client = anthropic.Anthropic(
            auth_token=auth_token,
            default_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_format=VisionResult,
        messages=[{"role": "user", "content": content}],
    )

    if resp.stop_reason == "refusal":
        raise SystemExit(f"거절됨: {getattr(resp, 'stop_details', None)}")

    result = resp.parsed_output
    cl_defects = [d.model_dump() for d in (result.defects or [])]

    print(f"\n[비교] {MODEL} — 결함 {len(cl_defects)}건")
    compare_to_candidates(cl_defects, candidates, MODEL)
    for d in cl_defects:
        b = d.get("bbox") or {}
        print(
            f"    {d.get('type')}  conf={d.get('confidence')}  ratio={d.get('ratio')}  "
            f"img={d.get('image_index')}  bbox=({b.get('xmin')},{b.get('ymin')},{b.get('xmax')},{b.get('ymax')})"
        )

    u = resp.usage
    # 프로모 단가 $2/$10 per MTok (2026-08-31까지). 이후 $3/$15.
    cost = (u.input_tokens / 1e6) * 2.0 + (u.output_tokens / 1e6) * 10.0
    print(f"\n토큰: 입력 {u.input_tokens:,} / 출력 {u.output_tokens:,}")
    print(f"비용: ${cost:.4f} (프로모 단가 기준, 약 {cost * 1400:.0f}원)")


if __name__ == "__main__":
    main()
