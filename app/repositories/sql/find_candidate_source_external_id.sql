SELECT cs.source_external_id
FROM candidate_sources AS cs
JOIN candidates AS c ON c.id = cs.candidate_id
WHERE c.public_id = $1
  AND cs.source_type = $2
ORDER BY cs.created_at DESC
LIMIT 1;
