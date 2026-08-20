-- A/B 실험 재료 추출 — 읽기 전용. 사람이 결함을 확인한 실촬영 1건의 입력만 뽑는다.
\pset pager off
\pset tuples_only on
\pset format unaligned
SELECT jsonb_build_object(
  'lpn', agent_logs->>'lpn_barcode',
  'image_urls', image_urls,
  'yolo_candidates', COALESCE(agent_logs->'yolo_candidates','[]'::jsonb),
  'old_defect_count', jsonb_array_length(COALESCE(agent_logs->'defects','[]'::jsonb))
)
FROM return_jobs
WHERE agent_logs->>'lpn_barcode' = 'LPN-260810-A012'
ORDER BY created_at DESC LIMIT 1;
