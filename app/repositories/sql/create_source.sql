INSERT INTO submission_sources (
  submission_id,
  source_type,
  source_external_id,
  source_payload_ref,
  metadata_json
)
VALUES ($1, $2, $3, $4, $5::jsonb)
ON CONFLICT (source_type, source_external_id) DO NOTHING
RETURNING id;
