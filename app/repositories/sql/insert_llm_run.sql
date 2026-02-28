INSERT INTO llm_runs (
  submission_id,
  provider,
  model,
  api_base,
  chain_version,
  spec_version,
  response_language,
  temperature,
  seed,
  tokens_input,
  tokens_output,
  latency_ms
)
SELECT id, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
FROM submissions
WHERE public_id = $1;
