import time

import pytest
from fastapi.testclient import TestClient

from app.api.http_app import build_app
from app.domain.contracts import CLAIM_SQL_CONTRACT
from app.domain.models import WorkItemClaim
from app.repositories.stub import InMemoryWorkRepository
from app.roles import SUPPORTED_ROLES, validate_role
from app.services.bootstrap import build_runtime_container
from app.workers.runner import WorkerRuntimeSettings


@pytest.mark.integration
@pytest.mark.parametrize("role_name", SUPPORTED_ROLES)
def test_canonical_roles_report_ready_in_skeleton_mode(role_name: str) -> None:
    role = validate_role(role_name)
    container = build_runtime_container(role)
    app = build_app(role=role.name, run_id="integration", worker_loop=container.worker_loop)

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["role"] == role_name
    assert payload["mode"] == "skeleton"
    assert payload["worker_loop_enabled"] == (role_name != "api")
    assert payload["worker_loop_ready"] is True
    assert "worker_metrics" in payload


@pytest.mark.integration
def test_claim_loop_contract_mentions_skip_locked_semantics() -> None:
    assert "FOR UPDATE SKIP LOCKED" in CLAIM_SQL_CONTRACT


@pytest.mark.integration
def test_worker_background_loop_runs_without_external_side_effects() -> None:
    role = validate_role("worker-deliver")
    container = build_runtime_container(role)
    assert container.worker_loop is not None
    assert isinstance(container.repository, InMemoryWorkRepository)
    repository = container.repository
    repository.queue.append(WorkItemClaim(item_id="job-1", stage="exports", attempt=1))
    repository.queue.append(WorkItemClaim(item_id="job-2", stage="exports", attempt=1))

    app = build_app(
        role=role.name,
        run_id="integration",
        worker_loop=container.worker_loop,
        worker_runtime_settings=WorkerRuntimeSettings(
            poll_interval_ms=5,
            idle_backoff_ms=5,
            error_backoff_ms=5,
        ),
    )
    with TestClient(app) as client:
        time.sleep(0.05)
        response = client.get("/ready")

    assert len(repository.finalizations) >= 2
    payload = response.json()
    assert payload["worker_metrics"]["started"] is True
    assert payload["worker_metrics"]["ticks_total"] >= 2
    assert payload["worker_metrics"]["claims_total"] >= 2
