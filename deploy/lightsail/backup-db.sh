#!/usr/bin/env bash
# Lightsail 박스 DB 일일 백업 - pg_dump(custom 포맷) → S3.
#
# 배경: 컷오버 후 DB가 박스 유일본이다. 박스 유실 = 데이터 전손이므로
# 외부(S3)에 일일 스냅샷을 남긴다. 보존 90일은 S3 수명주기 규칙(db-backups-90d)이 담당한다
# (스크립트가 지우지 않는다 - 박스 권한을 최소로 유지).
#
# 설치(박스에서 1회):
#   crontab -e →  0 18 * * * /home/ubuntu/wms-secret-backend/deploy/lightsail/backup-db.sh >> /home/ubuntu/db-backup.log 2>&1
#   (UTC 18:00 = KST 03:00. 로그는 단순 append - logrotate 불요 수준의 분량)
#
# 복원:
#   aws s3 cp s3://$BUCKET/$PREFIX/<파일> .
#   docker compose -f docker-compose.prod.yml exec -T db pg_restore -U admin -d wms_db --clean --if-exists < <(gunzip -c <파일>)

set -euo pipefail
export PATH="$PATH:/snap/bin"

APP_DIR="${APP_DIR:-/home/ubuntu/wms-secret-backend/deploy/lightsail}"
BUCKET="wms-secret-vision-assets"
PREFIX="db-backups"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
OUT="/tmp/wms_db_${STAMP}.dump.gz"

cd "$APP_DIR"

# 배포 워크플로와 동일한 자격증명 소스(.env) - 인스턴스 역할 폴백 방지
export AWS_ACCESS_KEY_ID="$(grep '^AWS_ACCESS_KEY_ID=' .env | cut -d= -f2)"
export AWS_SECRET_ACCESS_KEY="$(grep '^AWS_SECRET_ACCESS_KEY=' .env | cut -d= -f2)"
export AWS_DEFAULT_REGION="ap-northeast-2"

# custom 포맷(-Fc): pg_restore로 선택 복원 가능. 파이프 중간 실패는 pipefail이 잡는다.
docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U admin -Fc wms_db | gzip > "$OUT"

# 빈 덤프 방어 - 스키마만 있어도 수십 KB는 나온다
SIZE=$(stat -c%s "$OUT")
if [ "$SIZE" -lt 10240 ]; then
  echo "[FAIL] 덤프가 비정상적으로 작습니다: ${SIZE}B - 업로드하지 않음"
  exit 1
fi

aws s3 cp "$OUT" "s3://${BUCKET}/${PREFIX}/$(basename "$OUT")" --only-show-errors
rm -f "$OUT"
echo "[OK] $(date -u +%FT%TZ) ${STAMP} (${SIZE}B) → s3://${BUCKET}/${PREFIX}/"
