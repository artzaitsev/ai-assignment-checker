UPDATE submissions
SET status = $2,
    {attempt_field} = {attempt_field} + 1,
    claimed_by = NULL,
    claimed_at = NULL,
    lease_expires_at = NULL,
    last_error_code = $3,
    last_error_message = $4,
    updated_at = NOW()
WHERE status = $1
  AND lease_expires_at <= NOW()
  AND {attempt_field} + 1 < $5
RETURNING public_id;
