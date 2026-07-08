# 📋 B2B WMS AI Platform - Sprint 1 Kanban & Work Log

노션(Notion) 서버 점검이나 접속 불가 시, GitHub Repository 내부 또는 로컬에서 마크다운(.md)으로 직접 관리할 수 있는 칸반 보드 및 작업 일지입니다. 

---

## 📊 1주 차 (Week 1) Kanban Board

### 📝 To Do (할 일 대기열)
- `[ ]` **[BE-1.1]** PostgreSQL DB 연동, 코어 스키마(DDL) 설계 및 FastAPI 세팅 (@박민우(Main))
- `[ ]` **[BE-1.2]** S3 Pre-signed URL 발급 API 개발 및 통합 (@박민우(Main))
- `[ ]` **[BE-2.1]** Managed Redis 환경 세팅 및 Celery 브로커/워커 연동 파이프라인 구축 (@서다은(Main))
- `[ ]` **[BE-2.2]** SSE 스트리밍 라우팅 및 Celery Flower / 작업 소요 시간 측정 API 구현 (@서다은(Main))
- `[ ]` **[DATA-1.1]** Vector DB(ChromaDB) 환경 세팅 및 LangChain 임베딩 파이프라인 파이썬 스크립트 작성 (@소한민(Main))
- `[ ]` **[DATA-1.2]** Vision AI 검증용 도서 실제 환경 사진 촬영 및 원시 데이터(Raw Data) 100장 수집 (@소한민(Main))
- `[ ]` **[AI-1.0]** **[Day-1 블로커]** 타사 정책 리서치 기반 AI Knowledge Base (YAML) 데이터 취합 및 구축 완료 (@고영빈(Main))
- `[ ]` **[AI-1.1]** LangGraph 4-Agent 상태 머신 뼈대 구조 세팅 및 PoC 1차 테스트 (@홍경표(Main))
- `[ ]` **[AI-1.2]** 상태 평가지수(UBCI) 룰셋 정량화 및 프롬프트 파라미터 구조화 (@홍경표(Main), @고영빈(Sub))
- `[ ]` **[FE-1.1]** Next.js 모바일 UI 레이아웃 및 WebRTC 기반 카메라 제어/WASM 최적화 뷰 로직 구현 (@고영빈(Main))
- `[ ]` **[FE-2.1]** Jotai 낙관적 UI 전역 큐 상태관리 연동 및 프론트엔드 상태 아키텍처 총괄 구축 (@박준희(Main))
- `[ ]` **[FE-2.2]** 관리자 PC 대시보드 웹 UI 뼈대 작성 및 에이전트 로그 아코디언 컴포넌트 목업 (@박준희(Main))

### 🏃 In Progress (현재 진행 중)
- (Empty)

### 🚨 Blocked (이슈 발생 / 대기 중)
- (Empty)

### ✅ Done (완료 및 PR 병합 완료)
- `[x]` **[PM-1.1]** GitHub Branch Protection 설정 및 CI/CD 자동화 구축 완료 (@장문경)
- `[x]` **[PM-1.2]** EKS(Elastic Kubernetes Service) 노드 그룹 설계 총괄 (@장문경(Main))
- `[x]` **[BE-2.3]** EKS CloudWatch 연동 인프라 환경 및 로깅 권한 세팅 총괄 (@장문경(Main))

---

## 📓 Daily Task 작업 일지 (Work Log)

### 📅 2026-07-07 (1주 차 - 1일 차)
| 담당자 | 진행 티켓 | 오늘 한 일 (Done) | 내일 할 일 (To-Do) | 이슈 / 블로커 (Blocker) | PR 링크 |
|:---:|:---:|---|---|---|:---:|
| **장문경** | PM-1.2 | EKS 클러스터 블루프린트 자동화 설계 완료 | 클라우드 계정 생성 대기 | 없음 | - |
