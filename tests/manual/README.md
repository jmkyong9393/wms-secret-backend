# tests/manual — 수동 실행 검증 스크립트

**pytest 대상이 아니다.** 파일명이 `test_`로 시작하지만 픽스처·단언 구조가 아니라
직접 실행해 출력을 눈으로 확인하는 스크립트다. 외부 자원(실제 이미지, OpenAI API,
로컬 모델 파일)을 요구해 CI에서 돌릴 수 없어 여기 분리했다.

| 파일 | 검증 대상 | 필요한 것 |
| --- | --- | --- |
| `test_dual_yolo_wbf_ensemble.py` | WBF 앙상블 융합 결과 | 로컬 `.pt` 3종 |
| `test_e2e_real_image_inference.py` | 실촬영 이미지 전 구간 추론 | 이미지 + OpenAI 키 |
| `test_e2e_nexus_features.py` | 주요 기능 종단 흐름 | 기동 중인 API |
| `test_local_multiagent_pipeline.py` | LangGraph 노드 연결 | OpenAI 키 |
| `test_vlm_doodle_detection.py` | 낙서 판독 정확도 | 이미지 + OpenAI 키 |

## 자동 테스트는 어디에

| 위치 | 내용 | 실행 |
| --- | --- | --- |
| `tests/unit/` | 단위 테스트 | `pytest tests/unit` |
| `tests/api/` | API 통합 테스트 | `pytest tests/api` |
| `tests/test_ubci_matrix_equivalence.py` | UBCI 산식 동일성 | `pytest` |

CI(`pr-check.yml`)는 `pytest`로 위 셋만 돌린다. 이 폴더는 수집되지 않는다.

> ⚠ 파일명이 `test_`로 시작해 **pytest가 수집하려 시도할 수 있다.**
> 수집돼서 import 단계에서 깨지면 `pyproject.toml`의 pytest 설정에
> `--ignore=tests/manual` 또는 `norecursedirs`를 추가한다.
