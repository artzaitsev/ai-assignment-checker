INSERT INTO worker_checkpoints (stream, cursor)
VALUES ($1, $2)
ON CONFLICT (stream)
DO UPDATE SET
    cursor = EXCLUDED.cursor,
    updated_at = NOW();
