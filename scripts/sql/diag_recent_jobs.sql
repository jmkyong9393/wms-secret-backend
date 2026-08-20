-- MINT 100 전건 판정 진단 — 운영 DB 읽기 전용.
--
-- 가설 판별: yolo_candidates가 언제부터 0이 됐는가.
--   특정 배포일부터 0  → 코드 변경(랩핑/서명 등)이 원인
--   서서히/카메라 수정일부터 0 → 입력 이미지 품질(초점)이 원인 — 롤백 무효
-- SELECT만 쓴다.

\pset pager off
\pset footer off

\echo '=================== A. 날짜별 추이 — 후보·결함·MINT ==================='
SELECT
  created_at::date                                                         AS 날짜,
  count(*)                                                                 AS 건수,
  round(avg(jsonb_array_length(COALESCE(agent_logs->'yolo_candidates','[]'::jsonb))),1) AS 평균YOLO후보,
  count(*) FILTER (WHERE jsonb_array_length(COALESCE(agent_logs->'yolo_candidates','[]'::jsonb)) = 0) AS 후보0건,
  round(avg(jsonb_array_length(COALESCE(agent_logs->'defects','[]'::jsonb))),1)         AS 평균확정결함,
  count(*) FILTER (WHERE ubci_score = 100)                                 AS 만점,
  count(*) FILTER (WHERE ubci_score >= 95)                                 AS MINT,
  round(avg(ubci_score),1)                                                 AS 평균점수
FROM return_jobs
GROUP BY 1 ORDER BY 1;

\echo ''
\echo '=================== B. 최근 12건 상세 ==================='
SELECT
  created_at::timestamp(0)                                                 AS 시각,
  left(COALESCE(agent_logs->>'lpn_barcode', id::text), 20)                 AS lpn,
  status                                                                   AS 상태,
  ubci_score                                                               AS 점수,
  jsonb_array_length(COALESCE(agent_logs->'yolo_candidates','[]'::jsonb))  AS yolo후보,
  jsonb_array_length(COALESCE(agent_logs->'defects','[]'::jsonb))          AS 확정결함,
  jsonb_array_length(COALESCE(image_urls,'[]'::jsonb))                     AS 이미지수,
  COALESCE(agent_logs->'invalid_image_indexes','[]'::jsonb)::text          AS 무효컷,
  (agent_logs ? 'node_tokens')                                             AS 계측,
  COALESCE(agent_logs->>'reason_code','-')                                 AS 사유
FROM return_jobs
ORDER BY created_at DESC LIMIT 12;

\echo ''
\echo '=================== C. 최근 5건 — Vision이 뭐라고 했나 ==================='
SELECT
  created_at::timestamp(0)                       AS 시각,
  left(COALESCE(agent_logs->>'vision_text',''), 160) AS vision_판독문,
  left(COALESCE(agent_logs->>'special_notes',''), 80) AS 특이사항
FROM return_jobs
ORDER BY created_at DESC LIMIT 5;

\echo ''
\echo '=================== D. 계측 도입(08-17) 전후 비교 ==================='
SELECT
  CASE WHEN created_at::date >= DATE '2026-08-17' THEN '08-17 이후' ELSE '08-16 이전' END AS 구간,
  count(*)                                                                 AS 건수,
  round(avg(jsonb_array_length(COALESCE(agent_logs->'yolo_candidates','[]'::jsonb))),1) AS 평균YOLO후보,
  round(avg(jsonb_array_length(COALESCE(agent_logs->'defects','[]'::jsonb))),1)         AS 평균확정결함,
  round(100.0*count(*) FILTER (WHERE ubci_score >= 95)/count(*),1)         AS MINT비율
FROM return_jobs
GROUP BY 1 ORDER BY 1;
