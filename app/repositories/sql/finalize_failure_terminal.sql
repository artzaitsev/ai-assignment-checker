WITH target AS (
  SELECT id
  FROM submissions
  WHERE public_id = $1
    AND status = $2
    AND claimed_by = $3
    AND lease_expires_at > NOW()
  FOR UPDATE
)
UPDATE submissions s
SET status = $4,
    last_error_code = $5,
    last_error_message = $6,
    claimed_by = NULL,
    claimed_at = NULL,
    lease_expires_at = NULL,
    updated_at = NOW()
FROM target
WHERE s.id = target.id
RETURNING s.public_id;
