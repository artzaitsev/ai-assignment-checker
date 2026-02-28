# Architecture Layer Boundaries

This document explains how the runtime is organized, where business logic should live, and where to make changes safely.

## 1) Runtime Overview

Entrypoint: `app/main.py`

- The process starts with `--role`.
- Supported roles:
  - `api`
  - `worker-ingest-telegram`
  - `worker-normalize`
  - `worker-evaluate`
  - `worker-deliver`
- `migrator` is external (one-shot service) and not an app role.

`app/main.py` validates role, builds dependencies via `app/services/bootstrap.py`, and starts FastAPI app from `app/api/http_app.py`.

## 2) Layers and Responsibilities

- `app/api`
  - HTTP transport only (routes, request/response mapping, readiness/health).
  - Calls handlers in `app/api/handlers`.
  - Should not contain core business rules.

- `app/services`
  - Dependency wiring/bootstrap.
  - Builds runtime container and injects contracts/adapters.

- `app/lib`
  - Infrastructure support libraries shared by adapters/handlers.
  - `app/lib/artifacts/*` owns artifact serialization, version checks, and storage mapping.

- `app/workers`
  - Worker orchestration:
    - polling runner
    - claim/process/finalize loop
    - role-to-handler mapping
  - Stage-specific process handlers live in `app/workers/handlers`.

- `app/domain`
  - Core contracts, DTOs, models, use-cases.
  - Main place for production business logic.
  - Artifact I/O is accessed via `ArtifactRepository` contract.
  - Must not import transport/framework/SDK-specific modules.

- `app/repositories`
  - Persistence adapters implementing domain repository contracts.
  - Includes asyncpg Postgres adapter and in-memory deterministic stub.

- `app/clients`
  - External integration adapters (storage, Telegram, LLM).
  - Currently non-network stubs.

## 3) Dependency Direction (Strict Rule)

Direction must remain:

`api/services/workers -> domain contracts`

And:

- `repositories` and `clients` implement domain contracts.
- `domain` must not depend on `api`, `workers`, FastAPI, Uvicorn, provider SDKs, or DB drivers.
- `workers/services` should use interfaces, not concrete infrastructure imports in business code.
- Artifact contract compatibility policy defaults to `strict` (`ARTIFACT_COMPAT_POLICY=strict`).

## 4) Request and Worker Flows

### API flow (role: `api`)

- Routes are defined in `app/api/http_app.py`.
- Routes call handler functions from `app/api/handlers/*`.
- Handlers call domain use-cases (or stubs for now).
- Responses are mapped back to HTTP JSON.
- Export retrieval is API-mediated (`download_url` -> `/exports/{export_id}/download`) so storage can remain private.

Current key routes:

- `GET /health`
- `GET /ready`
- `POST /candidates`
- `POST /assignments`
- `GET /assignments`
- `POST /submissions`
- `POST /webhooks/telegram`
- `GET /submissions/{submission_id}`
- `POST /submissions/file` (synthetic infra check)
- `GET /feedback`
- `POST /exports`
- `GET /exports/{export_id}/download`
- `POST /internal/test/run-pipeline` (synthetic infra check)

### Worker flow (worker-* roles)

- `app/workers/runner.py` runs background polling loop.
- Each tick calls `WorkerLoop.run_once()` from `app/workers/loop.py`.
- `run_once()` lifecycle:
  1. `claim_next(...)`
  2. `process(claim)` (role handler)
  3. optional `link_artifact(...)`
  4. `finalize(...)`

Why each step exists:

- `claim_next(...)`
  - Select one eligible work item for the current stage.
  - Prevent two workers from processing the same item at the same time.

- `process(claim)`
  - Execute stage-specific business behavior (normalize/evaluate/deliver/etc).
  - Returns `ProcessResult` with success flag, detail, and optional artifact metadata.
  - Evaluate stage resolves chain spec from `app/eval/chains/chain.v1.yaml` and uses domain chain execution (`app/domain/evaluation_chain.py`).

- `link_artifact(...)` (optional)
  - Persist artifact reference/version when stage produced output.
  - Optional because some stages may only change state and not create artifacts.
  - Artifact trace keys are documented in `app/domain/artifacts.py`.
  - Keys map 1:1 to persisted artifact-producing stages.

- `finalize(...)`
  - Record final outcome of the stage attempt (success/failure + detail).
  - This is the terminal step for a single claim attempt and is used by retries/next-stage logic.

Lease behavior for long-running handlers:

- Runtime periodically calls `reclaim_expired_claims(stage=...)` before each worker tick.
- `WorkerLoop` starts a heartbeat while `process(claim)` runs and extends claim lease via `heartbeat_claim(...)`.
- If lease ownership is lost mid-process, the tick fails with stale-claim error and does not commit success.

Dead-letter handling (current limitation):

- `dead_letter` is currently a terminal persistence state only.
- Runtime transitions work items to `dead_letter` after max-attempt failures or expired-lease reclaim.
- There is currently no dedicated dead-letter processing flow (triage/requeue/alerting API or worker).
- Production rollout should include operational dead-letter handling before relying on retries-only behavior.

Execution order matters:

- Claim first, then process, then finalize.
- If `process` fails, `finalize` still records failure context (via exception path in higher-level runner/retry policy).
- Keep `process` deterministic and idempotent where possible; retries may re-run the same logical task.

Sequence sketch:

```text
runner tick
   |
   v
WorkerLoop.run_once()
   |
   +--> repository.claim_next(stage, worker_id)
   |      -> WorkItemClaim | None
   |
   +--> role_handler.process(claim)
   |      -> ProcessResult(success, detail, artifact_ref?, artifact_version?)
   |
   +--> [if artifact_ref] repository.link_artifact(...)
   |
   +--> repository.finalize(item, stage, success, detail)
   |
   +--> return did_work=True

If claim_next returns None -> return did_work=False (idle/backoff path in runner).
```

Role handler mapping is in `app/workers/handlers/factory.py`.

## 5) Skeleton Mode (Current State)

- Adapters are stubs, no real network calls.
- Worker roles run continuous background polling loop.
- `/ready` includes worker runtime state and counters:
  - `worker_loop_enabled`
  - `worker_loop_ready`
  - `worker_metrics`:
    - `ticks_total`
    - `claims_total`
    - `idle_ticks_total`
    - `errors_total`

Runner tuning via env:

- `WORKER_POLL_INTERVAL_MS` (default `200`)
- `WORKER_IDLE_BACKOFF_MS` (default `1000`)
- `WORKER_ERROR_BACKOFF_MS` (default `2000`)

## 6) Where to Implement New Logic

Use `app/COMPONENTS.md` as source of truth for component IDs and file targets.

### If you add a new API business feature

1. Add/adjust route in `app/api/http_app.py`.
2. Implement handler in `app/api/handlers/*`.
3. Put real business rules into `app/domain/use_cases/*`.
4. Inject dependencies through `app/services/bootstrap.py`.
5. Add unit tests and integration tests.

### If you add a new worker stage behavior

1. Implement stage logic in `app/workers/handlers/<stage>.py`.
2. Keep orchestration generic in `app/workers/loop.py`.
3. Keep polling behavior in `app/workers/runner.py`.
4. Add domain logic in `app/domain/use_cases/*`.
5. Wire new dependencies through `app/services/bootstrap.py`.

### If you add real external integration

1. Define/extend contract in `app/domain/contracts.py`.
2. Implement adapter in `app/clients/*` or `app/repositories/*`.
3. Wire implementation in `app/services/bootstrap.py`.
4. Do not call SDK/DB directly from domain use-cases.

## 7) PR Readiness Checklist

Use this checklist before opening a PR:

- [ ] Business logic lives in `app/domain/use_cases/*`, not in route/transport code.
- [ ] Dependency direction remains valid (`api/services/workers -> domain contracts`).
- [ ] `COMPONENT_ID` values and mappings in `app/COMPONENTS.md` are updated if behavior changed.
- [ ] Unit tests cover logic/contracts changes.
- [ ] Integration tests cover runtime/wiring path changes.
- [ ] If a new role was added, role/stage/handler/compose/test matrices are all updated.
- [ ] `make test`, `make test-unit`, `make test-integration`, and `make typecheck` pass.
- [ ] Cross-track edits outside primary ownership scope reference an explicit seam contract from `app/COMPONENTS.md` (`seam.*`).

## 7.1) Parallel Ownership Boundaries

The contract-freeze workflow uses five primary ownership tracks to reduce PR conflicts:

- Platform: runtime/bootstrap/repository claim semantics
- Ingress: API upload and Telegram intake boundaries
- Evaluation: normalized + llm-output contracts and deterministic scoring
- Delivery/Export: feedback payloads and organizer export shaping
- Quality: test matrix and acceptance checks

When a change must touch another track's primary files, keep edits limited to documented seam files and call out the seam contract in the PR checklist.

## 8) Synthetic End-to-End Note

`POST /submissions/file` and `POST /internal/test/run-pipeline` are for infrastructure verification only.

They validate that routing, handlers, wiring, and stage sequencing work together in-process, without requiring real persistence/integrations.

### 8.1) Quick Usage

Recommended local verification sequence:

1. Create candidate and assignment via API routes.
2. Upload a file with `POST /submissions/file`.
3. Execute synthetic chain with `POST /internal/test/run-pipeline`.
4. Inspect final state/trace with `GET /submissions/{submission_id}`.

Minimal pipeline trigger example:

```bash
curl -sS -X POST "http://localhost:8000/internal/test/run-pipeline" \
  -H "Content-Type: application/json" \
  -d '{"submission_id":"sub_01ABCDEF0123456789ABCDEF01"}'
```

State semantics:

- success path: `uploaded -> normalization_in_progress -> normalized -> evaluation_in_progress -> evaluated -> delivery_in_progress -> delivered`
- fail-fast path: pipeline stops at first failed stage and returns `failed_*`

### 8.2 Deferred Post-MVP Hardening

- Submission-level evaluation snapshot (`chain_digest` plus resolved chain spec) is deferred for post-MVP.
- Current evaluate flow resolves the default chain spec at runtime from `app/eval/chains/chain.v1.yaml`.
- Chain mismatch policy after snapshot rollout: start with `warn-only` diagnostics on digest mismatch, then promote to `strict-fail` once persistence is stable.

## 9) Common Mistakes

### 9.1 Business logic in routes instead of domain use-cases

Do:

```python
@app.post("/submissions")
async def create_submission(payload: CreateSubmissionRequest):
    return await create_submission_with_candidate_handler(
        api_deps,
        source_external_id=payload.source_external_id,
        candidate_public_id=payload.candidate_public_id,
        assignment_public_id=payload.assignment_public_id,
    )
```

Don't:

```python
@app.post("/submissions")
async def create_submission(payload: dict[str, str] = Body(default={})):
    # business rule directly in transport layer
    score = int(payload["score"])
    if score > 7:
        return {"result": "pass"}
    return {"result": "fail"}
```

### 9.2 Domain depending on concrete adapters

Do:

```python
def evaluate_submission(cmd: EvaluateSubmissionCommand, *, llm: LLMClient) -> EvaluateSubmissionResult:
    ...
```

Don't:

```python
from app.clients.stub import StubLLMClient

def evaluate_submission(cmd: EvaluateSubmissionCommand) -> EvaluateSubmissionResult:
    llm = StubLLMClient()
    ...
```

### 9.3 Stage-specific behavior in generic worker loop

Do:

```python
def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    # worker.evaluate.process_claim
    ...
```

Don't:

```python
def run_once(self) -> bool:
    ...
    if self.role == "worker-evaluate":
        # stage-specific logic in generic orchestration
        ...
```

### 9.4 Transport-specific types leaked into domain

Do:

```python
@dataclass(frozen=True)
class PrepareExportResult:
    export_rows: list[ExportRowArtifact]
```

Don't:

```python
from fastapi.responses import JSONResponse

def prepare_export(...) -> JSONResponse:
    return JSONResponse({"ok": True})
```

### 9.5 Missing component map and tests after changes

Do:

```text
1. Update app/COMPONENTS.md with new/changed component IDs.
2. Add/adjust unit + integration tests.
```

Don't:

```text
Ship new behavior without updating component map or tests.
```

## 10) Implementation Playbook

Use this flow for new behavior:

1. Scope the change
   - Select a component from `app/COMPONENTS.md` (for example `worker.evaluate.process_claim`).
   - Confirm target file and contracts before coding.
2. Implement in the right layer
   - Keep domain rules in `app/domain/use_cases/*`.
   - Keep API transport in `app/api/*` and worker orchestration in `app/workers/loop.py` or `app/workers/runner.py`.
3. Wire dependencies
   - Add/adjust contracts first (`app/domain/contracts.py`) if needed.
   - Wire adapters in `app/services/bootstrap.py`.
4. Verify
   - Add unit tests for business logic.
   - Add integration tests for runtime path and wiring.
   - Run `make test-unit`, `make test-integration`, and `make typecheck`.
5. Finalize
   - Update `app/COMPONENTS.md` when component boundaries or IDs change.
   - Update `README.md` only if public runtime behavior changes.

### Terminology: what "matrices" means here

In this document, a matrix means a role registration map/list that must stay consistent across files.

- `role matrix`: canonical role list in `app/roles.py` and user-facing role docs.
- `stage matrix`: role -> stage mapping in `app/workers/roles.py`.
- `handler matrix`: role -> handler mapping in `app/workers/handlers/factory.py`.
- `compose matrix`: service -> role command mapping in `docker-compose.yml` and `docker-compose.override.yml`.
- `test matrix`: role/service coverage lists in integration tests.

### 10.1 When adding a new worker role

Use this checklist to avoid partial registration:

1. Define role and stage mapping
   - Add role to `app/roles.py` (`SUPPORTED_ROLES`).
   - Add stage mapping to `app/workers/roles.py` (`ROLE_TO_STAGE`).
2. Implement and register handler
   - Add handler module in `app/workers/handlers/<role_or_stage>.py`.
   - Register the role in `app/workers/handlers/factory.py` (`build_process_handler`).
3. Wire runtime services
   - Add service entries in `docker-compose.yml` and `docker-compose.override.yml`.
   - Keep role command shape consistent: `python -m app.main --role <role-name>`.
4. Update test/service matrices
   - Update `tests/integration/test_runtime_smoke.py` role list.
   - Update `tests/integration/test_compose_contract.py` service matrix checks.
5. Update component map and docs
   - Add/adjust worker component IDs in `app/COMPONENTS.md`.
   - Update `README.md` if user-facing runtime role list changes.
