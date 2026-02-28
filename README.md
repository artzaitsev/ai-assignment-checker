# AI Assignment Checker

This repository provides a runtime and orchestration baseline:

- Single application entrypoint with role flags
- Skeleton-mode startup for API and worker roles
- Fail-fast role validation
- Health/readiness endpoints and structured startup logging
- Docker Compose baseline with external one-shot migrator
- Local non-Docker runtime contract using `uv` and `.venv`
- Layered skeleton with interface-first repository/client contracts
- Persistence-backed onboarding contracts for candidates and assignments
- OpenAPI/Swagger request/response contracts with typed Pydantic schemas

## Layered Architecture

- `app/api`: transport and readiness endpoints
- `app/services`: dependency wiring/bootstrap
- `app/workers`: claim/process/finalize skeleton loop
- `app/domain`: shared contracts and models
- `app/repositories`: asyncpg Postgres adapter + in-memory deterministic stub
- `app/clients`: external client stubs

Dependency direction: `api/services/workers -> domain contracts`, with infrastructure wired at bootstrap. See `app/ARCHITECTURE.md`.
Component extension points for future business logic are listed in `app/COMPONENTS.md`.

Worker roles run a background polling loop in skeleton mode. Each tick executes claim -> process -> finalize using no-op-safe handlers and stubbed dependencies (no real external calls yet).

Readiness now includes worker-loop status and counters via `/ready`:

- `worker_loop_enabled`
- `worker_loop_ready`
- `worker_metrics` (`ticks_total`, `claims_total`, `idle_ticks_total`, `errors_total`)

Synthetic in-process pipeline checks are available for infrastructure verification:

- `POST /submissions/file` accepts a file plus `candidate_public_id` and `assignment_public_id`, then stores a `raw/` artifact.
- `POST /internal/test/run-pipeline` runs normalize -> evaluate -> deliver handler chain for the submission.
- `GET /submissions/{id}` returns current state, transitions, and artifact refs for test submissions.

Quick local usage:

1. Create candidate and assignment, then upload a file via `POST /submissions/file`.
2. Run synthetic pipeline via `POST /internal/test/run-pipeline`.
3. Read resulting trace via `GET /submissions/{submission_id}`.

Example `curl` for step 2:

```bash
curl -sS -X POST "http://localhost:8000/internal/test/run-pipeline" \
  -H "Content-Type: application/json" \
  -d '{"submission_id":"sub_01ABCDEF0123456789ABCDEF01"}'
```

Expected states:

- happy path ends with `delivered`
- fail-fast path ends with one of `failed_normalization`, `failed_evaluation`, `failed_delivery`

Artifact refs are internal storage references (`s3://...` in skeleton mode), not public download URLs.

Current API onboarding flow:

- `POST /candidates` creates or reuses candidate identity mapping.
- `POST /assignments` creates assignment metadata.
- `GET /assignments` lists assignments (active by default).
- `POST /submissions` requires `candidate_public_id` and `assignment_public_id`.
- `POST /webhooks/telegram` stores Telegram intake updates idempotently by `update_id`.

Public ID contracts:

- candidate: `cand_<ULID>`
- assignment: `asg_<ULID>`
- submission: `sub_<ULID>`

Internal database primary keys remain `BIGSERIAL`; public IDs are external API-facing identifiers.

Swagger/OpenAPI is available from FastAPI defaults (`/docs`, `/openapi.json`) and is treated as the HTTP contract source.

Note: this synthetic flow is intentionally in-process and does not model shared persistence across separate Compose services yet.

Dead-letter processing is not implemented yet; `dead_letter` currently acts as a terminal persistence state.

## Runtime Roles

- `api`
- `worker-ingest-telegram`
- `worker-normalize`
- `worker-evaluate`
- `worker-deliver`

`migrator` is external and not an app role.

## Local Setup (uv + .venv)

```bash
uv venv .venv
uv sync --all-groups
```

Start a role in empty mode:

```bash
uv run python -m app.main --role api
```

Dry-run startup validation:

```bash
uv run python -m app.main --role worker-evaluate --dry-run-startup
```

Worker polling tuning (optional):

- `WORKER_POLL_INTERVAL_MS` (default: `200`)
- `WORKER_IDLE_BACKOFF_MS` (default: `1000`)
- `WORKER_ERROR_BACKOFF_MS` (default: `2000`)
- `WORKER_CLAIM_LEASE_SECONDS` (default: `30`)
- `WORKER_HEARTBEAT_INTERVAL_MS` (default: `10000`)

Run local role smoke checks:

```bash
make smoke-local
```

## Docker Compose Startup Contract

The compose dependency path is:

`postgres -> migrator -> app services`

Start prod-like stack:

```bash
docker compose -f docker-compose.yml up --build
```

Start fast local dev mode (default compose + override):

```bash
docker compose up --build
```

This starts `postgres`, `migrator`, and `api` by default.

Start full local dev mode with workers enabled via profile:

```bash
docker compose --profile full up --build
```

Notes:
- `docker-compose.yml` stays prod-like (no local source mount).
- `docker-compose.override.yml` is the dev layer (mounts + dev commands).

## Tests

- all tests: `make test`
- unit-only: `make test-unit`
- integration-only: `make test-integration`
- type checking: `make typecheck`

Postgres-backed integration tests run when DB is reachable via `TEST_DATABASE_URL` (or `DATABASE_URL`); otherwise they are skipped.
CI should provide reachable Postgres and treat Postgres-backed integration coverage as required.
