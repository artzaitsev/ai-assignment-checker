WITH candidate AS (
  SELECT id
  FROM submissions
  WHERE status = $1
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE submissions AS s
SET status = $2,
    claimed_by = $3,
    claimed_at = NOW(),
    lease_expires_at = NOW() + make_interval(secs => $4::int),
    updated_at = NOW()
FROM candidate
WHERE s.id = candidate.id
RETURNING s.public_id, s.{attempt_field}, s.lease_expires_at;
