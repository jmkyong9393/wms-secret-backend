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
\echo '=================== 6. UBCI 점수·등급 분포 ==================='
-- ubci_score는 agent_logs가 아니라 return_jobs의 컬럼이다.
-- 등급 문자열은 agent_logs.suggested_grade에 들어간다(final_grade는 저장되지 않는다).
-- suggested_grade는 나중에 추가된 키라 옛 건에는 없다. 점수에서 계산해 채운다.
-- 경계값은 ubci_grade_from_score()와 동일하다 (S>=95 / A>=85 / B>=65 / else REJECT).
SELECT
  CASE
    WHEN ubci_score IS NULL THEN '(점수 없음)'
    WHEN ubci_score >= 95   THEN 'MINT'
    WHEN ubci_score >= 85   THEN 'GOOD'
    WHEN ubci_score >= 65   THEN 'NORMAL'
    ELSE 'REJECT'
  END                                                  AS 등급,
  count(*)                                             AS 건수,
  round(100.0*count(*) / SUM(count(*)) OVER (), 1)     AS 비율,
  round(avg(ubci_score), 1)                            AS 평균점수,
  min(ubci_score)                                      AS 최저,
  max(ubci_score)                                      AS 최고
FROM return_jobs GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '--- 점수 미산정 건 (판독 실패·게이트 차단 = 점수를 주지 않는 것이 정상) ---'
SELECT status AS 상태, count(*) AS 건수
FROM return_jobs WHERE ubci_score IS NULL GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '=================== 7. 방어 게이트 발동 이력 ==================='
-- reason_code가 게이트 사유다. primary_reason_code는 주 결함 유형(HITL 드롭다운 초기값)이라
-- 게이트 지표로 쓰면 안 된다.
SELECT COALESCE(agent_logs->>'reason_code','(정상 통과)') AS 게이트사유, count(*) AS 건수
FROM return_jobs GROUP BY 1 ORDER BY 2 DESC LIMIT 12;

\echo ''
\echo '--- MINT(무결점) 및 자동 매입 대상 비율 ---'
SELECT
  count(*) FILTER (WHERE (agent_logs->>'is_mint')::boolean)              AS mint건수,
  count(*) FILTER (WHERE (agent_logs->>'auto_refund_eligible')::boolean) AS 자동매입대상,
  count(*)                                                              AS 전체,
  round(100.0 * count(*) FILTER (WHERE (agent_logs->>'is_mint')::boolean) / count(*), 1) AS mint_pct
FROM return_jobs;

\echo ''
\echo '=================== 8. LLM 비용 실측 (계측 보유분) ==================='
SELECT count(*) AS 계측건수,
       round(avg((agent_logs->'cost_summary'->>'total_cost_usd')::numeric), 6) AS 건당평균USD,
       round(avg((agent_logs->'cost_summary'->>'total_tokens')::numeric), 0)   AS 건당평균토큰
FROM return_jobs WHERE agent_logs->'cost_summary' ? 'total_cost_usd';
