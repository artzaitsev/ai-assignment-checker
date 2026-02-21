import pytest

from app.domain.contracts import CLAIM_SQL_CONTRACT, STORAGE_PREFIXES
from app.domain.models import WorkItemClaim
from app.repositories.stub import InMemoryWorkRepository
from app.roles import validate_role
from app.services.bootstrap import build_runtime_container
from app.workers.loop import WorkerLoop


@pytest.mark.unit
def test_claim_contract_documents_skip_locked_semantics() -> None:
    assert "FOR UPDATE SKIP LOCKED" in CLAIM_SQL_CONTRACT


@pytest.mark.unit
def test_runtime_container_wires_worker_through_contracts() -> None:
    role = validate_role("worker-normalize")
    container = build_runtime_container(role)

    assert container.worker_loop is not None
    assert isinstance(container.worker_loop, WorkerLoop)


@pytest.mark.unit
def test_worker_loop_claim_process_finalize_lifecycle() -> None:
    role = validate_role("worker-evaluate")
    container = build_runtime_container(role)
    assert container.worker_loop is not None
    assert isinstance(container.repository, InMemoryWorkRepository)
    repository = container.repository

    repository.queue.append(WorkItemClaim(item_id="a1", stage="llm-output", attempt=1))

    did_work = container.worker_loop.run_once()

    assert did_work is True
    assert repository.transitions == [("a1", "llm-output", "llm-output:processing")]
    assert repository.finalizations[0][0] == "a1"


@pytest.mark.unit
def test_storage_stub_enforces_prefix_contract() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)

    ok_key = f"{STORAGE_PREFIXES[0]}submission-1.txt"
    assert container.storage.put_bytes(key=ok_key, payload=b"hello").startswith("stub://")

    with pytest.raises(ValueError):
        container.storage.put_bytes(key="unknown/submission-2.txt", payload=b"hello")
