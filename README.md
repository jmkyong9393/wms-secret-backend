# Nexus — AI Smart WMS Platform (Backend)

입고부터 출고까지를 단일 파이프라인으로 처리하는 **AI 기반 B2B 물류(WMS) 자동화 플랫폼**의
백엔드·AI 워커 레포지토리입니다. 시연 도메인은 도서이며, 신품 무검수 Fast-Track과
중고·반품 AI 검수가 같은 파이프라인의 두 갈래로 동작합니다.

- **Live**: https://nexus-wms.p-e.kr (AWS EC2 kubeadm 2노드 클러스터에서 운영 중)
- **Stack**: FastAPI · Celery/Redis · PostgreSQL · Alembic · LangGraph · ChromaDB · XGBoost

## 아키텍처 개요

```
Worker PWA ──(presigned POST)──▶ S3 ─┐
     │ 202 접수                       │
     ▼                                ▼
 FastAPI ──▶ Redis Queue ──▶ Celery AI Worker ──▶ LangGraph 판정 체인
     │            │                                Detector(WBF 3-YOLO, LLM 미사용)
     │            └─ KEDA 오토스케일                → Vision(GPT-4o 판독)
     ▼                                             → Policy(UBCI 결정론 산식)
 SSE(1회용 티켓 인증) ◀── 판정 결과 ──            → Critic(2단 교차검증)
                                                   → Supervisor(게이트) → Report(보증서)
```

**설계 원칙 — AI는 제안하고, 산식이 결정한다.** 결함 탐지·판독은 AI가 하지만
등급·가격 등 금액을 정하는 값은 결정론 산식이 계산합니다(해당 노드 LLM 토큰 0,
동일 입력 270회 반복 검증 오차 0).

## 도메인 구성 (`app/domains/`)

| 도메인 | 역할 |
| --- | --- |
| `inbound` | 신품 Fast-Track / 중고 LPN 발급 · 3컷 촬영 접수 · AI 검수 큐 등록 |
| `admin` | HITL 결재(결함 박스 보정 → 재학습 정답지 축적) · 재검수 |
| `inventory` | Zone–Rack–Shelf 로케이션 자동 배정 · 재고 원장 |
| `orders` / `outbound` | 주문 → 스냅샷 기반 피킹 지시서 → 3D 패킹(EP-BFD 자체 구현) → 송장 |
| `po` | 이벤트 기반 저재고 감지 → Restock 발주 제안(관리자 승인 후 집행) |
| `fds` | 이상거래 룰엔진 (블라인드 승인 · 등급 오버라이드 · 야간 대량 고액) |
| `dashboard` | 관제 지표 · Weekly Insights(수치는 SQL, 서사만 LLM) |
| `board` / `uploads` | 게시판 + 첨부 파일 보안 업로드(격리 검사 후 승격) |
| `labels` | ZPL 라벨 프린터 브리지 (LAN 내부망 하드웨어 제어) |
| `auth` / `users` / `settings` / `notifications` / `returns` / `research` | 인증 · 계정 · 정책 · 알림 · 반품 · 실험 계측 |

핵심 공용 모듈은 `app/core/`에 있습니다 — UBCI 감점 매트릭스(`ubci_matrix.py`),
RAG 정책 근거(`rag_service.py`), 파일 보안 검사(`file_security.py`),
CloudFront 서명(`cloudfront_signing.py`), SSE 티켓(`sse_ticket_service.py`) 등.

## 신뢰성 · 보안 (실측 기준)

| 항목 | 내용 |
| --- | --- |
| 작업 유실 방지 | Celery `acks_late` + DLQ. 카오스 테스트(워커 강제 종료)에서 4/4건 전량 복구 — 유실 0 |
| 판정 재현성 | 결정론 엔진 동일 입력 270회 반복 오차 0 |
| 동시성 | Redis Lock 중복 처리 차단 · 1,000건 동시 입고 부하 실측 |
| 인증 | HttpOnly Secure 쿠키 JWT. SSE도 같은 쿠키로 인가한다(EventSource는 커스텀 헤더를 못 붙인다). 쿠키를 쓸 수 없는 호출자를 위한 1회용 티켓 경로가 있으나 현재 프론트는 쓰지 않는다 |
| 인가 | 역할별 RBAC + 전 엔드포인트 무인증 런타임 프로빙 — 노출 경로 17건 발견 → 전량 차단(잔여 0) |
| 실시간 스트림 | `no-transform`·`X-Accel-Buffering: no`로 중간 프록시의 압축·버퍼링을 차단하고, 이벤트가 없는 구간에만 25초 하트비트를 보낸다. 압축에 갇혀 이벤트가 브라우저에 **전혀 도달하지 못하던** 결함을 2026-08-26 실측으로 발견해 막았다 |
| 객체 열람 | S3 Block Public Access + CloudFront 서명 URL만 허용 |
| 첨부 업로드 | presign → 격리 구역(`quarantine/`) 업로드 → 서버가 실제 바이트 검사 → 정상 구역 승격. 매직바이트·중첩 아카이브·PDF 액티브 콘텐츠·후미 페이로드 등 10계층 검사, 소유권은 키 접두사로 결속 |
| 개인정보 | KISA 권고 전수 대조 · 감사 추적(판정 100% 기록 보존) |

상세 명세와 검증 로그는 `개인개발가이드/` 문서 체계(93번 첨부 보안, 92번 트러블슈팅
아카이브 등)와 `tests/`에 있습니다.

## 실행

```bash
uv sync                                  # 의존성 (Python 3.11, uv 기준)
docker compose up -d                     # Postgres · Redis 로컬 기동
alembic upgrade head                     # 스키마 마이그레이션 (Migration as Code)
uvicorn app.main:app --reload            # API 서버
celery -A app.core.celery_app worker -l info   # AI 워커
```

테스트는 `pytest`로 실행합니다. 단위 테스트는 `tests/unit/`에 있으며 첨부 보안
엣지케이스(위장 확장자·중첩 압축·오탐 검증 등)를 포함합니다.

## 배포

**현행 (2026-09-01~)**: Lightsail 단일 박스 + docker compose (`deploy/lightsail/`).
Caddy가 TLS(Let's Encrypt)를 맡고, 배포는 GitHub Actions "Deploy to Lightsail"
(빌드+ECR push는 main push 자동, 반영은 수동 트리거 — 롤백은 직전 SHA 입력).
평가 종료 후 비용 최적화 이전이며, 검증 절차는 `deploy/lightsail/README.md`·smoke-test.sh.

**평가 기간 운영(보존)**: `k8s/` 매니페스트 — kubeadm 2노드, ALB/ACM HTTPS 단일 개방,
KEDA(Redis 큐 길이 기반 워커 오토스케일), GitHub Actions OIDC 무중단 롤링 배포,
CloudWatch + Sentry 관측. 복귀 절차는 `k8s/RETURN.md`.

## 문서

- `docs/policies/` — 검수·감점 정책 원문 (RAG 근거 소스)
- `docs/ai_knowledge_base/`, `docs/vision_model_docs/` — AI 모델 지식베이스
- `CLAUDE.md`, `guide.md` — 개발 규칙 및 온보딩 가이드

## Copyright & Authorship

- **Project Manager & Chief Architect:** 장문경
- 핵심 아키텍처(비동기 판정 파이프라인, 결정론 판정·가격 엔진, S3-JSON 디커플링 등)의
  설계와 IP는 장문경 PM에게 귀속되며, 논문 및 포트폴리오로 활용될 예정입니다.
  참여 기여 내역은 커밋 이력으로 기록됩니다.
