SELECT public_id,
       title,
       description,
       language,
       task_schema,
       is_active
FROM assignments
WHERE ($1::bool IS FALSE) OR (is_active IS TRUE)
ORDER BY created_at;
