SELECT a.stage,
       a.bucket,
       a.object_key
FROM artifacts AS a
JOIN submissions AS s ON s.id = a.submission_id
WHERE s.public_id = $1
ORDER BY a.id ASC;
