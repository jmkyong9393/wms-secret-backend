-- A008~A013 테스트 배치 전체 추적 — 읽기 전용.
\pset pager off
\pset tuples_only on
\pset format unaligned
SELECT jsonb_agg(jsonb_build_object(
  'lpn',   agent_logs->>'lpn_barcode',
  'status', status,
  'score', ubci_score,
  'urls',  image_urls,
  'candidates', COALESCE(agent_logs->'yolo_candidates','[]'::jsonb),
  'defects',    COALESCE(agent_logs->'defects','[]'::jsonb),
  'invalid',    COALESCE(agent_logs->'invalid_image_indexes','[]'::jsonb),
  'detector_text', agent_logs->>'detector_text',
  'vision_text',   left(COALESCE(agent_logs->>'vision_text',''), 200),
  'reason', agent_logs->>'reason_code'
) ORDER BY agent_logs->>'lpn_barcode')
FROM return_jobs
WHERE agent_logs->>'lpn_barcode' BETWEEN 'LPN-260820-A008' AND 'LPN-260820-A013';
