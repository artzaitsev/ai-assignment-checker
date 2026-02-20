#!/usr/bin/env bash
set -euo pipefail

roles=(
  api
  worker-ingest-telegram
  worker-normalize
  worker-llm
  worker-deliver
)

for role in "${roles[@]}"; do
  echo "Checking role: ${role}"
  uv run python -m app.main --role "${role}" --dry-run-startup
done

echo "All roles start in empty mode via uv."
