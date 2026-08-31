# Lightsail 단일 박스 배포

kubeadm 2노드(EC2)에서 Lightsail 정액제 인스턴스로의 이전 구성이다.
**왜 이전했나**: 평가 종료 후 상시 트래픽이 없는 포트폴리오 단계에서 EC2+ELB+IPv4
변동 과금(월 $84 실측)을 정액 $24로 바꾸기 위해. k8s 구성은 `k8s/`에 보존되어 있고
복귀 절차는 `k8s/RETURN.md` 참조.

## 사양 근거 (실측 2026-09-01)

| 항목 | 실측 |
|---|---|
| 워커 피크 (3-YOLO 로드+추론) | **870MB** |
| 전 스택 합산 피크 | **약 1.9GB** |
| 플랜 선택 | **2GB($12)로 시작 가능, 4GB($24) 권장** — 아래 "2GB 운영 조건" 참조 |
| 크레딧 | Free Tier·Explore 3건 모두 Lightsail 적용 확인 (잔여 $55.43 → 약 2달 실지출 0) |

## 2GB($12) 플랜으로 운영할 때의 조건

스택 합산 피크 실측 ~1.9GB + OS ~0.3GB > 2GB이므로 **다음 세 가지가 전제**다.

1. **스왑 4GB 필수** (설치 2번 단계). 없으면 추론 중 OOM Killer가 무작위 컨테이너를 죽인다.
2. **`.env`에 `WORKER_CONCURRENCY=2`** (기본값). 동시 검수가 겹치면 스왑질로 느려질 뿐
   죽지는 않는다 - 포트폴리오 시연(단건)에는 영향 없다.
3. **배포는 순차 교체**: `up -d`를 한 번에 치면 구·신 컨테이너가 공존해 피크가 겹친다.
   2GB에서는 `docker compose ... up -d --no-deps api && ... worker && ... frontend`처럼
   서비스 단위로 나눠 친다 (각 단계 healthy 확인 후 다음).

느리다고 느껴지면 **스냅샷 → 4GB 플랜으로 복원**(다운타임 수 분)으로 언제든 승격한다.
면접 시연 전날만 4GB로 올렸다가 되돌리는 운용도 가능하다.

## 최초 설치 (Lightsail 인스턴스에서 1회)

```bash
# 1. Docker + compose 플러그인 (Ubuntu 기준)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # 재로그인

# 2. 스왑 4GB (추론 스파이크 보험)
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 3. 이 디렉터리 가져오기 + 환경 구성
git clone <backend-repo> && cd wms-secret-backend/deploy/lightsail
cp .env.example .env && vi .env    # 실제 값 채우기 (커밋 금지)

# 4. ECR 로그인 (ECR 읽기 전용 IAM 사용자 권장 — AmazonEC2ContainerRegistryReadOnly)
aws ecr get-login-password --region ap-northeast-2 \
  | docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.ap-northeast-2.amazonaws.com

# 5. 기동
docker compose -f docker-compose.prod.yml up -d
```

## 컷오버 체크리스트 (순서 중요)

1. **[구 클러스터] 최종 DB 덤프** — DB는 RDS가 아니라 클러스터 파드다. 이걸 빼먹으면
   평가 이후의 데이터(발주 결재·검수 이력)가 사라진다.
   ```bash
   kubectl exec <postgres-pod> -- pg_dump -U admin -Fc wms_db > final_20260901.dump
   ```
2. **[신규 박스] 복원** — 컨테이너 postgres는 15-alpine. 클러스터 쪽 major 버전이 더 높으면
   compose의 이미지 태그를 맞춰 올릴 것 (pg_dump는 상위→하위 복원을 보장하지 않는다).
   ```bash
   docker compose -f docker-compose.prod.yml cp final_20260901.dump db:/tmp/
   docker compose -f docker-compose.prod.yml exec db pg_restore -U admin -d wms_db --clean /tmp/final_20260901.dump
   ```
3. **[신규 박스] 전 서비스 healthy 확인** 후 스모크: 로그인 → 재고 조회 → SSE 연결
   (`curl -N https://<박스IP주소로 임시 hosts 지정>/api/v1/notifications/stream`)
4. **DNS 전환** — `nexus-wms.p-e.kr` A레코드를 Lightsail 고정 IP로. TTL 지난 뒤 Caddy가
   Let's Encrypt 인증서를 자동 발급한다 (첫 접속이 몇 초 느린 것은 정상).
5. **운영 확인** — `/api/v1/health`가 `pricing_model: xgboost`를 반환하는지까지 볼 것.
6. **구 인프라 정리** — EC2 2대 종료, **ELB 삭제, EIP 해제**(끄고도 계속 과금되는 항목),
   고아 EBS 볼륨 확인. S3·CloudFront·ECR은 그대로 둔다.

## 일상 운영

| 작업 | 명령 |
|---|---|
| 새 버전 배포 | `.env`의 `BACKEND_TAG`/`FRONTEND_TAG`를 새 git SHA로 → `docker compose -f docker-compose.prod.yml pull && up -d` |
| **롤백** (rollout undo 대응) | 태그를 이전 SHA로 되돌리고 `up -d` — ECR에 SHA별 이미지가 전부 남아 있다 |
| 무중단 교체 (롤링 대응) | `docker-rollout` 플러그인: 새 컨테이너 healthy 후 구 컨테이너 제거 |
| 워커 부하 조절 (KEDA 대응) | gevent `--concurrency` 조정. 정액 박스라 scale-to-zero는 무의미(비용 목적이므로) |
| DB 백업 | `docker compose exec db pg_dump -U admin -Fc wms_db > backup_$(date +%Y%m%d).dump` — 주기 백업은 cron + S3 업로드 권장 |
| 로그 | `docker compose logs -f api worker` |

## 주의 (실측에서 나온 것)

- **Caddy에 encode(압축)를 켜지 말 것** — SSE가 압축 버퍼에 갇혀 이벤트가 전혀 도달하지
  않는 결함을 Next rewrite 프록시에서 실제로 겪었다 (92번 아카이브 2026-08-26).
- 프론트→백엔드는 `BACKEND_ORIGIN=http://api:8000` rewrite로 same-origin을 유지한다.
  프론트만 Vercel 등으로 분리하지 말 것 — SSE·HttpOnly 쿠키 전제가 깨진다.
- celery beat 스케줄 파일은 `/tmp`에 있어야 한다 (non-root라 /app 루트 쓰기 불가).
