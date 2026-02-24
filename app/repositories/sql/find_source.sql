SELECT sub.public_id AS submission_id,
       s.source_type,
       s.source_external_id,
       s.metadata_json
FROM submission_sources AS s
JOIN submissions AS sub ON sub.id = s.submission_id
WHERE s.source_type = $1
  AND s.source_external_id = $2;
