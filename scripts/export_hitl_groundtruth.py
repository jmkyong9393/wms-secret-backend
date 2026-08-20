"""HITL 정답지 추출 — DB를 밀기 전에 되살릴 수 없는 것만 뽑아 파일로 남긴다.

관리자가 직접 그린 BBox와 오탐 지목은 재생산이 불가능하다. 검수 결과 자체는
재검수로 다시 만들 수 있지만(GPT-4o 비용 발생), 사람의 판단은 그렇지 않다.
YOLO 재학습 정답지로 바로 쓸 수 있는 형태(정규화 0~1)까지 함께 만든다.

    python scripts/export_hitl_groundtruth.py
    python scripts/export_hitl_groundtruth.py --out D:/backup --container wms-secret-postgres

이미지 실물은 S3에 있다. 이 스크립트는 URL만 남기므로, 버킷을 비우기 전이라면
labels.jsonl의 image_url로 `aws s3 cp` 하면 된다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

# 좌표는 0~1000 정규화계다 (프리즈 문서 "판독 좌표계 원칙").
COORD_SCALE = 1000.0

KEEP_KEYS = ["hitl_bbox_edit", "human_feedback", "admin_decision", "certificate"]

# 두 갈래를 합친다.
#   valuable : 사람 손이 닿은 행 (정답지·피드백·결재·보증서) + 비정상 종료 건
#   rep      : 고유 image_urls 조합당 최신 1건 — 실촬영 표본 자체가 재생산 불가다
#              (2,700여 행 대부분은 같은 이미지를 복제한 것이라 표본 수가 아니다)
SQL = """
SELECT coalesce(jsonb_agg(t ORDER BY t.created_at), '[]'::jsonb) FROM (
  WITH valuable AS (
    SELECT id FROM return_jobs
    WHERE agent_logs ?| array['hitl_bbox_edit','human_feedback','admin_decision','certificate']
       OR status <> 'APPROVED'
  ), rep AS (
    SELECT DISTINCT ON (image_urls::text) id FROM return_jobs
    ORDER BY image_urls::text, created_at DESC
  )
  SELECT r.id::text, r.status, r.created_at::text, r.image_urls, r.agent_logs,
         (r.id IN (SELECT id FROM valuable)) AS human_touched
  FROM return_jobs r
  WHERE r.id IN (SELECT id FROM valuable UNION SELECT id FROM rep)
) t;
"""


def fetch(container: str, db: str, user: str) -> list[dict]:
    """psql로 JSON 한 덩어리를 받아온다. -tA는 헤더·정렬 없는 raw 출력이다."""
    proc = subprocess.run(
        ["docker", "exec", container, "psql", "-U", user, "-d", db, "-tAc", SQL],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        sys.exit(f"[중단] psql 실패:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout.strip() or "[]")


def to_yolo(box: dict) -> dict | None:
    """0~1000 정규화 좌표를 YOLO 형식(중심 x,y + 너비,높이 / 0~1)으로 바꾼다."""
    try:
        x1, y1 = float(box["xmin"]), float(box["ymin"])
        x2, y2 = float(box["xmax"]), float(box["ymax"])
    except (KeyError, TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return {
        "cx": round((x1 + x2) / 2 / COORD_SCALE, 6),
        "cy": round((y1 + y2) / 2 / COORD_SCALE, 6),
        "w": round((x2 - x1) / COORD_SCALE, 6),
        "h": round((y2 - y1) / COORD_SCALE, 6),
        "xyxy_1000": [x1, y1, x2, y2],
    }


def image_of(urls, idx) -> str | None:
    if not isinstance(urls, list) or idx is None:
        return None
    try:
        return urls[int(idx)]
    except (IndexError, TypeError, ValueError):
        return None


def extract(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """정답 라벨과 오탐(hard negative)을 분리해 뽑는다."""
    labels: list[dict] = []
    negatives: list[dict] = []

    for r in rows:
        logs = r.get("agent_logs") or {}
        urls = r.get("image_urls") or []
        lpn = logs.get("lpn_barcode") or r["id"]
        defects = logs.get("defects") or []
        edit = logs.get("hitl_bbox_edit") or {}

        # ① 관리자가 직접 그린 BBox — 가장 강한 정답. 재생산 불가.
        for b in edit.get("added_bboxes") or []:
            y = to_yolo(b)
            if y:
                labels.append({
                    "lpn": lpn, "source": "human_added", "cls": b.get("type"),
                    "image_index": b.get("imageIndex"),
                    "image_url": image_of(urls, b.get("imageIndex")), **y,
                })

        # ② 관리자가 좌표를 고친 것 — 유형은 원래 defect에서 가져온다.
        for b in edit.get("edited_bboxes") or []:
            y = to_yolo(b)
            if not y:
                continue
            i = b.get("index")
            src = defects[i] if isinstance(i, int) and i < len(defects) else {}
            labels.append({
                "lpn": lpn, "source": "human_edited", "cls": src.get("type"),
                "image_index": src.get("image_index"),
                "image_url": image_of(urls, src.get("image_index")), **y,
            })

        # ③ 관리자가 승인한 AI 좌표.
        excluded = set(edit.get("excluded") or [])
        for i, d in enumerate(defects):
            box = d.get("bbox")
            if not isinstance(box, dict):
                continue
            y = to_yolo(box)
            if not y:
                continue
            rec = {
                "lpn": lpn, "cls": d.get("type"),
                "image_index": d.get("image_index"),
                "image_url": image_of(urls, d.get("image_index")),
                "confidence": d.get("confidence"), **y,
            }
            # ④ 오탐 지목분은 재학습의 hard negative다. 버리면 같은 오탐을 다시 배운다.
            if i in excluded or d.get("hitl_excluded"):
                negatives.append({**rec, "source": "human_rejected"})
            elif edit:
                labels.append({**rec, "source": "human_confirmed"})

    return labels, negatives


def main() -> None:
    ap = argparse.ArgumentParser(description="HITL 정답지 추출")
    ap.add_argument("--out", default=None, help="출력 폴더 (기본: _groundtruth_export/<오늘>)")
    ap.add_argument("--container", default="wms-secret-postgres")
    ap.add_argument("--db", default="wms_db")
    ap.add_argument("--user", default="admin")
    args = ap.parse_args()

    out = Path(args.out or f"_groundtruth_export/{date.today().isoformat()}")
    out.mkdir(parents=True, exist_ok=True)

    rows = fetch(args.container, args.db, args.user)
    if not rows:
        sys.exit("[중단] 보존 대상 행이 0건이다. 컨테이너·DB 이름을 확인한다.")
    labels, negatives = extract(rows)

    (out / "groundtruth_raw.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, data in (("labels.jsonl", labels), ("false_positives.jsonl", negatives)):
        with (out / name).open("w", encoding="utf-8") as f:
            for rec in data:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    touched = sum(1 for r in rows if r.get("human_touched"))
    by_src = Counter(x["source"] for x in labels)
    by_cls = Counter(x["cls"] for x in labels if x.get("cls"))
    imgs = {x["image_url"] for x in labels + negatives if x.get("image_url")}
    all_imgs = {u for r in rows for u in (r.get("image_urls") or []) if u}

    (out / "SUMMARY.md").write_text(f"""# HITL 정답지 추출 결과

> 생성: {date.today().isoformat()} · 원본: 로컬 `{args.db}` (`{args.container}`)

## 무엇이 들어 있나

| 파일 | 내용 |
|---|---|
| `groundtruth_raw.json` | 보존 대상 행 {len(rows)}건 (사람 개입 {touched}건 + 고유 이미지 표본) |
| `labels.jsonl` | 정답 BBox {len(labels)}건 — YOLO 형식(0~1 정규화) |
| `false_positives.jsonl` | 사람이 오탐으로 지목한 {len(negatives)}건 — hard negative |

## 라벨 출처별

| 출처 | 건수 | 의미 |
|---|---:|---|
| `human_added` | {by_src.get('human_added', 0)} | 관리자가 직접 그림 — **재생산 불가** |
| `human_edited` | {by_src.get('human_edited', 0)} | AI 좌표를 관리자가 수정 |
| `human_confirmed` | {by_src.get('human_confirmed', 0)} | 관리자가 승인한 AI 좌표 |

## 결함 유형별

{chr(10).join(f'- `{k}` : {v}건' for k, v in by_cls.most_common()) or '- (없음)'}

## 이미지

보존 대상이 참조하는 이미지는 **{len(all_imgs)}장**이고, 그중 BBox 라벨이 붙은 것은
**{len(imgs)}장**이다. 실물은 S3(`wms-secret-vision-assets`)에 있고 여기에는 URL만 있다.
버킷을 비울 예정이면 먼저 내려받는다.

```bash
aws s3 sync s3://wms-secret-vision-assets ./s3-backup/vision-assets
```

## 좌표계

원본은 **0~1000 정규화**다. `cx/cy/w/h`는 YOLO 표준인 0~1로 변환한 값이고,
`xyxy_1000`에 원본 좌표를 함께 남겼다.

## 왜 오탐도 남기나

마모 탐지기 정밀도가 10~13%로 측정됐다(후보 23건 중 실제 2~3건). 정답만 학습시키면
같은 오탐을 다시 배운다. **사람이 "이건 아니다"라고 지목한 위치가 재학습에서 정답만큼
중요하다.**
""", encoding="utf-8")

    print(f"보존 대상 행   : {len(rows)}건 (사람 개입 {touched}건 + 고유 이미지 표본)")
    print(f"정답 BBox      : {len(labels)}건  {dict(by_src)}")
    print(f"오탐(negative) : {len(negatives)}건")
    print(f"참조 이미지    : {len(all_imgs)}장 (라벨 부착 {len(imgs)}장)")
    print(f"출력           : {out.resolve()}")


if __name__ == "__main__":
    main()
