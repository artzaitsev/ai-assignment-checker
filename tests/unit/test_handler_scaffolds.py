import asyncio

import pytest

from app.api.handlers import assignments, candidates, exports, feedback, status, submissions
from app.api.handlers.deps import ApiDeps
from app.api.handlers.pipeline import run_test_pipeline_handler
from app.clients.stub import StubLLMClient, StubStorageClient, StubTelegramClient
from app.domain.models import WorkItemClaim
from app.repositories.stub import InMemoryWorkRepository
from app.workers.handlers import deliver, evaluate, ingest_telegram, normalize
from app.workers.handlers.deps import WorkerDeps


@pytest.mark.unit
def test_api_handler_component_ids_are_stable() -> None:
    assert submissions.COMPONENT_ID == "api.create_submission"
    assert status.COMPONENT_ID == "api.get_submission_status"
    assert feedback.COMPONENT_ID == "api.list_feedback"
    assert exports.COMPONENT_ID == "api.export_results"
    assert candidates.COMPONENT_ID == "api.create_candidate"
    assert assignments.COMPONENT_ID_CREATE == "api.create_assignment"
    assert assignments.COMPONENT_ID_LIST == "api.list_assignments"


@pytest.mark.unit
def test_worker_handler_component_ids_are_stable() -> None:
    assert ingest_telegram.COMPONENT_ID == "worker.ingest_telegram.process_claim"
    assert normalize.COMPONENT_ID == "worker.normalize.process_claim"
    assert evaluate.COMPONENT_ID == "worker.evaluate.process_claim"
    assert deliver.COMPONENT_ID == "worker.deliver.process_claim"


@pytest.mark.unit
def test_handlers_execute_skeleton_flow() -> None:
    storage = StubStorageClient()
    deps = WorkerDeps(storage=storage, telegram=StubTelegramClient(), llm=StubLLMClient())
    claim = WorkItemClaim(item_id="s-1", stage="llm-output", attempt=1)

    async def _run() -> None:
        evaluate_result = await evaluate.process_claim(claim, deps)
        deliver_result = await deliver.process_claim(claim, deps)

        assert evaluate_result.success is True
        assert evaluate_result.artifact_ref is not None
        assert deliver_result.success is True
        assert deliver_result.artifact_ref is not None

    asyncio.run(_run())


@pytest.mark.unit
def test_export_handler_uses_storage_contract() -> None:
    async def _run() -> None:
        result = await exports.export_results_handler(
            submission_id="sub-1",
            feedback_ref="feedback/sub-1.json",
            storage=StubStorageClient(),
        )
        assert result.export_ref.startswith("stub://exports/")

    asyncio.run(_run())


@pytest.mark.unit
def test_api_deps_can_be_constructed() -> None:
    deps = ApiDeps(
        repository=InMemoryWorkRepository(),
        storage=StubStorageClient(),
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
        submissions={},
    )
    assert deps is not None


@pytest.mark.unit
def test_synthetic_pipeline_handler_returns_none_for_missing_submission() -> None:
    deps = ApiDeps(
        repository=InMemoryWorkRepository(),
        storage=StubStorageClient(),
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
        submissions={},
    )

    async def _run() -> None:
        result = await run_test_pipeline_handler(submission_id="missing", api_deps=deps)
        assert result is None

    asyncio.run(_run())
