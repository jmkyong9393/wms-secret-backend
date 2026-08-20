-- 특정 LPN 검수 전체 추적 — 읽기 전용. \set lpn 으로 대상 지정 불가(파일 고정)라 최근 재검수 우선.
\pset pager off
\pset tuples_only on
\pset format unaligned
SELECT jsonb_build_object(
  'lpn', agent_logs->>'lpn_barcode', 'status', status, 'score', ubci_score,
  'updated', updated_at::text,
  'urls', image_urls,
  'candidates', COALESCE(agent_logs->'yolo_candidates','[]'::jsonb),
  'defects', COALESCE(agent_logs->'defects','[]'::jsonb),
  'invalid', COALESCE(agent_logs->'invalid_image_indexes','[]'::jsonb),
  'detector_text', agent_logs->>'detector_text',
  'vision_text', left(COALESCE(agent_logs->>'vision_text',''), 250),
  'reason', agent_logs->>'reason_code',
  'deduction_basis', COALESCE(agent_logs->'deduction_basis','[]'::jsonb)
)
FROM return_jobs
WHERE agent_logs->>'lpn_barcode' = 'LPN-260820-A034'
ORDER BY updated_at DESC NULLS LAST LIMIT 1;
