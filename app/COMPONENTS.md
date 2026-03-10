# Точки расширения компонентов для удобной навигации по проекту.

## API

- `api.create_submission` -> `app/api/handlers/submissions.py`
- `api.create_candidate` -> `app/api/handlers/candidates.py`
- `api.create_assignment` -> `app/api/handlers/assignments.py`
- `api.list_assignments` -> `app/api/handlers/assignments.py`
- `api.get_submission_status` -> `app/api/handlers/status.py`
- `api.list_feedback` -> `app/api/handlers/feedback.py`
- `api.export_results` -> `app/api/handlers/exports.py`
- `api.download_export` -> `app/api/http_app.py`

## Воркеры

- `worker.ingest_telegram.process_claim` -> `app/workers/handlers/ingest_telegram.py`
- `worker.normalize.process_claim` -> `app/workers/handlers/normalize.py`
- `worker.evaluate.process_claim` -> `app/workers/handlers/evaluate.py`
- `worker.deliver.process_claim` -> `app/workers/handlers/deliver.py`
 
## Домены

- `domain.submission.create` -> `app/domain/use_cases/submissions.py`
- `domain.submission.transition_state` -> `app/domain/use_cases/status.py`
- `domain.artifact.link` -> `app/domain/use_cases/status.py`
- `domain.normalize.payload` -> `app/domain/use_cases/normalize.py` (plain-text detection, decoding, parser validation, normalized artifact assembly)
- `domain.llm.evaluate` -> `app/domain/use_cases/llm_eval.py`
- `domain.feedback.build` -> `app/domain/use_cases/deliver.py`
- `domain.export.prepare` -> `app/domain/use_cases/deliver.py`
- `domain.telegram.entry_link` -> `app/domain/use_cases/telegram_entry_links.py`

## Ownership matrix (v1 Contract Freeze)

- `track.platform`: bootstrap рантайма, worker loop, repository claim/finalize, SQL-контракты
  - Основные файлы: `app/services/bootstrap.py`, `app/workers/loop.py`, `app/workers/runner.py`, `app/repositories/*`
- `track.ingress`: API-границы входящих отправок и Telegram ingest
  - Основные файлы: `app/api/handlers/submissions.py`, `app/api/http_app.py`, `app/workers/handlers/ingest_telegram.py`
- `track.telegram-entry`: контракты signed-link токена Telegram и onboarding entry
  - Основные файлы: `app/domain/use_cases/telegram_entry_links.py`, `app/workers/handlers/ingest_telegram.py`, `app/services/runtime_settings.py`
- `track.evaluation`: контракт normalized, выполнение chain-spec, evaluate use-case, scoring/reproducibility
  - Основные файлы: `app/domain/evaluation_chain.py`, `app/domain/evaluation_contracts.py`, `app/domain/scoring.py`, `app/domain/use_cases/llm_eval.py`, `app/workers/handlers/evaluate.py`
- `track.normalization`: plain-text normalization contract, parser-backed extraction flow, normalized artifact assembly
  - Основные файлы: `app/domain/use_cases/normalize.py`, `app/workers/handlers/normalize.py`, `app/lib/artifacts/types.py`, `app/lib/artifacts/codecs.py`
- `track.delivery-export`: контракты feedback/export и формирование delivery stage
  - Основные файлы: `app/domain/use_cases/deliver.py`, `app/workers/handlers/deliver.py`, `app/api/handlers/exports.py`
- `track.quality`: acceptance checks и ответственность за test matrix
  - Основные файлы: `tests/unit/*`, `tests/integration/*`, `Makefile`

## Интеграционные швы

- `seam.worker-deps`: `app/workers/handlers/deps.py`
- `seam.api-worker-pipeline`: `app/api/handlers/pipeline.py`
- `seam.error-taxonomy`: `app/domain/error_taxonomy.py`
- `seam.artifact-contracts`: `app/lib/artifacts/types.py`

Кросс-трековые изменения вне основных зон владения должны ссылаться на один из seam-ID в описании PR/чеклисте.
