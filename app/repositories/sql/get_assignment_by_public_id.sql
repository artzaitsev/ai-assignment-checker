SELECT public_id,
       title,
       description,
       language,
       task_schema,
       is_active
FROM assignments
WHERE public_id = $1
LIMIT 1;
