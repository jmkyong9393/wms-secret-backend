# 쇼케이스 데모 시드 — 주입/삭제 가이드

시연·촬영용 고품질 합성 데이터를 한 번에 넣고(`seed_showcase_demo.py`),
마커 기준으로 흔적 없이 지우는(`purge_showcase_demo.py`) 스크립트 쌍입니다.

## 무엇이 들어가는가

| 데이터 | 수량 | 품질 보장 |
|---|---|---|
| 중고 재고 (IN_STOCK) | 7일 분산 약 18~28건 | 로케이션을 현행 배정 함수(`recommend_optimal_warehouse_zone`)로 결정 — **등급=Zone(B/C/D) · 카테고리=Rack(1~5) · 판형=Shelf(1~4)** 규칙이 라이브 로직과 항상 일치 |
| 검수 기록 (APPROVED) | 재고와 1:1 | **재고와 동일 LPN으로 연결** → 재고 상세 화면에 검수 이미지·LangGraph 진단 기록이 정상 렌더링 |
| 검수 기록 (REJECTED) | 2건 | 반려 건은 재고를 만들지 않음 (실제 흐름과 동일) |
| HITL 대기 | 2건 (≤5 유지) | 촬영 규격(앞/뒤/책등 3컷 이상)을 갖춘 **S3 실재 이미지 세트만** 사용 — 깨진 이미지 0 |
| 완료 주문 (SHIPPED) | 7일 분산 7~14건 | 대시보드 입출고 추이 차트용 |
| PENDING 주문 | 2건 | 출고 시연(피킹→패킹→송장) 시작점 |
| weekly_insights 캐시 | 이번 주 삭제 | 다음 대시보드 조회 때 Insight Agent가 라이브 재집계 |

모든 검수 이미지는 CloudFront(`deao4fid6qoyp.cloudfront.net`)에 실재하는
과거 실촬영 세트를 참조하므로 화면 어디에서도 깨진 이미지가 나오지 않습니다.

## 마커 (삭제의 기준 — 두 스크립트가 공유)

| 대상 | 마커 |
|---|---|
| 재고 / 검수 기록 | LPN 접두사 **`LPN-260731-`** (과거 날짜 네임스페이스라 오늘 날짜로 채번되는 운영 LPN과 절대 충돌하지 않음) |
| 주문 | customer_name 접두사 **`(데모)`** |

마커 밖의 데이터(실촬영 검수, 실주문)는 두 스크립트 모두 절대 건드리지 않습니다.

## 실행 방법

### 로컬 (개발 DB)

```bash
cd wms-secret-backend
python -m scripts.seed.seed_showcase_demo    # 주입
python -m scripts.seed.purge_showcase_demo   # 삭제
```

### 프로덕션 (EC2 kubeadm 클러스터)

레포가 이미지에 포함되어 있으므로 api 파드에서 바로 실행합니다:

```bash
ssh -i <키>.pem ubuntu@<마스터IP> \
  "kubectl exec deploy/wms-api -- sh -c 'cd /app && python scripts/seed/seed_showcase_demo.py'"
```

삭제도 동일하게 `purge_showcase_demo.py`로 바꿔 실행하면 됩니다.
(구버전 이미지라 스크립트가 없으면 `kubectl cp`로 파일을 파드에 복사 후 실행)

DB 대상은 `DATABASE_URL` 환경변수를 따르고, 없으면 로컬 개발 DB
(`postgresql://admin:password@localhost:5432/wms_db`)를 사용합니다.

## 안전 장치

- **멱등**: 주입 스크립트는 실행 시 자기 마커 데이터를 먼저 지우고 다시 넣으므로
  몇 번을 실행해도 중복이 쌓이지 않습니다. `random.seed` 고정이라 매번 같은 데이터가
  생성되어 촬영 리허설 재현성이 보장됩니다.
- **교차검증 내장**: 주입 후 ①등급↔Zone 불일치 ②검수기록 미연결 재고 ③외부 이미지 참조
  3개 지표를 자동 검사해 출력합니다(전부 0이어야 정상). 삭제 후에는 마커 잔재 0을 검증합니다.
- **HITL 오염 방지**: HITL 대기 시드는 실이미지 규격 세트 2건뿐이며, 나머지 합성 데이터는
  전부 확정 상태(APPROVED/REJECTED/SHIPPED)라 결재 대기열에 나타나지 않습니다.

## 주의

- 시연 종료 후 실사용으로 전환할 때는 purge를 한 번 실행해 합성 데이터를 걷어내면 됩니다.
- 마커 문자열을 바꿀 경우 **두 스크립트의 상수(`SEED_LPN_PREFIX`, `SEED_ORDER_PREFIX`)를
  반드시 같이** 바꿔야 합니다 — 한쪽만 바꾸면 지워지지 않는 고아 데이터가 생깁니다.
