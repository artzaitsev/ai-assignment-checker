SELECT c.public_id,
       c.first_name,
       c.last_name
FROM candidate_sources AS cs
JOIN candidates AS c ON c.id = cs.candidate_id
WHERE cs.source_type = $1
  AND cs.source_external_id = $2;
