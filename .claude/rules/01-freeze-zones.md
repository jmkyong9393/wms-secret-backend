# 코드 프리즈 구역 (Freeze Zones) 상세 규칙

> 최종 개정: 2026-08-04 (조장 예외 승인 하에 파이프라인 구조 개편 + 규정 문구 사실 정정)
> 개편 전 원본은 `archive/2026-08-04_freeze_exception_pipeline_v2.11/`에 보존.

## LangGraph 파이프라인 (모듈 격리 원칙)

현행 그래프 구조:

```
START ➔ Detector(YOLO) ➔ Vision(GPT-4o) ➔ Policy ➔ Critic ➔ Supervisor
                              ▲                                  │
                              └──── RETRY_VISION (최대 2회) ◄─────┤
                                                                 ├─ ESCALATE_HUMAN ➔ human_node ➔ END
                                                                 └─ ISSUE_REPORT ➔ Report Agent ➔ END
```

- 각 단계는 별도 노드/함수로 유지한다. 단일 에이전트나 단일 프롬프트로 병합하지 않는다.
- 판정(Agent)과 집행(WMS Action)의 책임을 섞지 않는다. 랙 배정·자동 매입/환불 같은
  부수 효과는 워커(`app/worker/tasks.py`)가 담당하고, 그래프는 판정만 낸다.

## 모델 배정 (사실 기준 - 2026-08-04 정정)

| 노드 | 모델 | 비고 |
|---|---|---|
| Detector Node | **LLM 미사용** | WBF 3-YOLO 앙상블, 결정론적 |
| Vision Agent | `GPT-4o` | 멀티모달 정밀 판독 (+ 예비감점 검증에 `GPT-4o-mini`) |
| Policy Agent | **LLM 미사용** | UBCI_Specification 매트릭스 산식, 결정론적 |
| Critic Agent | Stage A **LLM 미사용** / Stage B `GPT-4o-mini` | A=정합성 대조, B=판독 타당성 심사 |
| Supervisor | **LLM 미사용** | 규칙 기반 지휘 라우팅, 감사 추적 가능 |
| Report Agent | `GPT-4o-mini` | 고객 보증서 문서 생성 (구 ExplainerAgent 역할 흡수) |

> **[정정 이력]** 종전 규정은 "Policy / Critic / Report = GPT-4o-mini 고정 (비용 최적화)"라고
> 기재했으나, 실제 코드에서 Policy와 Supervisor는 LLM을 단 한 번도 호출하지 않는 순수
> 결정론적 함수였다. UBCI 등급은 매입가(금액)를 결정하는 규정 산식이므로 재현성이
> 필수이고, 여기에 LLM을 넣으면 오히려 감사 추적성이 깨진다. **문서가 코드를 따라가도록**
> 위 표로 정정한다. 발표/문서에서 "4-Agent 전부 LLM 구동"이라고 기술하지 말 것.

### Critic 2단 구조 (2026-08-04 신설)

- **Stage A (결정론적 게이트, LLM 없음)**: Vision 결함 수 ↔ Policy 감점 정합성, BBox 누락,
  `image_index` 범위 초과, 결함 0건인데 감점 발생 등 **사실 대조**. 위반 시 즉시 HITL.
- **Stage B (`GPT-4o-mini`)**: Stage A를 통과하고 **결함이 1건 이상일 때만** 실행.
  "판독한 결함이 실제로 타당한가"(인쇄물 오탐, 중복 환각, special_notes 모순 등)를 심사.
  결함 0건이면 호출하지 않으므로 MINT 물량에서 추가 비용이 발생하지 않는다.
- Stage B는 부가 검증이므로 **fail-open**: LLM 장애 시 Stage A 결과만으로 진행한다.
- Stage B 출력은 반드시 `with_structured_output(CriticVerdict)`로 받는다.
  응답 텍스트를 `ast.literal_eval`/`json.loads`로 파싱하면 코드블록이나 서론 한 줄에
  예외가 나며 노드가 죽는다.

## 제거된 구조 (되살리지 말 것)

- **MINT Fast-track 분기 (`route_from_vision`)** — 2026-08-04 제거.
  `is_mint=True`면 Policy/Critic/Supervisor를 건너뛰고 자동 매입 승인까지 직행했다.
  명분은 비용 최적화였으나 우회 대상 3개 노드가 LLM을 전혀 쓰지 않아 **절약 효과는 0건**이었고,
  금전적 확정이 환각 방어(Critic) 검증을 건너뛰는 위험만 남았다.
  실제 사고: OpenAI 키 만료로 VLM이 401을 반환하자 `defects`가 빈 배열로 남아
  `is_mint=True`로 해석되었고, **모든 반품 도서가 검증 없이 UBCI 100점 MINT로 자동 매입 승인**됐다.
  "MINT 자동 매입"이라는 비즈니스 기능은 `auto_refund_eligible` 플래그로 보존되어 있으며,
  등급이 전 검증 경로를 통과한 뒤에만 워커가 집행한다.
- **`auto_refund_agent` 노드** — 위 분기 제거로 도달 불가가 되었고, 하는 일도 Report Agent와
  동일했다(양쪽 다 `build_certificate_document()` 1회 호출).

## 판독 실패 처리 원칙 (CRITICAL)

- **"검수하지 못했다"와 "검수했더니 흠이 없다"를 절대 같게 취급하지 않는다.**
  VLM 호출 실패 시 `defects`가 비어 있다는 이유로 MINT/100점을 부여하는 코드를 다시 만들지 말 것.
- 판독 실패는 `vision_failed=True`로 표시하고 `ubci_score`를 `None`으로 남긴다.
  Critic의 기존 재검수 루프(최대 2회)가 재시도를 담당하고, 소진되면 Supervisor가 HITL로 이관한다.
- YOLO 사전탐지 후보는 상태에 보존하되, **임의의 ratio/감점 값으로 변환해 등급을 매기지 않는다.**
  HITL 관리자의 참고 증거로만 쓴다.

## 예외 처리 절차

- 프리즈 구역을 반드시 건드려야 하는 문제가 발견되면, **코드를 수정하지 말고** 문제를 요약해
  사용자에게 보고하고 명시적 예외 승인을 먼저 받는다.
- 예외 승인을 받더라도, 수정 전 해당 블록을 `archive/`에 별도 백업한 뒤 진행한다.
