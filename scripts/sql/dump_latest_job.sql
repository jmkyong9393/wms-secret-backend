-- 최신 검수 1건 전체 덤프 (좌표 진단용) — 읽기 전용.
\pset pager off
\pset tuples_only on
\pset format unaligned
SELECT jsonb_build_object(
  'lpn', agent_logs->>'lpn_barcode', 'urls', image_urls,
  'candidates', COALESCE(agent_logs->'yolo_candidates','[]'::jsonb),
  'defects', COALESCE(agent_logs->'defects','[]'::jsonb),
  'old_defect_count', jsonb_array_length(COALESCE(agent_logs->'defects','[]'::jsonb))
) FROM return_jobs ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1;
