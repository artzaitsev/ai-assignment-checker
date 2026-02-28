SELECT a.object_key
FROM artifacts a
JOIN submissions s ON s.id = a.submission_id
WHERE s.public_id = $1
  AND a.stage = $2
ORDER BY a.created_at DESC, a.id DESC
LIMIT 1;
