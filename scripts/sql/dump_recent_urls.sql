-- 최근 검수 이미지 URL 추출 (선명도 비교용) — 읽기 전용.
\pset pager off
\pset tuples_only on
\pset format unaligned
SELECT jsonb_agg(jsonb_build_object('lpn', agent_logs->>'lpn_barcode', 'urls', image_urls, 'date', created_at::date))
FROM (SELECT * FROM return_jobs ORDER BY created_at DESC LIMIT 6) t;
