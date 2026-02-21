INSERT INTO submissions (public_id, candidate_id, assignment_id, status)
SELECT $1,
       c.id,
       a.id,
       $4
FROM candidates AS c
JOIN assignments AS a ON a.public_id = $3
WHERE c.public_id = $2
RETURNING id;
