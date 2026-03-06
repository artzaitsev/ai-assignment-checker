import asyncio

import pytest

from app.clients.stub import StubStorageClient
from app.domain.contracts import CLAIM_SQL_CONTRACT, STORAGE_PREFIXES
from app.repositories.postgres import PostgresWorkRepository
from app.repositories.stub import InMemoryWorkRepository
from app.roles import validate_role
from app.services import bootstrap
from app.services.bootstrap import build_runtime_container
from app.workers.loop import WorkerLoop


def _clear_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "INTEGRATION_MODE",
        "DATABASE_URL",
        "RUNTIME_VALIDATION_MODE",
        "S3_ENDPOINT_URL",
        "S3_BUCKET",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_REGION",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_s3_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")
    monkeypatch.setenv("S3_BUCKET", "artifacts")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("S3_REGION", "us-east-1")


@pytest.mark.unit
def test_claim_contract_documents_skip_locked_semantics() -> None:
    assert "FOR UPDATE SKIP LOCKED" in CLAIM_SQL_CONTRACT


@pytest.mark.unit
def test_runtime_container_wires_worker_through_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    role = validate_role("worker-normalize")
    container = build_runtime_container(role, integration_mode="stub")

    assert container.worker_loop is not None
    assert isinstance(container.worker_loop, WorkerLoop)


@pytest.mark.unit
def test_worker_loop_claim_process_finalize_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    role = validate_role("worker-evaluate")
    container = build_runtime_container(role, integration_mode="stub")
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
    role = validate_role("api")
    container = build_runtime_container(role, integration_mode="stub")

    ok_key = f"{STORAGE_PREFIXES[0]}submission-1.txt"
    assert container.storage.put_bytes(key=ok_key, payload=b"hello").startswith("s3://")

    with pytest.raises(ValueError):
        container.storage.put_bytes(key="unknown/submission-2.txt", payload=b"hello")


@pytest.mark.unit
def test_stub_mode_uses_in_memory_repository_even_with_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgres://app:app@localhost:5432/app")

    role = validate_role("api")
    container = build_runtime_container(role, integration_mode="stub")

    assert isinstance(container.repository, InMemoryWorkRepository)
    assert isinstance(container.storage, StubStorageClient)


@pytest.mark.unit
def test_real_mode_uses_postgres_repository_when_database_url_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgres://app:app@localhost:5432/app")
    _set_s3_env(monkeypatch)

    role = validate_role("api")
    container = build_runtime_container(role, integration_mode="real")

    assert isinstance(container.repository, PostgresWorkRepository)


@pytest.mark.unit
def test_real_mode_wires_s3_storage_into_artifact_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgres://app:app@localhost:5432/app")
    _set_s3_env(monkeypatch)

    class FakeS3StorageClient:
        def put_bytes(self, *, key: str, payload: bytes) -> str:
            return f"s3://artifacts/{key}"

        def get_bytes(self, *, key: str) -> bytes:
            return b""

    fake_storage = FakeS3StorageClient()
    monkeypatch.setattr(bootstrap, "build_s3_storage_client", lambda **_: fake_storage)

    role = validate_role("api")
    container = build_runtime_container(role, integration_mode="real")

    assert container.storage is fake_storage
    assert getattr(container.artifact_repository, "storage", None) is fake_storage


@pytest.mark.unit
def test_stub_mode_stays_stubbed_even_when_s3_env_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgres://app:app@localhost:5432/app")
    _set_s3_env(monkeypatch)

    role = validate_role("api")
    container = build_runtime_container(role, integration_mode="stub")

    assert isinstance(container.storage, StubStorageClient)
    assert isinstance(container.repository, InMemoryWorkRepository)


@pytest.mark.unit
def test_real_mode_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_mode_env(monkeypatch)

    role = validate_role("api")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        build_runtime_container(role, integration_mode="real")
