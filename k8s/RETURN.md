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
