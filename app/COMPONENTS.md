# Component Extension Points

Use these component IDs in implementation tasks.

## API

- `api.create_submission` -> `app/api/handlers/submissions.py`
- `api.create_candidate` -> `app/api/handlers/candidates.py`
- `api.create_assignment` -> `app/api/handlers/assignments.py`
- `api.list_assignments` -> `app/api/handlers/assignments.py`
- `api.telegram_webhook` -> `app/api/handlers/telegram_webhook.py`
- `api.get_submission_status` -> `app/api/handlers/status.py`
- `api.list_feedback` -> `app/api/handlers/feedback.py`
- `api.export_results` -> `app/api/handlers/exports.py`
- `api.download_export` -> `app/api/http_app.py`

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

## Ownership Matrix (v1 Contract Freeze)

- `track.platform`: runtime bootstrap, worker loop, repository claim/finalize, SQL contracts
  - Primary files: `app/services/bootstrap.py`, `app/workers/loop.py`, `app/workers/runner.py`, `app/repositories/*`
- `track.ingress`: API submission ingress and Telegram ingest boundaries
  - Primary files: `app/api/handlers/submissions.py`, `app/api/http_app.py`, `app/workers/handlers/ingest_telegram.py`
- `track.evaluation`: normalized contract, chain-spec execution, evaluate use-case, scoring/reproducibility
  - Primary files: `app/domain/evaluation_chain.py`, `app/domain/scoring.py`, `app/domain/use_cases/llm_eval.py`, `app/workers/handlers/evaluate.py`
- `track.delivery-export`: feedback/export contracts and delivery stage shaping
  - Primary files: `app/domain/use_cases/deliver.py`, `app/workers/handlers/deliver.py`, `app/api/handlers/exports.py`
- `track.quality`: acceptance checks and test matrix ownership
  - Primary files: `tests/unit/*`, `tests/integration/*`, `Makefile`

## Integration Seams

- `seam.worker-deps`: `app/workers/handlers/deps.py`
- `seam.api-worker-pipeline`: `app/api/handlers/pipeline.py`
- `seam.error-taxonomy`: `app/domain/error_taxonomy.py`
- `seam.artifact-contracts`: `app/lib/artifacts/types.py`

Cross-track edits outside primary ownership should reference one of the seam IDs above in PR description/checklist.
