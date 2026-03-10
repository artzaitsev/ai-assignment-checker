UPDATE submissions AS s
SET status = CASE
        WHEN (
            SELECT d.status
            FROM deliveries AS d
            WHERE d.submission_id = s.id
            ORDER BY d.created_at DESC
            LIMIT 1
        ) = 'skipped' THEN $5
        ELSE $4
    END,
    claimed_by = NULL,
    claimed_at = NULL,
    lease_expires_at = NULL,
    last_error_code = NULL,
    last_error_message = NULL,
    updated_at = NOW()
WHERE s.public_id = $1
  AND s.status = $2
  AND s.claimed_by = $3
  AND s.lease_expires_at > NOW()
RETURNING s.public_id;
