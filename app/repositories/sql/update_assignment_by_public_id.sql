UPDATE assignments
SET title = $2,
    description = $3,
    language = $4,
    task_schema = $5,
    is_active = $6,
    updated_at = NOW()
WHERE public_id = $1
RETURNING public_id,
          title,
          description,
          language,
          task_schema,
          is_active;
