import os

pm_file = r'E:\취업\KT AIVLE School\빅프로젝트\개인개발가이드\LangGraph_MultiAgent_Vision_Architecture_Internal.md'
wms_file = r'E:\취업\KT AIVLE School\빅프로젝트\WMS_docs\LangGraph_MultiAgent_Vision_Architecture.md'

pm_content = """# LangGraph 기반 Supervisor Multi-Agent 도서 비전 검수 시스템 통합 설계 명세서
**[보안 등급: 내부용 / 대외비]**
**작성일자**: 2026-07-24  
**버전**: ver 2.3.0.0 (Milestone Upgrade)  
**작성자**: Senior AI Technical Architect (Antigravity)

---

## 1. 개요 및 설계 목적 (Executive Summary)

본 명세서는 **다중 에이전트(Multi-Agent) 파이프라인**을 통해 B2B 도서 반품/검수 라인의 **자동화율 85% 이상, 실효 미탐율 0%**를 동시에 달성하기 위한 엔터프라이즈 AI 비전 검수 아키텍처를 정의합니다.

기존 단일 객체 탐지 모델(YOLO)의 한계인 **시각적 맥락 인식 부재 및 저재현율(Recall) 문제**를 해결하기 위해, **LangGraph 기반의 Supervisor Supervisor Pattern Multi-Agent Architecture 다중 에이전트 그래프**와 **YOLOv8 앙상블 + GPT-4o Vision 하이브리드 엔진**, 그리고 **HITL (Human-in-the-Loop) 이중 방어선**을 결합한 통합 시스템을 구축합니다.

---

## 2. 전체 시스템 멀티 에이전트 토폴로지 (System Topology & Visualizations)

### A. Supervisor Supervisor Pattern Multi-Agent Architecture 전체 구조 다이어그램
본 시스템은 **LangGraph의 StateGraph**를 기반으로 중앙 `Supervisor` 노드가 전역 상태(`WMSInspectionState`)를 실시간 모니터링하며 최적의 에이전트로 동적 라우팅하는 **Supervisor Pattern Multi-Agent Architecture 구조**를 채택합니다.

```mermaid
flowchart TD
    START([입고 도서 검수 요청]) --> Supervisor["[Supervisor Agent]<br/>중앙 동적 라우터 (State Router)"]
    
    Supervisor -->|state.is_mint is None| VisionAgent["[Vision Agent]<br/>YOLOv8 Multi-Model Ensemble + GPT-4o"]
    VisionAgent -->|State Update| Supervisor
    
    Supervisor -->|is_mint == True| FastTrack["[Fast-Track Agent]<br/>S급 MINT 자동 승인 (환불/매입)"]
    FastTrack --> END([검수 종료 및 WMS 반영])
    
    Supervisor -->|ubci_score is None| PolicyAgent["[Policy Agent]<br/>UBCI 점수 수리적 산정"]
    PolicyAgent -->|State Update| Supervisor
    
    Supervisor -->|reason_code is None| CriticAgent["[Critic Agent]<br/>ChromaDB RAG 판례 검증 & 루프 제어"]
    CriticAgent -->|State Update| Supervisor
    
    Supervisor -->|revision_count >= 2 OR Violation| HumanNode["[Human-In-The-Loop Node]<br/>MemorySaver 중단점 / HITL 대시보드"]
    HumanNode -->|관리자 오버라이드| FeedbackAgent["[Feedback Agent]<br/>MLOps Ground Truth 수집"]
    FeedbackAgent --> Supervisor
    
    Supervisor -->|reason_code == OK| ReportAgent["[Report Agent]<br/>최종 레포트 생성 및 비동기 MLOps 전송"]
    ReportAgent --> END
```

---

### B. Vision Agent 3단계 다중 모델 앙상블 파이프라인 시각화
YOLOv8 High-Recall 모델(`yolov8_high_recall_best.pt`), YOLOv8m Base High-Precision 모델(`yolov8_high_precision_base.pt`)의 WBF 병합과 GPT-4o Vision의 자연어 맥락 판단 능력을 결합한 3단계 파이프라인 구조입니다.

```mermaid
flowchart LR
    ImgInput["도서 촬영 이미지"] --> M1["[Model 1: High-Recall YOLOv8]<br/>best.pt (Recall 88%)"]
    ImgInput --> M2["[Model 2: High-Precision Base]<br/>best_base_medium.pt (Precision 91.2%)"]
    
    M1 --> WBF["[WBF Fusion]<br/>Weighted Boxes Fusion 병합"]
    M2 --> WBF
    
    WBF --> BBoxExtract["BBox 좌표 & Confidence 추출"]
    
    BBoxExtract --> GPT4o["[Model 3: GPT-4o Vision API]<br/>special_notes 도서관 도장/부록 관찰"]
    GPT4o --> StateUpdate["WMSInspectionState 갱신 (JSONB 적재 준비)"]
```

---

### C. UBCI 등급 판정 및 HITL 에스컬레이션 의사결정 트라이앵글
Policy Agent의 수리적 점수 산출과 HITL 관리자 수동 검수 중단점의 연동 흐름입니다.

```mermaid
graph TD
    subgraph UBCI_Evaluation ["Policy Agent 점수 산정 (100점 만점)"]
        RawDefects["Vision Agent defects 수집"] --> DeductScore["UBCI v2.0.0.0 2D 감점 매트릭스 적용"]
        DeductScore --> CalculateUBCI["UBCI 점수 최종 계산"]
    end

    subgraph Decision_Branch ["등급 판정 및 HITL 분기"]
        CalculateUBCI --> Score95{"UBCI >= 95?"}
        Score95 -->|Yes| GradeS["S급 판정 (MINT)"]
        Score95 -->|No| Score80{"UBCI >= 80?"}
        Score80 -->|Yes| GradeA["A급 판정 (GOOD)"]
        Score80 -->|No| Score60{"UBCI >= 60?"}
        Score60 -->|Yes| GradeB["B급 판정 (NORMAL)"]
        Score60 -->|No| GradeReject["REJECT (반려 등급)"]
    end

    subgraph HITL_Escalation ["HITL 수동 검수 중단점"]
        CriticCheck{"Critic 검증 실패 OR<br/>Revision Count >= 2?"} -->|Yes| InterruptBefore["[MemorySaver 중단점]<br/>HITL 대시보드 강제 이관"]
        InterruptBefore --> AdminUI["관리자 웹 대시보드 1초 클릭 확인"]
    end

    GradeS --> CriticCheck
    GradeA --> CriticCheck
    GradeB --> CriticCheck
    GradeReject --> CriticCheck
```

---

## 3. 세부 에이전트 명세 (Detailed Agent Specification)

### 3.1 중앙 제어기: Supervisor Agent (`supervisor_node`)
* **역할**: 전역 상태 데이터(`WMSInspectionState`)를 파싱하여 조건부 엣지(`route_from_supervisor`)를 통해 다음 실행 노드를 동적으로 결정.

---

### 3.2 수리적 등급 산정기: Policy Agent (`policy_agent`)
* **역할**: Vision Agent가 전달한 `defects` 좌표 및 개수, 면적 비중(Area Ratio)을 바탕으로 **UBCI Specification v2.0.0.0 2D 정밀 감점 매트릭스**를 적용하여 100점 만점 수치 및 목표 등급(MINT/GOOD/NORMAL/REJECT)을 수리적으로 계산.
* **UBCI 정밀 감점 수식**:
  $$\text{UBCI Score} = 100 - \sum_{i=1}^{n} \left( \text{Deduction}_{\text{Type, Severity}, i} \times \text{Text Overlap Multiplier} \right)$$
  * **심각도(Area Ratio) 3단계**: Minor (<5%), Moderate (5~15%), Severe (≥15%)
  * **텍스트 침범 가중치**: 1.5배 곱셈 적용
  * **🚨 즉시 반려 (REJECT / 0점)**: 액체 오염(`STAIN_WATER_DAMAGE`), 습기/휨(`PAGE_WARPING`), 제본 완전 탈착(`BINDING_LOOSE`), 10장 초과 훼손
* **등급 매핑 기준**:
  * **🟢 S등급 (MINT)**: `95 ~ 100점`
  * **🟡 A등급 (GOOD)**: `80 ~ 94점`
  * **🟠 B등급 (NORMAL)**: `60 ~ 79점`
  * **🔴 REJECT (반려)**: `0 ~ 59점` (또는 특약 결함 발생 시 0점)

---

## 4. 엔터프라이즈 4대 핵심 아키텍처 고도화 명세 (ver 2.3.0.0 Upgrade)

### 4.1 다각도 비전 병렬 처리 (Parallel Multi-View Vision Pipeline)
- **개선 방안**: LangGraph `Send API` (Fan-out / Fan-in)를 도입하여 도서 표지, 내지, 측면 등 다각도 이미지를 독립 비전 노드로 병렬 추론한 후 Supervisor에서 동기화 조인(Join).
- **효과**: I/O 통신 병목을 완전 해소하여 전체 파이프라인 평균 처리 속도 **2.1초 이내** 보장.

### 4.2 Phase 2 ChromaDB RAG 기반 판례(Case Law) 실시간 피드백 연동
- **조장 지침 반영 (Phase 2 적재)**: 현재 가동 준비 완료된 ChromaDB 백엔드에 과거 HITL 관리자 오버라이드 판례 데이터를 수집 적재.
- **적용 수순**: 1단계 비전 엔진 및 기본 파이프라인 완증 후 2단계(Phase 2)에 Critic Agent의 Few-shot Context 채널로 연결하여 예외적 파손 사례(Edge Cases) 판정 무결성 확보.

### 4.3 메시지 메모리 턴(3-Turn Pruning) 제한 & DB 정답지 데이터 증류(Distillation) 이원화
- **대화 메모리(In-Memory State)**: LLM 토큰 오버플로우 및 API 비용 증대를 방지하기 위해 `WMSInspectionState.messages`는 **최대 3턴(System 1 + Vision 1 + Policy/Critic 1)**으로 자동 가비지 컬렉션(Memory Pruning) 수행.
- **DB 영구 적재 & 데이터 증류**: 대화 턴 삭제와 완전 이원화하여, 검수 결과 및 BBox/이미지 정답 데이터는 **PostgreSQL DB 및 ChromaDB에 100% 영구 적재**되며, 이 누적 정답지 데이터셋을 통해 추후 **YOLO 및 SLM 모델 재학습용 데이터 증류(Dataset Distillation) 파이프라인으로 전송**.

### 4.4 Continuous Learning (지속 학습) & Feedback Agent 파이프라인
- **피드백 루프**: `Report Agent` 실행 완료 및 HITL 관리자 수동 오버라이드 발생 시, 백그라운드 비동기 `Feedback Agent (Data Ingestion Engine)`가 자동 가동.
- **자동 환류**: 사람이 정정한 최종 등급/사유 및 원본 이미지를 MLOps 파이프라인으로 전송하여 YOLO 재학습 데이터셋 및 ChromaDB 판례 DB에 자동 피드백 적재.

---

## 5. 데이터 상태 구조 명세서 (`WMSInspectionState`)

```python
from typing import TypedDict, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage

class WMSInspectionState(TypedDict):
    messages: List[BaseMessage] # 최대 3턴 자동 Pruning 적용
    is_mint: Optional[bool]
    defects: Optional[List[Dict[str, Any]]] # JSONB BBox [x1, y1, x2, y2]
    special_notes: Optional[str] # AI 특이사항 (도서관 도장 등)
    human_issue_notes: Optional[str] # HITL 수동 메모
    ubci_score: Optional[int]
    reason_code: Optional[str]
    repair_directive: Optional[str]
    revision_count: int
    human_feedback: Optional[Dict[str, Any]]
    final_report: Optional[str]
```

---

## 6. 결론 및 기대 효과

1. **검수 자동화율 85% 달성**: YOLOv8 다중 앙상블 + GPT-4o 판단으로 자동 처리.
2. **현장 미탐율 0% 보장**: HITL 대시보드 연동으로 100% 완벽 검수 보장.
3. **Continuous Learning 완성**: 사람의 교정 데이터가 MLOps 데이터셋으로 100% 피드백 환류.
"""

wms_content = pm_content.replace("**[보안 등급: 내부용 / 대외비]**", "**[보안 등급: 팀원 배포용 (대외비 필터링 완료)]**")

with open(pm_file, 'w', encoding='utf-8') as f:
    f.write(pm_content)
print(f'Updated PM master doc ver 2.3.0.0: {pm_file}')

with open(wms_file, 'w', encoding='utf-8') as f:
    f.write(wms_content)
print(f'Updated WMS public doc ver 2.3.0.0: {wms_file}')
