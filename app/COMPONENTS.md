# Component Extension Points

Use these component IDs in implementation tasks.

## API

- `api.create_submission` -> `app/api/handlers/submissions.py`
- `api.get_submission_status` -> `app/api/handlers/status.py`
- `api.list_feedback` -> `app/api/handlers/feedback.py`
- `api.export_results` -> `app/api/handlers/exports.py`

## Workers

- `worker.ingest_telegram.process_claim` -> `app/workers/handlers/ingest_telegram.py`
- `worker.normalize.process_claim` -> `app/workers/handlers/normalize.py`
- `worker.evaluate.process_claim` -> `app/workers/handlers/evaluate.py`
- `worker.deliver.process_claim` -> `app/workers/handlers/deliver.py`

## Domain

- `domain.submission.create` -> `app/domain/use_cases/submissions.py`
- `domain.submission.transition_state` -> `app/domain/use_cases/status.py`
- `domain.artifact.link` -> `app/domain/use_cases/status.py`
- `domain.normalize.payload` -> `app/domain/use_cases/normalize.py`
- `domain.llm.evaluate` -> `app/domain/use_cases/llm_eval.py`
- `domain.feedback.build` -> `app/domain/use_cases/deliver.py`
- `domain.export.prepare` -> `app/domain/use_cases/deliver.py`
