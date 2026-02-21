UPDATE submissions
SET status = $3,
    updated_at = NOW()
WHERE public_id = $1
  AND status = $2
RETURNING public_id;
