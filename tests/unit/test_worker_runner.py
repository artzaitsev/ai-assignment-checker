import asyncio
import logging
from dataclasses import dataclass

import pytest

from app.domain.errors import DomainInvariantError
from app.domain.models import ProcessResult, WorkItemClaim
from app.repositories.stub import InMemoryWorkRepository
from app.workers.loop import WorkerLoop
from app.workers.runner import (
    WorkerRuntimeSettings,
    WorkerRuntimeState,
    worker_runtime_settings_from_env,
)


async def _process(claim: WorkItemClaim) -> ProcessResult:
    return ProcessResult(
        success=True,
        detail="ok",
        artifact_ref=f"exports/{claim.item_id}.json",
        artifact_version="test",
    )


@pytest.mark.unit
def test_worker_runtime_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_POLL_INTERVAL_MS", "50")
    monkeypatch.setenv("WORKER_IDLE_BACKOFF_MS", "100")
    monkeypatch.setenv("WORKER_ERROR_BACKOFF_MS", "150")
    monkeypatch.setenv("WORKER_CLAIM_LEASE_SECONDS", "45")
    monkeypatch.setenv("WORKER_HEARTBEAT_INTERVAL_MS", "5000")

    settings = worker_runtime_settings_from_env()

    assert settings == WorkerRuntimeSettings(
        poll_interval_ms=50,
        idle_backoff_ms=100,
        error_backoff_ms=150,
        claim_lease_seconds=45,
        heartbeat_interval_ms=5000,
    )


@pytest.mark.unit
def test_worker_runtime_settings_fall_back_on_bad_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_POLL_INTERVAL_MS", "abc")
    monkeypatch.setenv("WORKER_IDLE_BACKOFF_MS", "0")
    monkeypatch.setenv("WORKER_ERROR_BACKOFF_MS", "-10")

    settings = worker_runtime_settings_from_env()

    assert settings == WorkerRuntimeSettings()


@pytest.mark.unit
def test_worker_loop_run_once_processes_claim() -> None:
    repository = InMemoryWorkRepository(
        queue=[WorkItemClaim(item_id="job-1", stage="exports", attempt=1)]
    )
    loop = WorkerLoop(
        role="worker-deliver",
        stage="exports",
        repository=repository,
        process=_process,
    )

    did_work = asyncio.run(loop.run_once())

    assert did_work is True
    assert repository.finalizations[0][0] == "job-1"


@pytest.mark.unit
def test_worker_loop_maintains_lease_during_processing() -> None:
    async def _process_long(claim: WorkItemClaim) -> ProcessResult:
        del claim
        await asyncio.sleep(0.05)
        return ProcessResult(success=True, detail="ok")

    async def _run() -> None:
        repository = InMemoryWorkRepository()
        candidate = await repository.create_candidate(first_name="Test", last_name="Candidate")
        assignment = await repository.create_assignment(title="Task", description="desc")
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="hb-1",
            initial_status="normalized",
        )
        loop = WorkerLoop(
            role="worker-evaluate",
            stage="llm-output",
            repository=repository,
            process=_process_long,
            claim_lease_seconds=30,
            heartbeat_interval_ms=5,
        )

        did_work = await loop.run_once()
        assert did_work is True

        snapshot = await repository.get_submission(submission_id=created.submission_id)
        assert snapshot is not None
        assert snapshot.status == "evaluated"

    asyncio.run(_run())


@pytest.mark.unit
def test_worker_loop_fails_when_lease_is_lost() -> None:
    class _FailingHeartbeatRepository(InMemoryWorkRepository):
        async def heartbeat_claim(
            self,
            *,
            item_id: str,
            stage: str,
            worker_id: str,
            lease_seconds: int = 30,
        ) -> bool:
            del item_id, stage, worker_id, lease_seconds
            return False

    async def _process_long(claim: WorkItemClaim) -> ProcessResult:
        del claim
        await asyncio.sleep(0.05)
        return ProcessResult(success=True, detail="ok")

    async def _run() -> None:
        repository = _FailingHeartbeatRepository()
        candidate = await repository.create_candidate(first_name="Test", last_name="Candidate")
        assignment = await repository.create_assignment(title="Task", description="desc")
        await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="hb-2",
            initial_status="normalized",
        )
        loop = WorkerLoop(
            role="worker-evaluate",
            stage="llm-output",
            repository=repository,
            process=_process_long,
            claim_lease_seconds=30,
            heartbeat_interval_ms=5,
        )

        with pytest.raises(DomainInvariantError, match="claim ownership is stale"):
            await loop.run_once()

    asyncio.run(_run())


@dataclass
class _FlakyLoop:
    calls: int = 0

    @property
    def stage(self) -> str:
        return "exports"

    async def run_once(self) -> bool:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        return False


@pytest.mark.unit
def test_runner_survives_errors_and_continues() -> None:
    from app.workers.runner import run_worker_until_stopped

    flaky_loop = _FlakyLoop()
    stop_event = asyncio.Event()
    settings = WorkerRuntimeSettings(poll_interval_ms=1, idle_backoff_ms=1, error_backoff_ms=1)
    state = WorkerRuntimeState()

    async def _run() -> None:
        task = asyncio.create_task(
            run_worker_until_stopped(
                worker_loop=flaky_loop,  # pyright: ignore[reportArgumentType]
                role="worker-deliver",
                run_id="run-1",
                stop_event=stop_event,
                settings=settings,
                logger=logging.getLogger("test"),
                state=state,
            )
        )
        await asyncio.sleep(0.02)
        stop_event.set()
        await task

    asyncio.run(_run())
    assert flaky_loop.calls >= 2
    assert state.started is True
    assert state.stopped is True
    assert state.ticks_total >= 2
    assert state.errors_total >= 1


@pytest.mark.unit
def test_runner_reclaims_expired_claims_before_tick() -> None:
    from app.workers.runner import run_worker_until_stopped

    class _CountingRepository(InMemoryWorkRepository):
        reclaim_calls: int

        def __init__(self) -> None:
            super().__init__()
            self.reclaim_calls = 0

        async def reclaim_expired_claims(self, *, stage: str) -> int:
            self.reclaim_calls += 1
            return await super().reclaim_expired_claims(stage=stage)

    repository = _CountingRepository()
    loop = WorkerLoop(
        role="worker-deliver",
        stage="exports",
        repository=repository,
        process=_process,
    )
    stop_event = asyncio.Event()
    settings = WorkerRuntimeSettings(poll_interval_ms=1, idle_backoff_ms=1, error_backoff_ms=1)

    async def _run() -> None:
        task = asyncio.create_task(
            run_worker_until_stopped(
                worker_loop=loop,
                role="worker-deliver",
                run_id="run-reclaim",
                stop_event=stop_event,
                settings=settings,
                logger=logging.getLogger("test"),
            )
        )
        await asyncio.sleep(0.01)
        stop_event.set()
        await task

    asyncio.run(_run())
    assert repository.reclaim_calls >= 1
