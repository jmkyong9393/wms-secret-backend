# codemod — 문서 일괄 수정 스크립트 (적용 완료)

프로젝트 문서 수십 개를 한 번에 고칠 때 쓴 일회성 스크립트다.
**이미 적용이 끝났으므로 다시 실행하지 않는다** — 대상 문자열이 이미 바뀌어 있어
치환이 일어나지 않거나, 되레 잘못 덮어쓴다.

## 무엇을 했나

| 묶음 | 스크립트 | 내용 |
| --- | --- | --- |
| 프로젝트명 확정 | `apply_nexus_project_name.py` · `apply_nexus_to_all_55_docs.py` | 구 명칭 → **Nexus**로 전 문서 통일 |
| 버전 동기화 | `sync_v2_6_0_*.py` · `update_v2_6_*.py` · `update_v2_7_0_0_docs.py` · `fix_micro_version_*.py` | 릴리스마다 문서 버전 헤더 일괄 갱신 |
| 아키텍처 반영 | `apply_architecture_v2_3_upgrade.py` · `update_architecture_v2_3_docs.py` · `update_langgraph_arch_doc.py` · `simplify_vision_parallel_arch.py` | 파이프라인 개편을 문서에 반영 |
| 용어 정정 | `replace_excellent_with_good_across_all_docs.py` | 등급 명칭 EXCELLENT → GOOD |
| 기타 | `append_history_docs.py` · `enrich_*.py` · `update_pm_docs.py` · `update_author_to_jmkyoo.py` · `audit_and_merge_g5khm.py` · `revert_v2_6_version.py` | 이력 추가 · 저자 표기 · 병합 감사 |

## 왜 남겨 두나

"언제 무엇을 왜 일괄로 바꿨는가"의 근거다. 예를 들어 등급 명칭이 EXCELLENT였다가
GOOD이 된 것은 이 스크립트가 유일한 기록이다. 지우면 옛 문서와 현행의 차이를
설명할 수 없다.

문서 정본은 `개인개발가이드/`(내부)와 `WMS_docs/`(배포본)에 있다.
