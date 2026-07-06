# 📅 B2B WMS AI Platform: 7주간 팀원 상세 Task 가이드 (Unit Development)

본 가이드는 7주간의 프로젝트 기간 중 **단 한 명의 유휴 인력도 발생하지 않도록** 설계된 '단위 개발(Unit Development)' 스케줄러입니다. 전체 일정은 6+1 Week Schedule(6 weeks dev, 1 week QA/Demo)로 진행되며 1~6주 차는 순수 개발에 집중(Code Freeze), 7주 차는 통합 QA 및 데모 준비에 전념합니다. Architecture 제약 사항으로 개별 에이전트 단위로 Pod를 분할하는 것은 금지합니다.

---

## 👥 팀원 역할 (R&R) 요약
1. **[Tech PM] 장문경:** Git Gatekeeper, DevOps/EKS Infrastructure manager, Kanban master. 통합 관리자 (Integration Manager)
  * **[1.4.2.0 업데이트]** HPA(CPU) 및 KEDA(Queue 길이) 하이브리드 오토스케일링 아키텍처 배포 관리, 트러블슈터 (Cross-functional Troubleshooter - 병목 구간 직접 투입 및 기술적 문제 해결), 코드 검수 (Code Reviewer - 사고 방지 및 전사 코드 품질 보증). (NO backend API orchestration).
2. **[BE Core] 박민우:** FastAPI 기반 WMS 메인 트랜잭션 API (주문/출고/입고/재고), 다중 등급(Composite Key) 재고 DB 설계. (No Spring Boot)
  * **[1.4.2.0 업데이트]** 하드웨어 라벨러 장애 대비 LPN 매트릭스 큐 모드 (Fail-over) 비상 로직 연동
3. **[BE 연동] 서다은:** 비동기 Worker 데몬(Redis & Celery 강제, Celery/Redis 배제)
  * **[1.4.2.0 업데이트]** Celery gevent 코루틴 풀 기반 데몬 전환 및 Tenacity 지수 백오프(429 에러 방어) 로직 구현 구축, LangGraph Supervisor 파이프라인 호출, 대시보드 API.
4. **[AI Lead] 홍경표:** 4대 에이전트(Vision, Policy, Critic, Report) 파이프라인 총괄 구축. Vision Agent(객체 추출, Fast-track Routing 연동), Critic Agent(Reason Codes 기반 환각 검증) 프롬프트 고도화 및 LangSmith 추적 적용.
5. **[FE PC/Admin] 박준희:** PC 관리자 대시보드 UI/UX 설계 및 React 연동. 에이전트 로그 추적 시각화 및 HITL 수동 개입 화면 프론트엔드 퍼블리싱.
6. **[Data/FDS] 소한민:** RAG 구축(사내 규정 문서 임베딩), K8s CronJob 배치 기반 FDS(이상거래탐지) 어뷰징 리포트 생성.
7. **[FE 모바일] 고영빈:** WebRTC/WASM 등 4대 극한 최적화 기술이 적용된 고성능 PWA 앱, Canvas 리사이징, IndexedDB 오프라인 동기화, SSE 실시간 푸시 수신.

---

## 🗓️ 주차별 상세 태스크 매트릭스

### 📍 [1주 차] 기초 공사 및 기획 확정 (DB & 인프라)
- **장문경:** AWS/Supabase 환경 셋업, GitHub 레포지토리(Rule/Branch Protection) 구축, Notion 칸반 룰 확정.
- **박민우:** WMS 코어 DB 스키마 설계. (신간/중고 분리, MINT~SCRAP 등급별 재고 복합키 설계 반영)
- **서다은:** API 통신 아키텍처 및 Supabase Queue 테이블 설계.
- **홍경표:** GPT-4o Vision API 파라미터 테스트 및 자체 개발 상태 평가지수(UBCI) Policy 룰셋 프롬프트 파라미터 구조화.
- **박준희:** PC 관리자 대시보드 구조 기획 및 컴포넌트 트리 설계.
- **소한민:** 테스트용 도서 파손 이미지 데이터 1,000장 크롤링 및 수집, RAG 환경 세팅.
- **고영빈:** 모바일/PC 대시보드 Figma 와이어프레임 설계 확정.

### 📍 [2주 차] 단위 기능 개발 시작 (코어 로직)
- **장문경:** GitHub Actions 기반 CI/CD 자동 배포 파이프라인 연동.
- **박민우:** FastAPI 기반 WMS 필수 CRUD API(입고, 출고, 반품 접수) 구현 완료.
- **서다은:** FastAPI 서버에서 Supabase Queue 테이블로 `INSERT PENDING` 및 202 응답 반환 로직 구현.
- **홍경표:** BBox 면적/길이 추출 및 '상대적 비율(Relative Ratio BBox)' 변환 프롬프트 스크립트 작성.
- **박준희:** FE PC 관리자 대시보드 (통계 차트, 에이전트 로그 확인 창) 껍데기 퍼블리싱.
- **소한민:** 사내 환불 규정 마크다운 파일 파싱 및 벡터 DB(Pinecone 등) 임베딩 테스트.
- **고영빈:** 모바일 앱 카메라 연동, 풋페달 트리거 캡처, HTML5 Canvas 최적화 로직 구현.

### 📍 [3주 차] AI Agent 체인 연동 및 비동기 처리
- **장문경:** 개발(Dev) 서버와 운영(Prod) 서버 DB 격리, K8s 클러스터 세팅.
- **박민우:** UBCI 점수에 따른 MINT~SCRAP 등급별 WMS 가상 로케이션 편입 및 **주문 발생 시 등급별 선입선출(FIFO) 재고 차감 로직(출고)** 구현.
- **서다은:** Python 데몬 스크립트 작성 (gevent 코루틴 풀 적용 및 Tenacity 지수 백오프 래핑). PostgreSQL Redis 브로커 및 Celery Worker 기반 비동기 큐를 `Celery/Redis 비동기 폴링`로 폴링하여 LangGraph 호출 (Celery/Redis 완전 배제).
- **홍경표:** Supervisor 기반 에이전트 라우팅 적용 및 Policy Agent 동적 가격(Dynamic Pricing) 산정 로직, Critic Agent 교차 검증 / Fast-track Routing 구현.
- **박준희:** PC 관리자 대시보드 상태 관리(Zustand/Redux) 셋업 및 API 연동(조회/수정) 준비.
- **소한민:** FDS(블랙컨슈머 탐지) 룰 엔진 기획 및 **출고 데이터를 활용한 수요 예측(Demand Forecasting) 알고리즘** 기초 설계.
- **고영빈:** 네트워크 단절을 대비한 IndexedDB 기반 오프라인 사진 캐싱 및 자동 재동기화 구현.

### 📍 [4주 차] UBCI 고도화 및 다중 이미지 대응
- **장문경:** 각 단위 브랜치의 `main` 브랜치 Merge PR 리뷰 및 승인 (코드 퀄리티 통제).
- **박민우:** 관리자 수동 승인 API, 상태값 강제 변경 API 및 하드웨어 장애 대비 LPN 매트릭스 큐 화면 데이터 반환 엣지 케이스 추가 개발.
- **서다은:** LangGraph의 State(상태) 객체 정의 및 MemorySaver(Snapshot) 적용, 각 노드 간 데이터 파싱 릴레이 및 LangSmith tracing 연동 테스트.
- **홍경표:** Vision AI가 "가로의 10% 찢김", "면적의 1% 얼룩"을 정확히 리턴하는지 엣지 케이스 점검.
- **박준희:** 에이전트 로그 DB를 바탕으로 PC 화면에 실시간 추론 과정을 보여주는 UI 연동.
- **소한민:** FDS 판별 로직을 Pandas/SQL로 구현하고 통계용 배치(Batch) 스크립트로 분리.
- **고영빈:** '중고 매입 모드' 선택 시, N장의 속지 사진을 Array 형태로 업로드하는 다중 전송 UI 추가.

### 📍 [5주 차] E2E 프론트-백 연동 및 배치 스케줄링
- **장문경:** Locust/JMeter 기반 API 서버 부하 테스트 진행, 병목 지점(Bottleneck) 파악 및 스케일 아웃.
- **박민우:** 다중 주문 시 메타데이터(부피, 무게) 합산 기반 **최적 택배 박스 사이즈 추천 알고리즘(3D Bin Packing)** 로직 구현.
- **서다은:** [모바일 업로드 ➡️ API ➡️ Worker ➡️ Agent ➡️ DB 업데이트] E2E 통신 테스트.
- **홍경표:** Report Agent 사유서/UBCI 텍스트 포맷 생성 백엔드 로직 연동 및 Supervisor 전체 지연 시간(Latency) 30초 이내 달성 최적화.
- **박준희:** Report Agent가 생성한 사유서 데이터 및 **소비자 제공용 디지털 'UBCI 품질 보증서' (QR/Link) 화면 프론트엔드 렌더링** 구현.
- **소한민:** 수요 예측 기반 **안전 재고 도달 전 출판사 자동 발주(Auto-PO) 배치 스크립트** K8s CronJob 등록.
- **고영빈:** 백엔드 완료 이벤트 수신을 위한 SSE(Server-Sent Events) 연동, 실시간 PUSH 알림 UI 구현.

### 📍 [6주 차] 기능 완성 (Code Freeze) 및 예외 처리
> **⚠️ 6주 차 금요일 자정부로 모든 신규 Feature 추가를 중단(Code Freeze)합니다.**
- **장문경:** 모든 기능 브랜치 Merge 완료. EKS HPA 및 KEDA(이벤트 기반 큐 스케일링) 매니페스트 동시 적용 및 스케일 아웃 테스트.
- **박민우:** WMS 코어 로직 단위 테스트(Pytest) 작성 및 커버리지 달성.
- **서다은:** 대시보드 등 조회용 API에 Postgres Celery/Redis 비동기 폴링 캐싱을 적용하여 응답 속도 향상.
- **홍경표:** 조명이 어둡거나 초점이 나간 '블러(Blur)' 사진에 대해 재촬영을 요구하는 예외 방어 로직 추가.
- **박준희:** PC 대시보드 UI/UX 사용성 개선, 디자인 폴리싱 및 QA 피드백 수정.
- **소한민:** FDS 데이터가 대시보드 테이블로 올바르게 넘어가도록 최종 적재 포맷 정리.
- **고영빈:** 풋페달 오류, 네트워크 지연 등 모바일 예외 상황 시 에러 핸들링 및 스낵바 알림 고도화.

### 📍 [7주 차] 통합 QA, 데모 시나리오 준비
- **장문경:** 클라우드 인프라 불필요 리소스 제거(비용 최적화), 데모 데이 총괄 지휘 및 리허설 통제.
- **박민우:** 다수의 사용자가 동시 검수를 진행할 때 발생하는 DB 데드락(Deadlock) 등 극단적 엣지 케이스 테스트.
- **서다은:** 발표 시연 중 발생할 수 있는 오류를 위한 비상용 핫픽스 대기 및 API 모니터링.
- **홍경표:** 시연용 도서 파손 데이터에 대한 모델 환각(Hallucination) 최종 점검.
- **박준희:** 데모 발표에 사용할 '이상적인 결과값'의 로그 데이터 세팅 및 시연 스크립트 타이밍 맞춤.
- **소한민:** 데모용 가상 어뷰저 데이터(FDS 발동용) DB 적재.
- **고영빈:** 시연자가 무대에서 스마트폰 조작 시 버벅거림이 없도록 모바일 애니메이션 및 트랜지션 폴리싱.
