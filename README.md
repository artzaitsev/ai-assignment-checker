# AI Assignment Checker

This repository provides a runtime and orchestration baseline:

- Single application entrypoint with role flags
- Empty-mode startup for API and worker roles
- Fail-fast role validation
- Health/readiness endpoints and structured startup logging
- Docker Compose baseline with external one-shot migrator
- Local non-Docker runtime contract using `uv` and `.venv`

## Runtime Roles

- `api`
- `worker-ingest-telegram`
- `worker-normalize`
- `worker-llm`
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
uv run python -m app.main --role worker-llm --dry-run-startup
```

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
