SELECT public_id,
       title,
       description,
       criteria_schema_json,
       is_active
FROM assignments
WHERE ($1::bool IS FALSE) OR (is_active IS TRUE)
ORDER BY created_at;
