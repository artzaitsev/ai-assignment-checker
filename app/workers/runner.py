from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

from app.workers.loop import WorkerLoop


@dataclass(frozen=True)
class WorkerRuntimeSettings:
    poll_interval_ms: int = 200
    idle_backoff_ms: int = 1000
    error_backoff_ms: int = 2000
    claim_lease_seconds: int = 30
    heartbeat_interval_ms: int = 10000


@dataclass
class WorkerRuntimeState:
    started: bool = False
    stopped: bool = False
    ticks_total: int = 0
    claims_total: int = 0
    idle_ticks_total: int = 0
    errors_total: int = 0
    stage_duration_ms_total: dict[str, int] = field(default_factory=dict)
    stage_success_total: dict[str, int] = field(default_factory=dict)
    stage_retry_total: dict[str, int] = field(default_factory=dict)
    stage_terminal_failure_total: dict[str, int] = field(default_factory=dict)
    stage_error_total: dict[str, int] = field(default_factory=dict)


def worker_runtime_settings_from_env() -> WorkerRuntimeSettings:
    return WorkerRuntimeSettings(
        poll_interval_ms=_env_int("WORKER_POLL_INTERVAL_MS", 200),
        idle_backoff_ms=_env_int("WORKER_IDLE_BACKOFF_MS", 1000),
        error_backoff_ms=_env_int("WORKER_ERROR_BACKOFF_MS", 2000),
        claim_lease_seconds=_env_int("WORKER_CLAIM_LEASE_SECONDS", 30),
        heartbeat_interval_ms=_env_int("WORKER_HEARTBEAT_INTERVAL_MS", 10000),
    )


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError:
        return default

    return parsed if parsed > 0 else default


async def run_worker_until_stopped(
    *,
    worker_loop: WorkerLoop,
    role: str,
    run_id: str,
    stop_event: asyncio.Event,
    settings: WorkerRuntimeSettings,
    logger: logging.Logger,
    state: WorkerRuntimeState | None = None,
) -> None:
    if isinstance(worker_loop, WorkerLoop):
        worker_loop.claim_lease_seconds = settings.claim_lease_seconds
        worker_loop.heartbeat_interval_ms = settings.heartbeat_interval_ms

    if state is not None:
        state.started = True

    logger.info(
        "worker loop started",
        extra={"role": role, "service": role, "run_id": run_id, "stage": worker_loop.stage},
    )

    while not stop_event.is_set():
        delay_ms = settings.idle_backoff_ms
        try:
            if isinstance(worker_loop, WorkerLoop):
                await worker_loop.repository.reclaim_expired_claims(stage=worker_loop.stage)
            did_work = await worker_loop.run_once()
            if state is not None:
                state.ticks_total += 1
                if did_work:
                    state.claims_total += 1
                    _update_stage_metrics_from_loop(state=state, worker_loop=worker_loop)
                else:
                    state.idle_ticks_total += 1
            delay_ms = settings.poll_interval_ms if did_work else settings.idle_backoff_ms
            logger.info(
                "worker tick",
                extra={
                    "role": role,
                    "service": role,
                    "run_id": run_id,
                    "stage": worker_loop.stage,
                    "did_work": str(did_work).lower(),
                },
            )
        except Exception as exc:
            if state is not None:
                state.ticks_total += 1
                state.errors_total += 1
            delay_ms = settings.error_backoff_ms
            logger.exception(
                "worker tick error",
                extra={
                    "role": role,
                    "service": role,
                    "run_id": run_id,
                    "stage": worker_loop.stage,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay_ms / 1000)
        except TimeoutError:
            continue

    logger.info(
        "worker loop stopped",
        extra={"role": role, "service": role, "run_id": run_id, "stage": worker_loop.stage},
    )
    if state is not None:
        state.stopped = True


def _update_stage_metrics_from_loop(*, state: WorkerRuntimeState, worker_loop: WorkerLoop) -> None:
    diagnostics = worker_loop.last_run_diagnostics
    if diagnostics is None:
        return

    stage = diagnostics.stage
    state.stage_duration_ms_total[stage] = state.stage_duration_ms_total.get(stage, 0) + max(diagnostics.duration_ms, 0)

    if diagnostics.success:
        state.stage_success_total[stage] = state.stage_success_total.get(stage, 0) + 1
        return

    if diagnostics.error_code is None:
        return

    error_key = f"{stage}:{diagnostics.error_code}"
    state.stage_error_total[error_key] = state.stage_error_total.get(error_key, 0) + 1
    if diagnostics.retry_classification == "recoverable":
        state.stage_retry_total[error_key] = state.stage_retry_total.get(error_key, 0) + 1
        return
    state.stage_terminal_failure_total[error_key] = state.stage_terminal_failure_total.get(error_key, 0) + 1
