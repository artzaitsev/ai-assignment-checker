INSERT INTO deliveries (
  submission_id,
  channel,
  status,
  external_message_id,
  attempts,
  last_error_code
)
SELECT id, $2, $3, $4, $5, $6
FROM submissions
WHERE public_id = $1;
