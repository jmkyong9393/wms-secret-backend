-- 검수 파이프라인 성능 지표 — 운영 DB 읽기 전용 집계.
--
-- 탐지기 단일 정밀도가 아니라 "시스템이 낸 최종 판정이 사람 판단과 얼마나 맞았나"를 본다.
-- 관리자 결재(hitl_bbox_edit)가 무엇을 뒤집었는지가 그대로 남아 있어 역산이 가능하다.
--   added_bboxes  = AI가 놓친 것      → 미탐(FN)
--   excluded      = AI가 잘못 잡은 것 → 오탐(FP)
--   나머지 확정분 = 사람이 인정한 것  → 정탐(TP)
--
-- SELECT만 쓴다. 운영 DB에 어떤 변경도 가하지 않는다.

\pset pager off
\pset footer off

\echo '=================== 1. 표본 규모 ==================='
SELECT
  count(*)                                   AS 전체건수,
  count(DISTINCT image_urls::text)           AS 고유이미지조합,
  count(*) FILTER (WHERE agent_logs ? 'node_tokens') AS 계측보유,
  min(created_at)::date                      AS 최초,
  max(created_at)::date                      AS 최종
FROM return_jobs;

\echo ''
\echo '=================== 2. 최종 상태 분포 (자동화율) ==================='
SELECT
  status                                                   AS 상태,
  count(*)                                                 AS 건수,
  round(100.0 * count(*) / SUM(count(*)) OVER (), 1)       AS 비율
FROM return_jobs GROUP BY status ORDER BY 2 DESC;

\echo ''
\echo '=================== 3. 고유 이미지 기준 (복제 제거) ==================='
WITH uniq AS (
  SELECT DISTINCT ON (image_urls::text) * FROM return_jobs
  ORDER BY image_urls::text, created_at DESC
)
SELECT status AS 상태, count(*) AS 건수,
       round(100.0 * count(*) / SUM(count(*)) OVER (), 1) AS 비율
FROM uniq GROUP BY status ORDER BY 2 DESC;

\echo ''
\echo '=================== 4. 관리자 결재로 본 AI 판정 성적 ==================='
-- HITL로 넘어가 사람이 실제로 검토한 건만 대상. 자동 확정분은 사람이 안 봤으므로 제외한다.
WITH e AS (
  SELECT id,
         agent_logs->'hitl_bbox_edit'                         AS edit,
         jsonb_array_length(COALESCE(agent_logs->'defects','[]'::jsonb)) AS ai_defects
  FROM return_jobs WHERE agent_logs ? 'hitl_bbox_edit'
), agg AS (
  SELECT
    count(*)                                                              AS 검토건수,
    SUM(jsonb_array_length(COALESCE(edit->'added_bboxes','[]'::jsonb)))   AS 미탐_FN,
    SUM(jsonb_array_length(COALESCE(edit->'excluded','[]'::jsonb)))       AS 오탐_FP,
    SUM(ai_defects)                                                       AS AI제시총건
  FROM e
)
SELECT 검토건수, AI제시총건, 오탐_FP, 미탐_FN,
       (AI제시총건 - 오탐_FP)                                             AS 정탐_TP,
       CASE WHEN AI제시총건 > 0
            THEN round(100.0*(AI제시총건-오탐_FP)/AI제시총건, 1) END      AS 정밀도_pct,
       CASE WHEN (AI제시총건 - 오탐_FP + 미탐_FN) > 0
            THEN round(100.0*(AI제시총건-오탐_FP)/(AI제시총건-오탐_FP+미탐_FN), 1) END AS 재현율_pct
FROM agg;

\echo ''
\echo '=================== 5. 결함 유형 분포 (확정분) ==================='
SELECT d->>'type' AS 유형, count(*) AS 건수,
       round(avg((d->>'confidence')::numeric), 3) AS 평균확신도
FROM return_jobs r, jsonb_array_elements(COALESCE(r.agent_logs->'defects','[]'::jsonb)) d
WHERE d->>'type' IS NOT NULL
GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '=================== 6. UBCI 점수 분포 ==================='
SELECT COALESCE(agent_logs->>'final_grade','(미산정)') AS 등급, count(*) AS 건수,
       round(avg((agent_logs->>'ubci_score')::numeric), 1) AS 평균점수
FROM return_jobs GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '=================== 7. 방어 게이트 발동 이력 ==================='
SELECT COALESCE(agent_logs->>'primary_reason_code','(없음)') AS 사유코드, count(*) AS 건수
FROM return_jobs GROUP BY 1 ORDER BY 2 DESC LIMIT 12;

\echo ''
\echo '=================== 8. LLM 비용 실측 (계측 보유분) ==================='
SELECT count(*) AS 계측건수,
       round(avg((agent_logs->'cost_summary'->>'total_cost_usd')::numeric), 6) AS 건당평균USD,
       round(avg((agent_logs->'cost_summary'->>'total_tokens')::numeric), 0)   AS 건당평균토큰
FROM return_jobs WHERE agent_logs->'cost_summary' ? 'total_cost_usd';
