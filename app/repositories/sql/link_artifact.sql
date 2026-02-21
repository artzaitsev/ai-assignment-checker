INSERT INTO artifacts (
  submission_id,
  stage,
  bucket,
  object_key,
  schema_version
)
SELECT id, $2, $3, $4, $5
FROM submissions
WHERE public_id = $1;
