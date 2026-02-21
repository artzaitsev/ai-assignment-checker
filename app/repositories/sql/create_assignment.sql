INSERT INTO assignments (public_id, title, description, is_active)
VALUES ($1, $2, $3, $4)
RETURNING public_id, title, description, is_active;
