# AI Assignment Checker

This repository provides a runtime and orchestration baseline:

- Single application entrypoint with role flags
- Skeleton-mode startup for API and worker roles
- Fail-fast role validation
- Health/readiness endpoints and structured startup logging
- Docker Compose baseline with external one-shot migrator
- Local non-Docker runtime contract using `uv` and `.venv`
- Layered skeleton with interface-first repository/client contracts

## Layered Architecture

- `app/api`: transport and readiness endpoints
- `app/services`: dependency wiring/bootstrap
- `app/workers`: claim/process/finalize skeleton loop
- `app/domain`: shared contracts and models
- `app/repositories`: repository adapter stubs
- `app/clients`: external client stubs

Dependency direction: `api/services/workers -> domain contracts`, with infrastructure wired at bootstrap. See `app/ARCHITECTURE.md`.
Component extension points for future business logic are listed in `app/COMPONENTS.md`.

Worker roles run a background polling loop in skeleton mode. Each tick executes claim -> process -> finalize using no-op-safe handlers and stubbed dependencies (no real external calls yet).

Readiness now includes worker-loop status and counters via `/ready`:

- `worker_loop_enabled`
- `worker_loop_ready`
- `worker_metrics` (`ticks_total`, `claims_total`, `idle_ticks_total`, `errors_total`)

Synthetic in-process pipeline checks are available for infrastructure verification:

- `POST /submissions/file` accepts a file and stores a `raw/` artifact via stubs.
- `POST /internal/test/run-pipeline` runs normalize -> evaluate -> deliver handler chain for the submission.
- `GET /submissions/{id}` returns current state, transitions, and artifact refs for test submissions.

Note: this synthetic flow is intentionally in-process and does not model shared persistence across separate Compose services yet.

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
