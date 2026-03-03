from __future__ import annotations

import asyncio

import pytest

from app.clients.stub import StubLLMClient, StubStorageClient, StubTelegramClient
from app.lib.artifacts import build_artifact_repository
from app.repositories.stub import InMemoryWorkRepository
from app.workers.handlers.deps import WorkerDeps
from app.workers.handlers.normalize import process_claim
from app.workers.loop import WorkerLoop


@pytest.mark.unit
def test_normalize_worker_sets_unsupported_format_error_code() -> None:
    async def _run() -> None:
        repository = InMemoryWorkRepository()
        storage = StubStorageClient()
        artifact_repository = build_artifact_repository(storage=storage)

        candidate = await repository.create_candidate(first_name="N", last_name="U")
        assignment = await repository.create_assignment(title="A", description="D")
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="unsupported-1",
            initial_status="uploaded",
        )

        raw_ref = storage.put_bytes(key=f"raw/{created.submission_id}/payload.png", payload=b"img")
        await repository.link_artifact(
            item_id=created.submission_id,
            stage="raw",
            artifact_ref=raw_ref,
            artifact_version=None,
        )

        deps = WorkerDeps(
            repository=repository,
            artifact_repository=artifact_repository,
            storage=storage,
            telegram=StubTelegramClient(),
            llm=StubLLMClient(),
        )
        loop = WorkerLoop(
            role="worker-normalize",
            stage="normalized",
            repository=repository,
            process=lambda claim: process_claim(deps, claim=claim),
        )

        did_work = await loop.run_once()
        assert did_work is True

        snapshot = await repository.get_submission(submission_id=created.submission_id)
        assert snapshot is not None
        assert snapshot.status == "failed_normalization"
        assert snapshot.attempt_normalization == 1
        assert snapshot.last_error_code == "unsupported_format"

    asyncio.run(_run())


@pytest.mark.unit
def test_normalize_parse_error_retries_then_dead_letter(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_parse_error(payload: bytes) -> str:
        del payload
        raise RuntimeError("pdf parse exploded")

    monkeypatch.setattr("app.domain.normalization._parse_pdf", _raise_parse_error)

    async def _run() -> None:
        repository = InMemoryWorkRepository()
        storage = StubStorageClient()
        artifact_repository = build_artifact_repository(storage=storage)

        candidate = await repository.create_candidate(first_name="N", last_name="U")
        assignment = await repository.create_assignment(title="A", description="D")
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="parse-1",
            initial_status="uploaded",
        )

        raw_ref = storage.put_bytes(key=f"raw/{created.submission_id}/payload.pdf", payload=b"%PDF-fake")
        await repository.link_artifact(
            item_id=created.submission_id,
            stage="raw",
            artifact_ref=raw_ref,
            artifact_version=None,
        )

        deps = WorkerDeps(
            repository=repository,
            artifact_repository=artifact_repository,
            storage=storage,
            telegram=StubTelegramClient(),
            llm=StubLLMClient(),
        )
        loop = WorkerLoop(
            role="worker-normalize",
            stage="normalized",
            repository=repository,
            process=lambda claim: process_claim(deps, claim=claim),
        )

        assert await loop.run_once() is True
        assert await loop.run_once() is True
        assert await loop.run_once() is True

        snapshot = await repository.get_submission(submission_id=created.submission_id)
        assert snapshot is not None
        assert snapshot.status == "dead_letter"
        assert snapshot.attempt_normalization == 3
        assert snapshot.last_error_code == "normalization_parse_error"

    asyncio.run(_run())
