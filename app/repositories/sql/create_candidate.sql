INSERT INTO candidates (public_id, first_name, last_name)
VALUES ($1, $2, $3)
RETURNING public_id, first_name, last_name;
