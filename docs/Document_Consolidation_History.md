# 🗂️ B2B WMS AI Platform 문서 대통합 히스토리 (Consolidation History)

## 1. 통합 개요 (Overview)
프로젝트 기획 및 설계 과정에서 생성된 20여 개의 마크다운(.md) 문서들이 파편화되어 열람 및 유지보수가 어려워지는 문제를 해결하기 위해, 성격이 비슷한 문서들을 **3개의 마스터 통합본**으로 병합(Consolidation)하는 대대적인 구조조정을 진행했습니다.

## 2. 주요 정비 사항 (Key Refactoring Points)
통합 스크립트 실행 시, 단순히 텍스트만 합친 것이 아니라 전 문서에 걸쳐 아래의 텍스트 리팩토링을 동시 수행했습니다.
* **UBCI 브랜딩:** 외부 의존적으로 보이던 `알라딘 데이터 기반 평가지표`, `알라딘 기준` 등의 단어를 파싱하여 모두 **`자체 개발 중고 도서 상태 평가지수 (UBCI - Used Book Condition Index)`** 로 100% 전면 교체.
* **구버전 잔재 청소:** `Celery`, `Redis`, `MSA 에이전트별 Pod 분리` 등 과거 기획 회의에서 논의되었다가 기각(Dropped)된 파편들을 일괄 색인 및 삭제.

---

## 3. 문서 통폐합 매핑 보드 (Consolidation Mapping)

다음 18개의 파편화된 구버전 파일들은 3개의 통합본으로 병합된 후, 저장소의 깔끔한 관리를 위해 일괄 삭제(Clean-up) 되었습니다.

### ⚙️ 01_엔지니어링_산출물_통합본.md
개발 및 아키텍처 설계에 필요한 시스템 코어 문서들을 하나로 통합했습니다.
1. `1_요구사항_정의서_Draft.md`
2. `2_아키텍처_정의서_Draft.md`
3. `3_ERD_설계서_Draft.md`
4. `Backend_Core_WMS_DB_Schema_Plan.md`
5. `4_API_명세서_Draft.md`
6. `5_서비스_플로우_Draft.md`
7. `6_UI_UX_설계서_Draft.md`

### 💼 02_프로젝트_기획_및_피칭_통합본.md
비즈니스 기획, 발표 스크립트, 질의응답 등 대외적인 설득 및 커뮤니케이션 문서들을 하나로 통합했습니다.
8. `project_definition_brief.md` *(※ 잦은 열람을 위해 별도 파일로도 재추출하여 병행 유지)*
9. `Project_Definition_Pitch_Script.md`
10. `project_definition_qa.md`
11. `qna_interview_prep.md`
12. `technical_glossary.md` *(※ 잦은 열람을 위해 별도 파일로도 재추출하여 병행 유지)*
13. `W1_Standup_Meeting_Agenda.md`

### 📊 03_프로젝트_평가_및_회고_통합본.md
프로젝트의 아키텍처 딥다이브, 한계점 방어 전략, 객관적인 자체 평가 문서들을 하나로 통합했습니다.
14. `B2B_WMS_AI_Platform_DeepDive.md`
15. `B2B_WMS_AI_Platform_Mitigation_Plan.md`
16. `B2B_WMS_AI_Platform_Final_Grading.md`
17. `Project_Evaluation_Report.md`
18. `B2B_WMS_AI_Platform_Cold_Evaluation.md`

---

> **기록일시:** 2026년 7월 1일
> **수행자:** Tech PM 장문경 (통합 관리자 & 트러블슈터) & AI Assistant (Antigravity)


## [v1.4.2.0 업데이트] 아키텍처 전면 개편 및 고도화 반영
* 프론트엔드 WebRTC/WASM 및 낙관적 UI 적용
* 백엔드 FastAPI, Celery, LangGraph Multi-Agent 분산 파이프라인 적용
* Tech PM 역할을 Git Gatekeeper 및 EKS 인프라 리드로 승격
* 6+1주 스케줄 전면 도입 및 전 문서 동기화 완료