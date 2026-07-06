# 📦 B2B WMS AI Platform (Backend Repository)

본 저장소는 물류센터(WMS)로 인바운드되는 중고 및 반품 서적의 외관 상태를 AI가 자동으로 판독하고 검수 리포트를 생성하는 **대규모 비동기 AI 파이프라인 시스템**의 백엔드(FastAPI) 및 워커 코어 레포지토리입니다.

## 🚀 Key Architectural Innovations

본 시스템은 단순한 AI API 호출을 넘어, 대규모 트래픽과 데이터 유실 방지를 위한 엔터프라이즈급 아키텍처를 적용했습니다.

### 1. Zero Data Loss & 비동기 워커 (Redis/Celery)
- 클라이언트의 대기 시간을 최소화하기 위해 **비동기 분산 큐(Redis & Celery)** 구조를 채택했습니다.
- FastAPI 백엔드는 접수만 받고 빠르게 응답(202 Accepted)하며, 백그라운드 AI 워커 데몬이 트래픽 스파이크에도 무너지지 않고 순차적으로 추론을 수행합니다.

### 2. 대용량 이미지 처리 파이프라인 최적화 (S3 Pre-signed URL)
- 모바일 클라이언트가 5~10MB의 고화질 이미지를 백엔드를 거치지 않고 **AWS S3에 다이렉트로 업로드**하여 서버 병목을 원천 차단합니다.
- AI 모듈은 무거운 바이너리 이미지 대신 S3 URL과 경량화된 JSON 데이터만 패싱하여 토큰 비용과 레이턴시를 극단적으로 절약합니다.

### 3. AI 기반 검수 파이프라인 모듈
- **[AI Lead 홍경표]** 주도 하에 AI Vision 모델이 훼손 부위의 정량적 크기를 추출하고, 내부 품질 정책 DB 기반으로 감점 점수를 동적 산출하는 다중 모듈 프로세스를 가동합니다.

### 4. 하드웨어 연동 및 라벨링 처리
- **[FE PC/Admin 박준희]** 주도 하에 블루투스 프린터 통신을 제어하며, 도서 훼손을 방지하는 정전기 필름(포스트잇 재질) LPN 라벨 발급 로직을 지원합니다.

---

## 📂 Repository Structure & Documentations

팀원 및 기여자는 개발 시작 전 반드시 `docs` 폴더 내의 기획 문서들을 숙지하시기 바랍니다.

- 📄 [B2B_WMS_AI_Platform_기획서_ver1.4.2.0.md](docs/B2B_WMS_AI_Platform_기획서_ver1.4.2.0.md): 전체 시스템 구조 및 백엔드 요구사항
- 📊 [B2B_WMS_AI_Platform_워크플로우_ver1.4.2.0.md](docs/B2B_WMS_AI_Platform_워크플로우_ver1.4.2.0.md): 서비스 시퀀스 다이어그램 및 데이터 흐름도
- ⚙️ **app/**: FastAPI 기반 메인 API 서버 및 워커 스켈레톤 소스 코드

---

## 🔒 Copyright & Authorship
- **Project Manager & Chief Architect:** 장문경
- 본 레포지토리의 핵심 아키텍처(S3-JSON Decoupling, Redis/Celery 비동기 제어 구조 등)의 설계 기획 및 IP는 장문경 PM에게 귀속되어 있으며, 본 레포지토리 내의 구조는 추후 논문 및 포트폴리오로 활용될 예정입니다. 참여 팀원 여러분의 구현 기여 내역은 명확히 기록되며 우수 기여 시 공동 기여자(Acknowledgement) 혜택이 주어집니다.
