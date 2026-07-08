# 👥 1주차 팀원별 실무 Task 상세 가이드 (R&R)

> **"1주차의 핵심은 비즈니스 로직 완성이 아닙니다. 7명의 팀원이 각자의 자리에서 병렬(Parallel)로 개발을 시작할 수 있도록, 통신 인터페이스(Mock API)와 데이터베이스 뼈대를 최우선으로 뚫어내는 것입니다."**

본 가이드는 6+1 Week Schedule 의 가장 첫 단추인 1주차 스프린트 상세 액션 플랜입니다. **반드시 `main` 브랜치가 아닌 `feature/[이름]` 브랜치를 파서 작업한 뒤, Tech PM(장문경)에게 코드 리뷰(PR)를 요청하세요.**

---

## 🏗️ 1. [BE-1] WMS Core API & DB 설계 (박민우 Main)
**1주차 목표:** 물류 코어 DB(PostgreSQL) 설계 확정 및 코어 API(이미지 업로드 포함) 세팅
* **Action Items (상세):**
  1. **DB 스키마 설계 및 DDL 작성:** 일반적인 WMS와 다르게 신간과 중고 도서를 구분하는 다중 등급 재고 테이블 구조 설계. 실제 PostgreSQL 환경에 테이블을 띄울 수 있는 `init.sql` (DDL) 스크립트 작성 및 커밋.
  2. **FastAPI & SQLModel 연동:** FastAPI 진입점 구성 및 CORS 미들웨어 적용. ORM 맵핑 세팅.
  3. **Mock API 배포:** 프론트엔드가 즉시 API 호출 테스트를 할 수 있도록, 내부 로직 없이 JSON 형태의 껍데기 응답만 뱉어주는 입고/출고/반품 엔드포인트 초안 생성.
  4. **S3 Pre-signed URL API:** 프론트엔드 모바일에서 촬영한 이미지를 AWS S3로 다이렉트 업로드할 수 있도록 보안 토큰(URL)을 발급해 주는 API 엔드포인트 구현.

## 📡 2. [BE-2] API 오케스트레이션 및 비동기 큐 (서다은 Main)
**1주차 목표:** 시스템 병목을 막을 비동기 워커(Redis/Celery) 뼈대 및 모니터링 환경 구축
* **Action Items (상세):**
  1. **Redis & Celery 기반 큐 구현:** AI 검수 대기열 병목 방지를 위한 Celery 브로커/워커 파이프라인 초안 작성.
  2. **SSE(Server-Sent Events) 라우팅 세팅:** 모바일 클라이언트에 AI 검수 진행률(상태 변화)을 실시간으로 밀어내기 위한(Push) 스트리밍 엔드포인트 구축.
  3. **모니터링 및 로깅:** Celery Flower 대시보드 연동을 통해 Task 실패(Retry/OOM)를 트래킹하고, 관리자 대시보드 연동용 작업 소요 시간 측정 측정 뼈대 작성.

## 🤖 3. [AI Lead] 멀티 에이전트 파이프라인 구축 (홍경표 Main)
**1주차 목표:** LangGraph 환경 세팅 및 단일 노드(Vision Agent) PoC 검증
* **Action Items (상세):**
  1. **환경 세팅:** `langgraph`, `langchain`, `openai` 패키지 설치, LangSmith 환경 설정 및 API Key 보안 관리 체계 구축.
  2. **Vision Agent 단일 테스트 (PoC):** 샘플 이미지를 GPT-4o Vision API에 태우고 JSON 형태로 텍스트 결과가 리턴되는지 검증.
  3. **라우팅 설계:** Confidence Score에 따라 Auto-refund로 직행(Fast-track)하거나 수동 승인(HITL)으로 넘기는 뼈대 구조 설계.
  4. **UBCI 파라미터 구조화 (with 고영빈):** 자체 개발 상태 평가지수 룰셋을 프롬프트용 파라미터(JSON)로 정량화하여 Agent System Message 에 주입할 준비.

## 📊 4. [Data] Vector DB 파이프라인 구축 (소한민 Main)
**1주차 목표:** RAG(ChromaDB) 임베딩 환경 세팅 및 실제 테스트 데이터 수집
* **Action Items (상세):**
  1. **Vector DB (ChromaDB) 세팅:** 로컬 또는 도커 환경에 ChromaDB 인스턴스를 띄우는 스크립트 작성.
  2. **임베딩(Embedding) 파이프라인:** 고영빈(FE/Data)이 구축한 정책 데이터(YAML) 문서를 읽어와서 LangChain 스플리터로 쪼개고 임베딩을 수행하는 파이썬 전처리 스크립트 구축.
  3. **AI 테스트용 원시 데이터(Raw Data) 수집:** Vision AI 성능 검증을 위해, 스마트폰 카메라를 활용해 정상, 오염, 텍스트 훼손 등 50~100장가량의 도서 사진을 직접 촬영하여 S3(또는 로컬)에 샘플링.

## 📱 5. [FE-1 & AI Data] 모바일 클라이언트 및 RAG 지식 기반 구축 (고영빈 Main)
**1주차 목표:** 정책 데이터(RAG) 문서화 완료 및 모바일 카메라 제어 PoC
* **Action Items (상세):**
  0. **[Day-1 블로커] AI Knowledge Base 데이터 구축:** (가장 시급) 교보문고, YES24 등 다중 테넌트 정책 리서치를 바탕으로 환불 규정 데이터를 LLM이 파싱하기 쉬운 YAML 포맷으로 문서화하여 소한민(Data)에게 전달.
  1. **UBCI 파라미터 데이터화 서포트:** AI Lead(홍경표)를 도와, 사내 규정을 AI가 연산할 수 있는 수치적 파라미터로 구조화하는 작업 병행.
  2. **Next.js 모바일 레이아웃 세팅:** 모바일 해상도에 최적화된 Viewport 적용 및 TailwindCSS 초기 설정.
  3. **WebRTC 네이티브 카메라 제어 PoC:** 스마트폰 후면 카메라 강제 호출 및 WASM 기반 전처리(압축/흔들림 감지) 뷰 로직 뼈대 설계.

## 💻 6. [FE-2] 관리자 PC 대시보드 및 상태 아키텍처 (박준희 Main)
**1주차 목표:** 대시보드 UI/UX 라우팅 및 Jotai 기반 전역 큐 상태 관리 세팅 (오직 프론트엔드 코어 구축에 100% 집중)
* **Action Items (상세):**
  1. **상태 아키텍처 (Jotai):** 실시간 수동 승인(HITL) 큐 데이터 관리를 위한 Jotai 전역 상태 세팅 및 낙관적 UI(Optimistic UI) 뼈대 설계.
  2. **정적 라우팅 구성:** `/admin` 디렉토리 하위의 검수 내역, 대시보드 홈 등 페이지 라우팅 생성 및 GNB 사이드바 퍼블리싱.
  3. **에이전트 로그 창 UI 설계:** 백엔드 통신 없이 자체 더미(Mock) JSON 데이터를 만들어서, AI 추론 근거를 직관적으로 볼 수 있는 Accordion 또는 Modal 형태의 로그 뷰어 UI 설계 및 구현.

## 👑 7. [Tech PM] 인프라 및 전체 품질 통제 (장문경)
**1주차 목표:** Git/DevOps 인프라 프로비저닝 및 팀 내 블로커 타파
* **Action Items (상세):**
  1. **Git Gatekeeper:** 전사 GitHub 브랜치 보호 룰(Branch Protection Rules) 설정 및 PR 리뷰 통제.
  2. **EKS 인프라 초안 설계:** AWS EKS 클러스터 블루프린트 설계 및 CloudWatch 로깅 인프라 권한 세팅.
  3. **통합 관리자(Integration Manager):** 팀원들 간의 API 통신 병목이나 R&R 충돌 발생 시 즉각 개입하여 해결.
