# [상세 마일스톤] 6주+1주 B2B WMS AI Platform Sprint Plan

본 문서는 7인 팀이 **총 6주의 핵심 개발과 1주의 고도화 기간** 내에 **K8s 기반 마이크로서비스(MSA)** 인프라 구축과 **Multi-Agent(다중 에이전트)** 워크플로우라는 두 가지 거대한 목표를 동시에 달성하기 위한 주차별 상세 태스크(Task) 분배표입니다. 

---

## 👥 팀 역할 배정 (7인 Roster & Role)

총 7명의 인원을 철저하게 기능 및 인프라 단위로 분산합니다. 가장 난이도가 높은 K8s 인프라를 고급 인력인 Tech PM(장문경)이 전담하고, 백엔드와 프론트엔드의 세부 도메인을 분할하여 병목을 방지합니다.

| 역할명 | 담당자 | 주요 R&R (역할과 책임) |
| :--- | :---: | :--- |
| **Tech PM & DevOps** | **장문경 (PM)** | Git Gatekeeper, DevOps/EKS Infrastructure manager, Kanban master, 통합 관리자(Integration Manager)
  * **[1.4.2.0 업데이트]** HPA(CPU) 및 KEDA(Queue 길이) 하이브리드 오토스케일링 아키텍처 배포 관리, 트러블슈터(Cross-functional Troubleshooter), 코드 검수(Code Reviewer). (NO backend API orchestration) |
| **AI Lead** | **홍경표(Main)** | LangGraph Supervisor (Star Topology) 조립, Fast-track Routing (Mint 등급 Auto-refund) 구현 및 프롬프트 제어 |
| **BE-1 (Data/MLOps)** | **소한민(Main)<br/>고영빈(Sub)** | Dynamic RAG 구축, FDS 및 통계(CronJob), 오토 라벨링 데이터 적재 파이프라인 (YOLO 학습 배제) |
| **BE-2 (Orchestration)**| **서다은(Main)** | Redis & Celery 기반 LangGraph 연동 (Celery/Redis 배제), 실시간 SSE 통신, 대시보드 API 구현 |, gevent 풀 최적화 및 Tenacity 429 에러 방어 구현
| **BE-3 (WMS Core API)** | **박민우(Main)** | 전체 물류 DB 설계 및 WMS CRUD API (No Spring Boot) 전담 개발. FIFO by UBCI, 3D Bin Packing 구현 |, LPN 매트릭스 큐 모드 Fail-over 로직 추가
| **FE-1 (Mobile PWA)** | **고영빈(Main)** | **[3주 압축 플랜]** 작업자용 모바일 뷰, 카메라 단말 연동, Canvas 리사이징 압축 기반 엣지 최적화. 전체 프론트엔드 아키텍처 및 RAG 스키마 총괄 검수. |
| **FE-2 (Dashboard)** | **박준희(Main)** | 관리자용 PC 대시보드 개발. Dynamic Pricing 적용, 판독 데이터 시각화 및 수동 승인 뷰 개발 |

---

## 📅 주차별 상세 스프린트 계획 (6+1 Week 압축 플랜)

인프라(K8s) 셋업과 비즈니스 로직(LangGraph) 개발이 엉키지 않도록, **6주 차까지는 유닛 개발에 집중하여 Code Freeze를 달성**하고, 마지막 7주 차(1 week)는 QA, 버그 픽스, 그리고 데모 준비에만 몰두하는 **6+1 병렬 격리 전략**을 취합니다.

### 🏃 Sprint 1 (W1): 기반 공사 및 WMS DB 설계
* **[1주차] 환경 세팅 및 아키텍처 설계**
  * `[Tech PM, BE-3]`: 주문, 로케이션, 재고 입출고를 포함한 **WMS 코어 관계형 DB 스키마** 완벽 설계 (AWS RDS).
  * `[FE-1, FE-2]`: Figma 기반 모바일 뷰 및 관리자 WMS 뷰 와이어프레임 설계.
  * `[FE-1(Main), BE-1(Sub), AI Lead(Sub)]`: **[Day-1 블로커] AI Knowledge Base 100% 구축 (타사 정책 데이터 취합 후 고영빈이 최종 검수 및 제출) 및 전체 아키텍처 구조 검수.**
  * `[AI Lead, BE-1]`: 창고 환경 모사 도서 반품 데이터(정상/훼손 200장) 수집 및 Dynamic RAG (정책 DB) 스키마 설계 (Relative Ratio BBox, Page-level penalty 적용).
  * `[BE-2]`: 프론트엔드 개발 병목을 막기 위해 가상의 State를 반환하는 더미 API 명세 작성.

---

### 🏃 Sprint 2 (W2~W3): WMS 코어 API 및 에이전트 체인 연동
* **[2주차] 코어 개발 분업**
  * `[BE-3]`: **WMS 코어 API (고객 주문 출고, 재고 차감, 정상 입고/편입)** 개발 전담.
  * `[BE-1]`: FDS(블랙컨슈머 이상거래탐지) 및 주간 통계를 위한 독립된 `report_batch.py` 및 K8s CronJob 인프라 세팅.
  * `[AI Lead]`: LangGraph Supervisor (Star Topology) 기반 에이전트 조립 및 Fast-track Routing (Vision detects Mint -> Auto-refund, Policy/Critic 생략) 파이프라인 완성.
  * `[FE-1]`: 모바일 앱 카메라 연동, HTML5 Canvas 최적화, IndexedDB 캐싱 및 다중 업로드 UI 완료.
* **[3주차] API 오케스트레이션 및 비동기 연동**
  * `[BE-2]`: AI 판독 결과를 `BE-3` WMS API와 내부 연결하여 자동 입/출고 완성. Redis & Celery 강제 적용(Celery/Redis 완전 배제). 클라이언트 SSE 푸시 적용.
  * `[FE-1]`: SSE 실시간 푸시, 예외 처리 등 모바일 개발 조기 완료 (Code Freeze 및 인수인계 완수).
  * `[FE-2]`: 대시보드 레이아웃 구현 및 백엔드 더미 로그 연결.

---

### 🏃 Sprint 3 (W4~W5): 대시보드 시각화 및 Dockerizing 준비
* **[4주차] 에이전트 로그 대시보드 & 예외 처리**
  * `[AI Lead]`: Critic에 명시적 에러 타입(Reason Codes) 적용, MemorySaver(Snapshot)를 활용한 HITL(Human Review) pause/resume 구현 및 UBCI Quality Guarantee Certificate 자동 생성 로직 연동.
  * `[BE-1]`: 출고 데이터 기반 Demand Forecasting & Auto-PO 배치 연동, 오토 라벨링 결과 적재용 DB 스키마 완성.
  * `[FE-2]`: 관리자가 대시보드에서 '에이전트가 어떤 근거로 반려했는지' 직관적으로 확인할 수 있는 시각화 UI 완성.
* **[5주차] 도커라이징 및 CI/CD 파이프라인**
  * `[Tech PM, BE-3]`: FE/BE 레포지토리 `Dockerfile` 작성 및 GitHub Actions 기반 CI/CD 자동 빌드 세팅.
  * `[AI Lead]`: 엣지 케이스(빛 반사 등) 주입 테스트, 환각 방어력 점검 및 LangSmith(tracing) 기반 LLMOps 모니터링 적용.

---

### 🏃 Sprint 4 (W6~W7): K8s 배포, E2E QA 및 데모 리허설
* **[6주차] K8s 클러스터 배포 및 100% Code Freeze (유닛 개발 종료)**
  * `[Tech PM]`: **[인프라 주간]** AWS EKS 상에 Frontend, Admin, Backend Pod 배포 (No Pod splitting by individual agent). HPA 및 Prometheus+Grafana 적용.
  * `[전원 참여]`: **신규 기능 추가 절대 금지 (Code Freeze).** 6주차를 기점으로 모든 유닛 개발을 종료.
  * `[BE-3, AI Lead]`: 1,000장 단위 E2E 부하 테스트(JMeter) 수행. Tech PM이 Grafana 대시보드 스케일 아웃 결과 검증.
* **[7주차] (1 Week QA 및 버그 픽스) 데모 리허설**
  * `[AI Lead, BE-1]`: 최종 추론 정확도 산출. Phase 2 전환을 위한 YOLO 하이브리드 증류(Distillation) 데이터 적재 상태 최종 점검.
  * `[전원 참여]`: 데모 시연용 데이터베이스 리셋. 모니터링 시각화 화면을 포함한 발표 대본 리허설 반복 훈련.
