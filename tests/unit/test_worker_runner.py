import asyncio
import logging
from dataclasses import dataclass

import pytest

from app.domain.models import ProcessResult, WorkItemClaim
from app.repositories.stub import InMemoryWorkRepository
from app.workers.loop import WorkerLoop
from app.workers.runner import (
    WorkerRuntimeSettings,
    WorkerRuntimeState,
    worker_runtime_settings_from_env,
)


def _process(claim: WorkItemClaim) -> ProcessResult:
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

    settings = worker_runtime_settings_from_env()

    assert settings == WorkerRuntimeSettings(
        poll_interval_ms=50,
        idle_backoff_ms=100,
        error_backoff_ms=150,
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

    did_work = loop.run_once()

    assert did_work is True
    assert repository.finalizations[0][0] == "job-1"


@dataclass
class _FlakyLoop:
    calls: int = 0

    @property
    def stage(self) -> str:
        return "exports"

    def run_once(self) -> bool:
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
