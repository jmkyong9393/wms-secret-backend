# [보존] kubeadm 클러스터 시절 GitHub Actions 워크플로 (2026-09-01 이동)

운영이 Lightsail 단일 박스로 이전(2026-09-01 컷오버)하면서, 소멸한 클러스터를
대상으로 하던 워크플로를 `.github/workflows/`에서 이곳으로 옮겼다.
Actions 목록에는 현행 4개(pr-check·deploy·ecr-push·deploy-lightsail)만 남긴다 —
레포를 처음 보는 사람이 현행과 유물을 구분하지 못하는 문제의 해소가 목적이다.

## 분류

### 복귀 세트 — k8s 복귀 시 `.github/workflows/`로 되돌려 재사용 (RETURN.md 1단계)

| 파일 | 용도 |
|---|---|
| `cluster-rebuild.yml` | teardown 후 빈 계정에서 원클릭 재구축 |
| `cluster-teardown.yml` | 원클릭 정리(의존 순서 내장: ALB→TG→PVC→EC2→EBS→EIP) |
| `cluster-diagnostics.yml` | 읽기 전용 진단 (kubectl get/top/describe) |

(프론트 레포의 `cluster-apply-manifests.yml`도 같은 세트다.)

### 완료된 일회성 작업의 잔재 — 경위 기록용 보존, 재실행 대상 아님

`cluster-move-app-tier` / `cluster-preflight-move` (3a 앱 티어 이동, 완료),
`cluster-hotfix-api` (2026-08 502 플래핑 대응), `cluster-rightsize` /
`cluster-tune-resources` (당시 실측값 하드코딩 — 복귀 시 재실측이 정석),
`cluster-disk-cleanup`, `cluster-measure-model-memory`, `cluster-probe-api`.

### 현행 체계가 대체한 죽은 경로

- `deploy-prod.yml` — 지정 태그 롤백. **현행 롤백은 `deploy-lightsail.yml`의
  SHA 입력이 담당한다.** 남겨두면 롤백 시 죽은 쪽을 누를 위험이 있어 옮겼다.
- `inspection-metrics.yml` — 발표 수치용 운영 DB 집계. 대상 DB(클러스터)가
  소멸해 실행 불능. 집계 SQL은 Lightsail DB 포팅 가치가 있어 보존한다.
