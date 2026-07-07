# 👥 1주차 팀원별 실무 Task 상세 가이드 (R&R)

> **"1주차의 핵심은 비즈니스 로직 완성이 아닙니다. 7명의 팀원이 각자의 자리에서 병렬(Parallel)로 개발을 시작할 수 있도록, 통신 인터페이스(Mock API)와 데이터베이스 뼈대를 최우선으로 뚫어내는 것입니다."**

본 가이드는 6+1 Week Schedule (6주 개발, 1주 QA)의 가장 첫 단추인 1주차 스프린트 상세 액션 플랜입니다. **반드시 `main` 브랜치가 아닌 `feature/[이름]` 브랜치를 파서 작업한 뒤, Tech PM(장문경)에게 코드 리뷰(PR)를 요청하세요.**

---

## 🏗️ 1. [BE-3] WMS Core API & DB 설계 (박민우 Main)
**1주차 목표:** 물류 코어 DB(PostgreSQL) 설계 확정 및 프론트 연동용 FastAPI 더미 API 배포
* **Action Items (상세):**
  1. **DB 스키마 및 복합키(Composite Key) 설계:** 일반적인 WMS와 다르게, 신간과 중고 도서(MINT~SCRAP 등급)를 구분하는 다중 등급 재고 테이블 구조 설계.
  2. **PostgreSQL 연동 및 SQLModel 세팅:** FastAPI와 호환성이 좋은 SQLModel을 활용하여 WMS 핵심 6개 테이블(`orders`, `return_jobs`, `inventory`, `box_standards` 등) ORM 맵핑.
  3. **FastAPI 진입점 구성:** `main.py` 세팅, CORS 미들웨어 적용, Swagger UI(`/docs`) 정상 접속 확인.
  4. **Mock API 배포 (가장 중요):** 프론트엔드가 즉시 API 호출 테스트를 할 수 있도록, 내부 로직 없이 JSON 형태의 껍데기 응답만 뱉어주는 입고/출고/반품 엔드포인트 생성. (예: `POST /api/returns` 호출 시 `{"status": "PENDING", "job_id": 123}` 즉시 반환).

## 📡 2. [BE-2] API 오케스트레이션 및 비동기 큐 (서다은 Main)
**1주차 목표:** 시스템 병목을 막을 비동기 워커 뼈대와 SSE 실시간 스트리밍 엔드포인트 구축
* **Action Items (상세):**
  1. **Redis & Celery 기반 큐 구현:** AI 검수 대기열의 병목을 방지하기 위해 Celery 워커 스크립트 초안 작성 (단, 스레드 병목 방지를 위해 Celery 실행 시 반드시 `gevent` 코루틴 풀 옵션을 적용하도록 환경 세팅).
  2. **SSE(Server-Sent Events) 라우팅 세팅:** 모바일 클라이언트 측에 AI 검수 진행률(20% -> 50% -> 완료)을 밀어내기 위한(Push) 스트리밍 엔드포인트 `/api/stream/returns/{job_id}` 초안 작성.
  3. **LangGraph Supervisor 연동 뼈대:** AI Lead가 개발할 LangGraph Supervisor 파이프라인 함수를 import 하여 비동기로 호출할 수 있는 래퍼(Wrapper) 클래스 설계.

## 🤖 3. [AI Lead] 멀티 에이전트 파이프라인 구축 (홍경표 Main, 박준희 Sub)
**1주차 목표:** LangGraph 환경 세팅 및 Supervisor 기반 단일 노드(Vision Agent) PoC 검증
* **Action Items (상세):**
  0. **[Day-1 블로커] RAG 데이터 포맷(Schema) 설계 리드:** 데이터 파트(소한민)와 협업하여 `docs/ai_knowledge_base` 폴더 내 정책 데이터가 LLM이 파싱하기 가장 최적화된 형태(YAML 등)가 되도록 오늘 중으로 스키마(Schema)를 확정할 것.
  1. **환경 세팅:** `langgraph`, `langchain`, `openai` 패키지 설치, LangSmith(tracing) 환경 설정 및 `.env` 파일 기반 API Key 보안 관리 체계 구축.
  2. **Vision Agent 단일 테스트 및 Fast-track / HITL 기획 (홍경표):** 프론트에서 넘어온 테스트용 파손 도서 이미지를 GPT-4o Vision API에 태우고, JSON 형태로 정상적인 텍스트 결과가 리턴되는지 스크립트 단위 검증. Confidence Score에 따라 Auto-refund로 직행(Fast-track)하거나 수동 승인(HITL)으로 넘기는 라우팅 구조 설계.
  3. **상태 평가지수(UBCI) 룰셋 구조화 (박준희):** 자체 개발 상태 평가지수(UBCI) - 알라딘 참고을 참고한 사내 규정(예: 2cm 이상 찢어짐, 5쪽 이상 메모 등)을 LLM이 쉽게 이해할 수 있는 System Prompt 파라미터 구조로 작성. '상대적 비율(Relative Ratio BBox)'과 '페이지 단위 페널티' 연산 로직 기획.

## 📊 4. [BE-1] Data/MLOps 및 수요 예측 기획 (소한민 Main)
**1주차 목표:** Dynamic RAG (사내 규정 DB) 환경 세팅 및 FDS 데이터 파이프라인 준비
* **Action Items (상세):**
  0. **[Day-1 블로커] AI Knowledge Base 데이터 구축 100% 완료:** (가장 시급) `wms-core-backend\docs\ai_knowledge_base` 폴더 내에 타사 정책 리서치 가이드를 기반으로 교보문고, YES24 등 다중 테넌트(Tenant) 규정 데이터를 오늘 내로 구축 및 텍스트업 완료할 것. (이 데이터가 없으면 AI 파트와 BE 파트의 RAG 로직 개발이 전면 블록됨)
  1. **실제 환경 원시 데이터(Raw Data) 수집:** 스마트폰 카메라를 활용하여 창고 조명 환경을 모사한 도서 샘플 이미지(정상, 오염, 파손, 텍스트 훼손 등) 50~100장 직접 촬영 및 S3 아카이빙. (AI 파트 테스트용)
  2. **RAG 인프라 초안:** AI(Policy Agent)가 최신 반품 및 환불 규정을 즉시 참조할 수 있도록, B2B 멀티 테넌트 정책 격리가 적용된 YAML 데이터를 파싱하여 로컬 벡터 DB(ChromaDB)에 임베딩할 수 있는 스크립트 뼈대 작성.
  3. **수요 예측/FDS 어뷰징 탐지 DB 준비:** 출고(Outbound) 데이터를 바탕으로 추후 Demand Forecasting(수요 예측 및 자동 발주) 모델이 참조할 수 있는 시계열 데이터 스키마 초안 작성, 및 B2B 물류센터 내 악성 반품/블랙컨슈머 연관 FDS(추후 B2B2C 확장 고려) S3 메타데이터 스키마 설계. (모델 학습은 이번 주차에 금지)

## 📱 5. [FE-1] WebRTC/WASM 등 4대 극한 최적화 기술이 적용된 고성능 PWA 클라이언트 (고영빈 Main)
**1주차 목표:** Next.js 모바일 뷰 라우팅 세팅 및 Device API(카메라) 연동 PoC
* **Action Items (상세):**
  0. **[Day-1 블로커] RAG 데이터 및 전체 아키텍처 통합 설계 검수(Tech Lead):** 소한민(데이터)과 홍경표(AI)가 구축하는 `ai_knowledge_base` 스키마 구조가 FE/BE 전체 인터페이스 성능에 병목을 주지 않는지, 3주 조기 하차 전 전체적인 기술 책임자로서 최종 리뷰하고 승인할 것.
  1. **Next.js 모바일 레이아웃 세팅:** `create-next-app` 명령어 기반 프로젝트 생성 및 모바일 해상도(375x812 등)에 최적화된 Viewport 적용. TailwindCSS 초기 설정.
  2. **네이티브 카메라 호출 PoC:** HTML5 `<input type="file" accept="image/*" capture="environment">` 속성을 이용해 스마트폰 후면 카메라를 강제 호출하고, 촬영된 이미지를 로컬 브라우저에 미리보기(Preview)로 띄우는 컴포넌트 개발. (Canvas 리사이징은 추후 진행)
  3. **Mock API 통신 테스트:** BE-3(박민우)가 띄워준 백엔드 더미 주소로 POST 요청을 보내어 응답을 제대로 받아오는지 CORS 이슈 선제 점검.

## 💻 6. [FE-2] 관리자 PC 대시보드 (박준희 Main)
**1주차 목표:** Next.js App Router 기반 대시보드 레이아웃 및 뷰 분리
* **Action Items (상세):**
  1. **정적 라우팅 구성:** `/admin` 디렉토리 하위에 '대시보드 홈', '실시간 검수 내역', '수동 승인 큐', '재고/출고 관리(Dynamic Pricing, Box Optimization 조회용)' 페이지 라우팅 생성.
  2. **Global Layout UI 뼈대:** 좌측 GNB(Global Navigation Bar) Sidebar와 상단 상태창 등 뼈대 화면 퍼블리싱. 화면 전환 시 렌더링 깜빡임 없도록 세팅.
  3. **에이전트 로그 창 UI 설계:** Report Agent가 뱉어낼 JSON 포맷의 복잡한 추론 근거(Agent Logs)를 직관적으로 펼쳐볼 수 있는 Accordion 또는 Modal UI 컴포넌트 제작.

## 👑 7. [Tech PM] 인프라 및 전체 품질 통제 (장문경)
**1주차 목표:** Git/DevOps 인프라 프로비저닝 및 팀 내 블로커(Blocker) 타파
* **Action Items (상세):**
  1. **Git Gatekeeper:** 전사 GitHub Repository 생성 및 브랜치 보호 룰(Branch Protection Rules) 설정. (본인의 리뷰 승인 없이는 main 병합 금지)
  2. **K8s & 인프라 설계 (DevOps/EKS):** AWS EKS 기반 클러스터 초안 프로비저닝 및 추후 워커 파드 증설을 위한 KEDA Controller 선제적 헬름(Helm) 설치. Frontend와 Backend 파드의 3-Tier 분리 구조 설계 (에이전트별 파드 쪼개기 금지 룰 적용).
  3. **통합 관리자(Integration Manager):** 1주 차에 발생할 수 있는 FE-BE 통신 문제(CORS 등) 발생 시 즉시 **트러블슈터(Troubleshooter)**로 투입되어 해결 (직접 백엔드 API를 개발하지는 않고 팀의 장애물을 치워주는 역할).
  4. **코드 검수(Code Reviewer):** 팀원들이 첫 주차에 작성하는 보일러플레이트 구조가 전체 아키텍처 제약을 위반하지 않는지 꼼꼼히 리뷰.
