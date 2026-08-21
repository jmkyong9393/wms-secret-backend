-- HITL completed 20초 지연 진단 (읽기 전용)
-- ① 테이블 크기·행수 ② agent_logs 크기 분포 ③ 해당 쿼리 실행계획

\timing on

SELECT count(*) AS total_rows,
       pg_size_pretty(pg_total_relation_size('return_jobs')) AS table_total,
       pg_size_pretty(pg_relation_size('return_jobs')) AS heap_only
FROM return_jobs;

SELECT status, count(*),
       pg_size_pretty(avg(pg_column_size(agent_logs))::bigint) AS avg_logs,
       pg_size_pretty(max(pg_column_size(agent_logs))::bigint) AS max_logs,
       pg_size_pretty(sum(pg_column_size(agent_logs))::bigint) AS sum_logs
FROM return_jobs
GROUP BY status ORDER BY count(*) DESC;

EXPLAIN (ANALYZE, BUFFERS, TIMING)
SELECT rj.id, rj.agent_logs, b.title
FROM return_jobs rj
LEFT JOIN books b ON rj.book_id = b.id
WHERE rj.status IN ('APPROVED', 'REJECTED')
ORDER BY rj.updated_at DESC;
