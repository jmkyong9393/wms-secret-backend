# 📦 B2B WMS AI Platform: 중고/반품 서적 전용 4-Agent 자동화 검수 시스템

본 프로젝트는 물류센터(WMS)로 인바운드되는 중고 및 반품 서적의 외관 상태를 AI가 자동으로 판독하고, 규정에 따른 감점 및 최종 품질 보증서를 발급하는 **대규모 비동기 AI 파이프라인 시스템**입니다.

## 🚀 Key Architectural Innovations

본 시스템은 단순한 LLM API 호출을 넘어, 대규모 트래픽과 데이터 유실 방지를 위한 엔터프라이즈급 아키텍처를 적용했습니다.

### 1. Zero Data Loss & 비동기 워커 (Redis 브로커 및 Celery Worker 기반 비동기 큐)
- 클라이언트의 대기 시간을 최소화하기 위해 **비동기 Redis 브로커 및 Celery Worker 기반 비동기 큐(Redis & Celery)** 구조를 채택했습니다.
- 백엔드는 접수만 받고 빠지며(202 Accepted), 백그라운드 AI 워커 데몬이 트래픽 스파이크에도 무너지지 않고 순차적으로 E2E 추론을 수행합니다.

### 2. 대용량 이미지 처리 파이프라인 최적화 (S3 Pre-signed URL)
- 모바일 클라이언트가 5~10MB의 고화질 이미지를 백엔드를 거치지 않고 **AWS S3에 다이렉트로 업로드**하여 서버 병목을 원천 차단합니다.
- AI 에이전트 간에는 무거운 바이너리 이미지 대신, 추출된 **BBox 좌표와 상대 비율(%) 텍스트(JSON)** 데이터만 패싱하여 토큰 비용과 레이턴시를 극단적으로 절약합니다.

### 3. LangGraph 기반 4-Agent 상태 머신 (Star Topology)
LangChain의 `LangGraph`와 `MemorySaver`를 활용하여 4개의 특화된 에이전트가 협업 및 상호 견제합니다.
- **👁️ Vision Agent:** 촬영 거리의 한계를 극복하기 위해 절대 크기(cm)가 아닌 **'전체 면적 대비 결함의 상대 비율(Relative Ratio)'**을 추출합니다 (GPT-4o).
- **⚖️ Policy Agent:** Vision이 넘겨준 데이터를 바탕으로 **Vector DB(RAG)**를 조회하여, 사내 규정(UBCI)에 맞는 정확한 감점 스코어를 수학적으로 산출합니다.
- **🛡️ Critic Agent:** Policy의 논리를 교차 검증하며, 지속적인 환각(Hallucination) 발생 시 즉시 프로세스를 멈추고 **관리자 수동 개입(HITL)**을 요청합니다.
- **📝 Report Agent:** 단순 텍스트 조합이 아닌, 결함의 심각도와 고객 상황에 맞춰 위로 또는 단호한 매입 불가 안내 등 **동적 CS 페르소나 텍스트**를 렌더링합니다.

---

## 📂 Project Structure & Documentations

팀원 및 기여자는 개발 시작 전 반드시 아래의 핵심 기획 문서들을 숙지하시기 바랍니다.

- 📄 [B2B_WMS_AI_Platform_기획서_ver1.4.2.0.md](./B2B_WMS_AI_Platform_기획서_ver1.4.2.0.md): 상세 시스템 아키텍처 및 4-Agent 세부 동작 원리
- 📊 [B2B_WMS_AI_Platform_워크플로우_ver1.4.2.0.md](./B2B_WMS_AI_Platform_워크플로우_ver1.4.2.0.md): 에이전트 간 데이터 흐름 및 다이어그램
- ⚙️ **wms-core-backend/**: FastAPI 기반 메인 API 서버 및 LangGraph 워커 스켈레톤

---

## 🔒 Copyright & Authorship
- **Original Architecture & AI Pipeline Design by:** [장문경 (Project Manager & Architect)]
- 본 레포지토리의 핵심 아키텍처(S3-JSON Decoupling, UBCI 수식, 4-Agent LangGraph 제어 구조)는 향후 학술 논문 및 포트폴리오로 활용될 예정입니다. 팀원 여러분의 기여(구현)는 논문/포트폴리오에 감사(Acknowledgement) 또는 공동 기여자로 명시될 수 있습니다.
