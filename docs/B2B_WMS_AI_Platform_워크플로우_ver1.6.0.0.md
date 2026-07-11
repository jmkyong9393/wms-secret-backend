# [Tech PM Planner] 멀티 에이전트 기반 B2B 도서 물류(입출고·반품) 자동화 및 AI 재고 관리(WMS) 플랫폼 워크플로우 (ver1.4.2.0)

## 1. 과제 정의 및 핵심 목표 (Project Definition & Objective)
본 프로젝트는 기존 수작업에 의존하던 도서 이커머스 반품 검수 프로세스를 **VLM(Vision-Language Model)과 LangGraph 기반 멀티 에이전트(Multi-Agent)**를 활용해 자동화하는 B2B SaaS 플랫폼을 구축하는 것입니다. 
개발 기간(6+1주 압축) 및 팀 구성을 고려하여, 핵심 시각 판독은 강력한 GPT-4o에 위임하고 텍스트 기반 논리 전개는 GPT-4o-mini에 위임하여 속도와 정확성을 동시에 잡는 파이프라인을 구축합니다. Tech PM은 Git Gatekeeper, DevOps 매니저, Kanban 마스터 역할을 전담하여 인프라 안정성을 극대화합니다.

---

## 2. 시스템 아키텍처 및 워크플로우 (System Architecture & Workflow)

### 2.2 WMS 통합 입고(Inbound) 라우팅 흐름도

새 책은 다이렉트로 입고 처리되며, 중고/반품 서적은 4-Agent AI 검수대로 향하는 WMS 입고 첫 관문 라우팅 구조입니다.

```mermaid
sequenceDiagram
    participant Worker as 현장 작업자
    participant Printer as 블루투스 프린터
    participant FE as Frontend (Next.js/Jotai)
    participant BE as FastAPI
    participant Redis as Redis Broker
    participant Celery as Celery Worker (gevent Pool)
    participant Graph as LangGraph (4-Agents)
    participant DB as PostgreSQL

    Worker->>FE: 1. 스마트폰 카메라 촬영 및 바코드 스캔 (WebRTC)
    FE->>FE: 2. Canvas 압축 & 흔들림 검출 (Edge AI 대체)
    FE->>FE: 3. Jotai 큐 PENDING 추가 (낙관적 UI 전환)
    
    alt 프린터 정상 연결 (Primary Mode)
        FE->>Printer: 4-A. 블루투스 프린터 LPN 인쇄 지시 (정전기 필름)
        Worker->>Worker: 4-B. 훼손 방지 정전기 필름 라벨 부착 후 촬영 진행 (Non-blocking)
    else 하드웨어 장애/용지 소진 (Fail-over Mode)
        FE-->>Worker: 4-C. 화면에 가상 큐 번호(매트릭스 바구니) 할당 알림
        Worker->>Worker: 4-D. 해당 번호 바구니에 도서 적재 후 다음 촬영 진행
    end
    
    FE->>BE: 5. Signed Cookie 보안 인증
    BE->>DB: 6. 상태 PENDING 저장
    BE->>Redis: 7. Task 큐 적재
    BE-->>FE: 8. 202 Accepted 응답
    
    loop 3초 주기 비동기 Redis Pub/Sub SSE (Redis Pub/Sub SSE)
        FE->>BE: 9. GET /api/v1/inspections/{id}
    end
    
    Redis->>Celery: 10. Worker가 Task 수신
    Celery->>Graph: 11. Multi-Agent 파이프라인 실행
    Graph->>Graph: 12. Vision -> Policy -> Critic -> Report
    Graph->>DB: 13. 상태 COMPLETED 및 결과 업데이트
    
    BE-->>FE: 14. Redis Pub/Sub SSE 완료 응답 (COMPLETED)
    FE->>FE: 15. Jotai 큐 체크마크(✓) 및 최종 판정 업데이트
```

### 2.3 반품/중고 서적 전용 4-Agent AI 검수 워크플로우 (System Architecture & Workflow)

전체 시스템은 **Client(모바일/웹) -> API/백엔드 -> AI/Storage**의 3계층으로 분리되어 동작합니다. VLM 추론 시 발생하는 레이턴시(Latency) 병목을 막기 위해 **비동기 Redis Pub/Sub SSE(Redis Pub/Sub SSE) 방식**과 LangGraph를 결합합니다.

### 2.3.1 통합 시스템 아키텍처 도해도 (Mermaid)

```mermaid
graph TD
    subgraph "Client Layer (Vercel / Next.js)"
        A[작업자 고성능 PWA 신간 반품 및 중고 매입 모드] -->|1. 도서 가이드 촬영  - 중고 모드는 속지 N장 추가| B(HTML5 Canvas 리사이징)
        F[관리자 대시보드 Web] -->|7. 에이전트 로그 확인 및 승인| G((상태 모니터링))
    end

    subgraph "API & Orchestration (AWS EKS / FastAPI & Worker)"
        C[AWS CloudFront Edge Upload] -->|3. Redis 브로커 및 Celery Worker 기반 비동기 큐 INSERT 및 202 반환| E[(AWS RDS PostgreSQL)]
        C -->|클라이언트 SSE 실시간 푸시| A
        E -->|4. Celery/Redis 비동기 Redis Pub/Sub SSE| W[Worker Daemon]
        W -->|5. Multi-Agent 위임| D[LangGraph Workflow]
        D -->|6. 에이전트 로그 및 결과 DB 저장| E
    end

    subgraph "AI & Storage Layer"
        B -->|2. 최적화 이미지 다이렉트 업로드| S3[(AWS S3 Storage)]
        D <-->|7. AI Image Analysis Module| M1[GPT-4o Vision]
        D <-->|8. AI Evaluation & Reporting Module| M2[GPT-4o-mini]
    end

    subgraph "Analytics & LLMOps Layer"
        E -.->|자정 배치 실행| Batch[K8s CronJob report_batch.py]
        Batch -->|어뷰징 탐지 및 리포트 적재| F
        D -.->|Tracing & Snapshot| LangSmith[(LangSmith / MemorySaver)]
    end

    S3 -.-> D
```

### 2.2 검수 자동화 시퀀스 다이어그램 (Sequence Diagram)

```mermaid
sequenceDiagram
    participant Customer as 고객주문 (가상)
    participant Worker as 현장작업자
    participant API as WMS/FastAPI
    participant Graph as LangGraph (4-Agents)
    participant DB as WMS DB (PostgreSQL)

    %% [1] 스마트 출고 로직 (Smart Outbound)
    Customer->>API: 1. 가상 주문 인입 (단건/다건 주문)
    API->>DB: 2. [FIFO] 입고일 가장 오래된 상태별 재고 선입선출 조회
    API->>DB: 3. [동적 가격] UBCI 점수 비례 할인율 적용
    API->>API: 4. [박스 최적화] 메타데이터 합산 후 최적 택배박스(3D Bin) 계산
    API->>DB: 5. 재고 차감 (수요 예측 알고리즘 발동 체크)
    API-->>Worker: 6. 박스 사이즈 및 픽킹 위치 지시
    API-->>Customer: 7. 출고 완료 및 [UBCI 디지털 품질 보증서 발급]
    %% [2] 반품 및 매입 로직 (Returns / Used Purchases)
    Customer->>Worker: 4. 반품 도서 인입 / 중고 매입 접수
    Worker->>Worker: 모드 선택 (신간 1장 vs 중고 다중 N장 촬영)
    Worker->>API: 5. 비전 검수 요청 (S3 Upload)
    API->>DB: 6. 상태 '검수중(Processing)'
    API-->>Worker: 7. 202 Accepted 및 SSE 연결
    
    %% [3] Redis 브로커 및 Celery Worker (gevent Pool) 기반 비동기 큐 & Worker (Decoupling)
    API->>DB: 8. INSERT PENDING 상태 저장
    API-->>Worker: 9. 202 Accepted (API 응답 종료)
    DB->>WorkerDaemon: 10. Celery/Redis 비동기 큐 Redis Pub/Sub SSE
    %% [4] LangGraph Supervisor Star Topology 처리
    DB->>Graph(Supervisor): 11. Celery/Redis 비동기 Redis Pub/Sub SSE 워커 할당
    Graph(Supervisor)->>Graph(Vision): 12. [Vision Agent] 외관/내지 불량 탐지 지시
    Graph(Vision)-->>Graph(Supervisor): 13. 불량 유무 및 BBox 결과 보고
    
    alt 결함 없음 (MINT)
        Graph(Supervisor)->>API: 14-A. Policy/Critic 생략 (Fast-track MINT) -> Auto-Refund 승인
    else 결함 발견 시
        Graph(Supervisor)->>Graph(Policy): 14-B. [Policy Agent] 불량 내역 기반 UBCI 페널티 계산 지시
        Graph(Policy)-->>Graph(Supervisor): 14-C. 감점액 결과 보고
        Graph(Supervisor)->>Graph(Critic): 14-D. [Critic Agent] Reason Codes 기반 환각 방지 교차 검증
        Graph(Critic)-->>Graph(Supervisor): 14-E. 검증 완료 보고
    end
    
    Graph(Supervisor)->>Graph(Supervisor): 15. 결과 신뢰도 종합 평가 및 라우팅
    alt 훼손 최종 판정
        Graph(Supervisor)->>API: 16-A. [Report Agent] UBCI 사유서 및 보증서 발급
    else 신뢰도 낮음 (예외 케이스)
        Graph(Supervisor)->>Worker: 16-B. [MemorySaver HITL] 관리자 수동 검수 대기 (Pause)
        Worker-->>Graph(Supervisor): 16-C. 수동 보정 완료 (Resume)
    end
    
    %% [5] 입고/재고 연동 로직 (Inbound)
    alt 판정: 정상품 또는 매입 가능 중고 (UBCI B급 이상)
        Graph->>API: 15. 정상/매입 연동 호출
        API->>DB: 16. [WMS] 등급별(MINT~GOOD) 가상 로케이션 재고 +1 편입
    else 판정: 훼손품 또는 매입 불가 중고 (UBCI C급/F급)
        Graph->>Graph: 15. [Report Agent] 거절/매입 불가 사유서 작성
        Graph->>API: 16. 훼손품 WMS 연동 호출
        API->>DB: 17. [WMS] 악성 재고 방지(편입 거부) 및 자동 발주 추천
    end
    
    Graph->>DB: 18. 에이전트 로그 및 최종 상태 업데이트
    Graph->>API: 19. Task 완료 이벤트
    API->>Worker: 20. SSE 실시간 최종 결과 및 재고 변동 푸시
```

---

### 2.4 스마트 출고(Outbound) 5대 파이프라인 흐름도

물류의 완벽한 순환(Closed-loop)을 위해, 반품/입고뿐만 아니라 **출고 과정에도 AI와 데이터 분석이 개입**하여 악성 재고를 방지하고 물류비를 최적화합니다.

```mermaid
graph TD
    Order( - 고객 가상 주문 인입) --> FIFO
    
    subgraph "스마트 출고(Outbound) 5대 AI 파이프라인"
        direction TB
        FIFO["1. 상태 기반 선입선출 입고일이 가장 오래된 MINT/GOOD 등급 할당"]
        Price["2. 동적 가격 책정 할당된 도서의 UBCI 점수에 비례한 자동 할인 적용"]
        Forecast["3. 수요 예측 및 자동 발주 출고 트렌드 분석 후 안전재고 미달 시 출판사 PO 전송"]
        Pack["4. 3D Bin Packing 박스 최적화 다건 주문 시 도서 메타데이터 기반 최적 택배박스 추천"]
        
        FIFO --> Price
        Price --> Forecast
        Forecast --> Pack
    end
    
    Pack --> Cert( - 5. UBCI 디지털 품질 보증서 발급 소비자용 URL 링크 제공)
    
    style FIFO fill:#e1f5fe,stroke:#0288d1,stroke-width:2px
    style Price fill:#e1f5fe,stroke:#0288d1,stroke-width:2px
    style Forecast fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    style Pack fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
    style Cert fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
```

---

## 3. 핵심 데이터베이스 스키마 (Core DB Schema)

관계형 데이터베이스(PostgreSQL)를 활용하며, 다중 에이전트가 뱉어내는 다양한 중간 상태(State)와 사유서를 유연하게 저장하기 위해 `JSONB` 타입을 적극 활용합니다.

| Table Name | Column Name | Data Type | Constraint | Description |
| :--- | :--- | :--- | :--- | :--- |
| `books` | `id` | UUID | PK | 원본 도서 고유 ID |
| | `title` | VARCHAR | NOT NULL | 도서명 |
| | `virtual_stock` | INT | DEFAULT 0 | [WMS] 실제 가용 재고 수량 |
| `locations` | `id` | UUID | PK | [WMS] 창고 진열대 (예: A-1-3) |
| `orders` | `id` | UUID | PK | [WMS] 가상 고객 주문 데이터 |
| `return_jobs` | `id` | UUID | PK | 반품 검수 작업 고유 ID |
| | `status` | VARCHAR | NOT NULL | PENDING, PROCESSING, RESTOCKED, REJECTED |
| | `agent_logs` | **JSONB** | NULL | LangSmith Trace ID 및 Reason Codes 등 히스토리 |
| | `final_report` | TEXT | NULL | Report Agent가 작성한 거래처 송부용 사유서 |
| `inventory_logs`| `id` | UUID | PK | [WMS] 입출고 트랜잭션 기록 |

---


## 5. 개발 마일스톤 (6+1 Week 압축 Roadmap)

```text
+-------------------------------------------------------------------------------+
|                       6+1 Week Project Roadmap (Multi-Agent)                  |
+-------------------+-------------------+-------------------+-------------------+
|  Phase 1 (W1)     |  Phase 2 (W2-W3)  |  Phase 3 (W4-W5)  |  Phase 4 (W6-W7)  |
|  PoC & Planning   |  Core & LangGraph |  Dashboard & UI   |  Freeze & QA Prep |
+-------------------+-------------------+-------------------+-------------------+
| [Tech PM & BE-2/3] | [Tech PM & BE-3]  | [Tech PM & BE-2]  | [Tech PM & BE-3]  |
| 인프라 프로비저닝 | WMS API(출입고)   | SSE 푸시 및 AI통계| W6: Code Freeze   |
| WMS DB 설계 완성  | WMS 코어 로직 구축| AI결과 WMS 연동   | W7: QA & 버그픽스 |
| [AI Lead & BE-1]  | [AI Lead & BE-1]  | [AI Lead & BE-1]  | [AI Lead & BE-1]  |
| 단일 노드 PoC     | 4-Agent 조립 완성 | 프롬프트 고도화   | 시연용 도서 데이터|
| 원시 데이터 확보  | 오토 라벨링 파이프| FDS/어뷰징 탐지   | YOLO 증류 하이브리드|
|                   |                   | (CronJob 배치)    |                   |
| [FE-1 & FE-2]     | [FE-1 & FE-2]     | [FE-1 & FE-2]     | [FE-1 & FE-2]     |
| 보일러플레이트    | 카메라 연동 &     | 대시보드(WMS) 개발| UI 애니메이션 연출|
| 와이어프레임 설계 | 캔버스 리사이징   | 에이전트 로그 뷰어| 크로스 브라우징 QA|
+-------------------+-------------------+-------------------+-------------------+
```


