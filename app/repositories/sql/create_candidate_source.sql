INSERT INTO candidate_sources (
  candidate_id,
  source_type,
  source_external_id,
  metadata_json
)
SELECT c.id,
       $2,
       $3,
       $4::jsonb
FROM candidates AS c
WHERE c.public_id = $1
ON CONFLICT (source_type, source_external_id) DO NOTHING
RETURNING id;
