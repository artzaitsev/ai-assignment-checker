UPDATE submissions
SET status = 'dead_letter',
    {attempt_field} = {attempt_field} + 1,
    claimed_by = NULL,
    claimed_at = NULL,
    lease_expires_at = NULL,
    last_error_code = $2,
    last_error_message = $3,
    updated_at = NOW()
WHERE status = $1
  AND lease_expires_at <= NOW()
  AND {attempt_field} + 1 >= $4
RETURNING public_id;
