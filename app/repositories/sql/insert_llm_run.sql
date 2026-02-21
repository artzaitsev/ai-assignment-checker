INSERT INTO llm_runs (
  submission_id,
  provider,
  model,
  api_base,
  prompt_version,
  chain_version,
  rubric_version,
  result_schema_version,
  temperature,
  seed,
  tokens_input,
  tokens_output,
  latency_ms
)
SELECT id, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
FROM submissions
WHERE public_id = $1;
