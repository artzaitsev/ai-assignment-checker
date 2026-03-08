INSERT INTO assignments (public_id, title, description, language, task_schema, is_active)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING public_id, title, description, language, task_schema, is_active;
