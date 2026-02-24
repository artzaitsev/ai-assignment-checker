from __future__ import annotations

ROLE_TO_STAGE = {
    "worker-ingest-telegram": "raw",
    "worker-normalize": "normalized",
    "worker-evaluate": "llm-output",
    "worker-deliver": "exports",
}
