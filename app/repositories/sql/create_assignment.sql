INSERT INTO assignments (public_id, title, description, criteria_schema_json, is_active)
VALUES ($1, $2, $3, $4, $5)
RETURNING public_id, title, description, criteria_schema_json, is_active;
