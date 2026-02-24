UPDATE submissions
SET lease_expires_at = NOW() + make_interval(secs => $4::int),
    updated_at = NOW()
WHERE public_id = $1
  AND status = $2
  AND claimed_by = $3
  AND lease_expires_at > NOW()
RETURNING public_id;
