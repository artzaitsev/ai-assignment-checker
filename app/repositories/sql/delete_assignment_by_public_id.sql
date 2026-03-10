DELETE FROM assignments
WHERE public_id = $1
RETURNING public_id;
