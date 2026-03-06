import asyncio

import pytest

from app.domain.contracts import CLAIM_SQL_CONTRACT, STORAGE_PREFIXES
from app.repositories.postgres import PostgresWorkRepository
from app.repositories.stub import InMemoryWorkRepository
from app.roles import validate_role
from app.services.bootstrap import build_runtime_container
from app.workers.loop import WorkerLoop


def _clear_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "INTEGRATION_MODE",
        "DATABASE_URL",
        "RUNTIME_VALIDATION_MODE",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.unit
def test_claim_contract_documents_skip_locked_semantics() -> None:
    assert "FOR UPDATE SKIP LOCKED" in CLAIM_SQL_CONTRACT


@pytest.mark.unit
def test_runtime_container_wires_worker_through_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("INTEGRATION_MODE", "stub")
    role = validate_role("worker-normalize")
    container = build_runtime_container(role)

    assert container.worker_loop is not None
    assert isinstance(container.worker_loop, WorkerLoop)


@pytest.mark.unit
def test_worker_loop_claim_process_finalize_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("INTEGRATION_MODE", "stub")
    role = validate_role("worker-evaluate")
    container = build_runtime_container(role)
    assert container.worker_loop is not None
    assert isinstance(container.repository, InMemoryWorkRepository)
    repository = container.repository

    asyncio.run(
        _seed_and_create_submission(repository=repository, source_external_id="a1", initial_status="normalized")
    )

    did_work = asyncio.run(container.worker_loop.run_once())

    assert did_work is True
    assert repository.finalizations
    assert repository.transitions[0][2] == "evaluation_in_progress"


async def _seed_and_create_submission(
    *,
    repository: InMemoryWorkRepository,
    source_external_id: str,
    initial_status: str,
) -> None:
    candidate = await repository.create_candidate(first_name="Test", last_name="Candidate")
    assignment = await repository.create_assignment(title="Task", description="desc")
    await repository.create_submission_with_source(
        candidate_public_id=candidate.candidate_public_id,
        assignment_public_id=assignment.assignment_public_id,
        source_type="api_upload",
        source_external_id=source_external_id,
        initial_status=initial_status,
    )


@pytest.mark.unit
def test_storage_stub_enforces_prefix_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("INTEGRATION_MODE", "stub")
    role = validate_role("api")
    container = build_runtime_container(role)

    ok_key = f"{STORAGE_PREFIXES[0]}submission-1.txt"
    assert container.storage.put_bytes(key=ok_key, payload=b"hello").startswith("s3://")

    with pytest.raises(ValueError):
        container.storage.put_bytes(key="unknown/submission-2.txt", payload=b"hello")


@pytest.mark.unit
def test_stub_mode_uses_in_memory_repository_even_with_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("INTEGRATION_MODE", "stub")
    monkeypatch.setenv("DATABASE_URL", "postgres://app:app@localhost:5432/app")

    role = validate_role("api")
    container = build_runtime_container(role)

    assert isinstance(container.repository, InMemoryWorkRepository)


@pytest.mark.unit
def test_real_mode_uses_postgres_repository_when_database_url_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("INTEGRATION_MODE", "real")
    monkeypatch.setenv("DATABASE_URL", "postgres://app:app@localhost:5432/app")

    role = validate_role("api")
    container = build_runtime_container(role)

    assert isinstance(container.repository, PostgresWorkRepository)


@pytest.mark.unit
def test_real_mode_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("INTEGRATION_MODE", "real")

    role = validate_role("api")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        build_runtime_container(role)
