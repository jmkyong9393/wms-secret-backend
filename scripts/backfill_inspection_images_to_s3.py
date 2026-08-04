# -*- coding: utf-8 -*-
"""
검수 이미지 S3 백필 + image_urls 마이그레이션 스크립트 (1회성 운영 도구)

배경:
    입고 라우터가 오랫동안 base64 이미지를 로컬 디스크에만 저장하고 컨테이너 절대경로
    (/app/app/experiment_data/job-xxx/raw_0.jpg)를 return_jobs.image_urls에 넣어왔다.
    프론트는 이 값을 <img src>에 그대로 꽂으므로 http://localhost:3000/app/app/... 으로
    해석되어 100% 404였고, 상세페이지 검수 이미지가 한 장도 뜨지 않았다.

    또한 이 버킷은 Block Public Access 4종이 전부 ON이라 S3 오브젝트 직링크는 항상 403이며,
    CloudFront 배포만 200을 반환한다. 따라서 DB에는 반드시 CloudFront URL이 들어가야 한다.

동작:
    1. return_jobs를 순회하며 image_urls가 http(s)가 아닌(=로컬 경로) 건을 찾는다.
    2. 해당 경로의 로컬 파일을 S3 inbound/<YYYYMMDD>/<job-dir>/raw_N.jpg 키로 업로드한다.
    3. image_urls를 CloudFront URL 배열로 교체하고, 원본 로컬 경로는
       agent_logs.local_image_paths에 보존한다(워커 YOLO 추론이 로컬 경로를 쓰기 때문).

사용:
    .venv/Scripts/python.exe scripts/backfill_inspection_images_to_s3.py --dry-run
    .venv/Scripts/python.exe scripts/backfill_inspection_images_to_s3.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sqlalchemy import create_engine, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.s3_service import upload_bytes_to_s3  # noqa: E402

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
EXPERIMENT_ROOT = os.path.join(APP_DIR, "experiment_data")


def resolve_local_file(stored_path: str) -> str:
    """
    DB에 적재된 경로를 현재 실행 환경(호스트)에서 실제로 읽을 수 있는 경로로 환원한다.
    컨테이너 경로(/app/app/experiment_data/...)와 호스트 경로가 다르므로
    'experiment_data' 이후의 상대 경로만 취해 로컬 루트에 다시 붙인다.
    """
    normalized = (stored_path or "").replace("\\", "/")
    marker = "experiment_data/"
    if marker not in normalized:
        return ""
    tail = normalized.split(marker, 1)[1]
    return os.path.join(EXPERIMENT_ROOT, *tail.split("/"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="DB를 수정하지 않고 대상만 출력")
    args = parser.parse_args()

    engine = create_engine(settings.DATABASE_URL)

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, image_urls, agent_logs, created_at FROM return_jobs ORDER BY created_at"
        )).fetchall()

    migrated = uploaded = skipped = 0

    for job_id, image_urls, agent_logs, created_at in rows:
        image_urls = image_urls or []
        # 이미 공개 URL로 마이그레이션된 건은 건너뛴다 (재실행 안전 - 멱등).
        if not image_urls or all(str(u).startswith(("http://", "https://")) for u in image_urls):
            skipped += 1
            continue

        date_prefix = created_at.strftime("%Y%m%d") if created_at else "unknown"
        new_urls, local_paths = [], []

        for idx, stored in enumerate(image_urls):
            stored = str(stored)
            if stored.startswith(("http://", "https://")):
                new_urls.append(stored)
                continue

            local_file = resolve_local_file(stored)
            if not local_file or not os.path.exists(local_file):
                print(f"  [MISS] 로컬 파일 없음: {stored}")
                # 원본 파일이 사라진 건은 URL을 지어내지 않고 그대로 둔다.
                new_urls.append(stored)
                continue

            local_paths.append(stored)
            job_dir = os.path.basename(os.path.dirname(local_file))
            s3_key = f"inbound/{date_prefix}/{job_dir}/raw_{idx}.jpg"

            with open(local_file, "rb") as f:
                data = f.read()

            if args.dry_run:
                print(f"  [DRY] would upload {local_file} -> {s3_key}")
                new_urls.append(f"(dry-run)/{s3_key}")
                continue

            cdn_url = upload_bytes_to_s3(data, s3_key)
            if cdn_url:
                uploaded += 1
                new_urls.append(cdn_url)
            else:
                # 업로드 실패 시 최소한 백엔드 StaticFiles로는 열리도록 폴백.
                new_urls.append(f"/experiment_data/{job_dir}/raw_{idx}.jpg")

        print(f"[JOB {job_id}] {len(image_urls)}장 -> {new_urls[0] if new_urls else '-'}")

        if args.dry_run:
            migrated += 1
            continue

        merged_logs = dict(agent_logs or {})
        # 워커의 YOLO 추론은 로컬 경로를 요구하므로 원본 경로를 보존한다.
        merged_logs.setdefault("local_image_paths", local_paths)

        with engine.begin() as conn:
            conn.execute(
                text("UPDATE return_jobs SET image_urls = :urls, agent_logs = :logs WHERE id = :id"),
                {"urls": _jsonb(new_urls), "logs": _jsonb(merged_logs), "id": job_id},
            )
        migrated += 1

    print(f"\n완료: 대상 {migrated}건 / S3 업로드 {uploaded}장 / 건너뜀(이미 URL) {skipped}건")
    return 0


def _jsonb(value):
    """psycopg2가 dict/list를 JSONB 파라미터로 넘길 수 있도록 어댑터로 감싼다."""
    from psycopg2.extras import Json
    return Json(value)


if __name__ == "__main__":
    raise SystemExit(main())
