# [보존] kubeadm 클러스터 복귀 절차

> **상태 (2026-09-01)**: 운영은 Lightsail 단일 박스(`deploy/lightsail/`)로 이전했다.
> 이 디렉터리의 매니페스트는 **삭제하지 않고 보존**한다 — 평가 기간에 실제로 운영한
> 구성이며(kubeadm 2노드, KEDA, HPA), 복귀 시 그대로 재사용한다.
> 이전 사유: 상시 트래픽 없는 포트폴리오 단계에서 EC2+ELB+IPv4 변동 과금(월 $84 실측)
> → 정액 $24. 상세는 `deploy/lightsail/README.md`.

## 복귀 절차 (요약)

1. EC2 2대 기동 (app 노드에 `workload-type: app` 라벨) + kubeadm 클러스터 구성
2. `kubectl apply` 순서: Secret(wms-secret) → postgres/redis/chroma → 본 디렉터리 매니페스트
   - 적용 순서·프로브 함정은 `54_배포_안전_운영_플레이북` 참조 (probe 경로 선확인 필수)
3. 이미지는 ECR에 SHA 태그로 전부 남아 있다 — `<AWS_ACCOUNT_ID>` 치환 후 적용
4. DB는 Lightsail 박스에서 `pg_dump` → 클러스터 파드로 복원 (방향이 바뀐 것 외에는
   `deploy/lightsail/README.md` 컷오버 2번과 동일)
5. DNS A레코드를 ALB/노드 IP로 되돌리고, GitHub Actions의 k8s 배포 워크플로를 재활성화

## 이 디렉터리에서 유효하지 않은 것

- `celery-worker-keda.yaml`은 2026-08-26에 **제거**됐다(존재하지 않는 namespace·대상 참조).
  KEDA 스케일러는 `worker-scaledobject.yaml`·`ai-worker-scaledobject.yaml`이 정본이다.
  경위: `archive/2026-08-26_k8s_dead_scaledobject/README.md`

## 백업된 배포 워크플로 (2026-09-01)

`deploy-k8s-workflow.yml.bak` — 컷오버 전 `.github/workflows/deploy.yml` 원본 전체.
빌드+ECR push에 더해 **마이그레이션 Job 게이트·kubectl 롤링 업데이트·배포 검증**
(2026-08-10 사고 3건의 방어 로직 포함)을 담고 있다. 복귀 시 이 파일을
`.github/workflows/deploy.yml`로 되돌리면 k8s 배포 파이프라인이 그대로 살아난다.

## 클러스터 운영 워크플로 (2026-09-01 이동)

클러스터 대상 워크플로 13개는 `k8s/workflows/`로 옮겼다 (분류와 사유는 그 폴더
README). **복귀 1단계에서 되돌릴 것은 3개다**: `cluster-rebuild.yml`(재구축) ·
`cluster-teardown.yml`(정리) · `cluster-diagnostics.yml`(진단) —
`.github/workflows/`로 복사하면 Actions에 다시 나타난다. 프론트 레포는
`k8s/workflows/cluster-apply-manifests.yml` 하나를 같은 방식으로 되돌린다.
나머지(핫픽스·리소스 조정·1회성 실측류)는 당시 상황 전용이라 되돌리지 않는다.
