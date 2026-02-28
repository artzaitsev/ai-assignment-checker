from __future__ import annotations

import asyncio

import pytest

from app.clients.stub import StubLLMClient, StubStorageClient, StubTelegramClient
from app.lib.artifacts import build_artifact_repository
from app.repositories.stub import InMemoryWorkRepository
from app.workers.handlers.deps import WorkerDeps
from app.workers.handlers.factory import build_process_handler
from app.workers.loop import WorkerLoop


@pytest.mark.integration
def test_worker_loops_cover_full_backend_flow() -> None:
    async def _run() -> None:
        repository = InMemoryWorkRepository()
        storage = StubStorageClient()
        artifact_repository = build_artifact_repository(storage=storage)
        telegram = StubTelegramClient()
        llm = StubLLMClient()
        deps = WorkerDeps(
            repository=repository,
            artifact_repository=artifact_repository,
            storage=storage,
            telegram=telegram,
            llm=llm,
        )

        candidate = await repository.create_candidate(first_name="Flow", last_name="Candidate")
        assignment = await repository.create_assignment(title="Flow Assignment", description="Flow Description")
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="flow-e2e-1",
            initial_status="uploaded",
        )
        raw_ref = storage.put_bytes(
            key=f"raw/{created.submission_id}/submission.txt",
            payload=b"print('hello')",
        )
        await repository.link_artifact(
            item_id=created.submission_id,
            stage="raw",
            artifact_ref=raw_ref,
            artifact_version=None,
        )

        normalize_loop = WorkerLoop(
            role="worker-normalize",
            stage="normalized",
            repository=repository,
            process=build_process_handler("worker-normalize", deps),
        )
        evaluate_loop = WorkerLoop(
            role="worker-evaluate",
            stage="llm-output",
            repository=repository,
            process=build_process_handler("worker-evaluate", deps),
        )
        deliver_loop = WorkerLoop(
            role="worker-deliver",
            stage="exports",
            repository=repository,
            process=build_process_handler("worker-deliver", deps),
        )

        assert await normalize_loop.run_once() is True
        assert await evaluate_loop.run_once() is True
        assert await deliver_loop.run_once() is True

        snapshot = await repository.get_submission(submission_id=created.submission_id)
        assert snapshot is not None
        assert snapshot.status == "delivered"
        assert telegram.notifications[created.submission_id]
        assert repository.llm_runs
        assert repository.evaluations

    asyncio.run(_run())
