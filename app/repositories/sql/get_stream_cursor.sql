SELECT wc.cursor
FROM worker_checkpoints AS wc
WHERE wc.stream = $1;
