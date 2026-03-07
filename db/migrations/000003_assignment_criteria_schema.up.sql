ALTER TABLE assignments
ADD COLUMN IF NOT EXISTS criteria_schema_json JSONB;
