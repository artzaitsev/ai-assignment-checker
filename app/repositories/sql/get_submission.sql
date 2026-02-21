SELECT s.public_id,
       c.public_id AS candidate_public_id,
       a.public_id AS assignment_public_id,
       status,
       attempt_telegram_ingest,
       attempt_normalization,
       attempt_evaluation,
       attempt_delivery,
       claimed_by,
       claimed_at,
       lease_expires_at,
       last_error_code,
       last_error_message
FROM submissions AS s
JOIN candidates AS c ON c.id = s.candidate_id
JOIN assignments AS a ON a.id = s.assignment_id
WHERE s.public_id = $1;
