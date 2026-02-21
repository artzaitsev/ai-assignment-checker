UPDATE submissions
SET status = $4,
    {attempt_field} = {attempt_field} + 1,
    claimed_by = NULL,
    claimed_at = NULL,
    lease_expires_at = NULL,
    last_error_code = $5,
    last_error_message = $6,
    updated_at = NOW()
WHERE public_id = $1
  AND status = $2
  AND claimed_by = $3
  AND lease_expires_at > NOW()
  AND {attempt_field} + 1 < $7
RETURNING public_id;
