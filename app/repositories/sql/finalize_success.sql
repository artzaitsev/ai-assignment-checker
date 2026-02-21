UPDATE submissions
SET status = $4,
    claimed_by = NULL,
    claimed_at = NULL,
    lease_expires_at = NULL,
    last_error_code = NULL,
    last_error_message = NULL,
    updated_at = NOW()
WHERE public_id = $1
  AND status = $2
  AND claimed_by = $3
  AND lease_expires_at > NOW()
RETURNING public_id;
