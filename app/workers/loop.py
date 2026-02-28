from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging

from app.domain.contracts import WorkRepository
from app.domain.artifacts import artifact_keys_for_stage
from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.errors import DomainInvariantError
from app.domain.lifecycle import STAGE_LIFECYCLES
from app.domain.models import ProcessResult, WorkItemClaim

ProcessHandler = Callable[[WorkItemClaim], Awaitable[ProcessResult]]
logger = logging.getLogger("runtime")


@dataclass
class WorkerLoop:
    role: str
    stage: str
    repository: WorkRepository
    process: ProcessHandler
    claim_lease_seconds: int = 30
    heartbeat_interval_ms: int = 10000

    async def run_once(self) -> bool:
        artifact_keys_for_stage(stage=self.stage)
        claim = await self.repository.claim_next(
            stage=self.stage,
            worker_id=self.role,
            lease_seconds=self.claim_lease_seconds,
        )
        if claim is None:
            return False

        lifecycle = STAGE_LIFECYCLES[self.stage]
        lease_lost = False
        stop_heartbeat = asyncio.Event()

        async def _heartbeat_loop() -> None:
            nonlocal lease_lost
            interval_seconds = max(self.heartbeat_interval_ms, 1) / 1000
            while not stop_heartbeat.is_set():
                try:
                    await asyncio.wait_for(stop_heartbeat.wait(), timeout=interval_seconds)
                    break
                except TimeoutError:
                    pass

                heartbeat_ok = await self.repository.heartbeat_claim(
                    item_id=claim.item_id,
                    stage=self.stage,
                    worker_id=self.role,
                    lease_seconds=self.claim_lease_seconds,
                )
                if not heartbeat_ok:
                    lease_lost = True
                    stop_heartbeat.set()
                    break

        heartbeat_task = asyncio.create_task(_heartbeat_loop())
        try:
            result = await self.process(claim)
        finally:
            stop_heartbeat.set()
            await heartbeat_task

        if lease_lost:
            raise DomainInvariantError("claim ownership is stale")

        if result.artifact_ref:
            await self.repository.link_artifact(
                item_id=claim.item_id,
                stage=self.stage,
                artifact_ref=result.artifact_ref,
                artifact_version=result.artifact_version,
            )

        error_code = None
        retry_classification = None
        if not result.success:
            error_code = resolve_stage_error(
                stage=self.stage,
                code=result.error_code or "internal_error",
            )
            retry_classification = result.retry_classification or classify_error(error_code)
            logger.warning(
                "worker stage failed",
                extra={
                    "submission_id": claim.item_id,
                    "stage": self.stage,
                    "last_error_code": error_code,
                    "retry_classification": retry_classification,
                },
            )

        await self.repository.finalize(
            item_id=claim.item_id,
            stage=self.stage,
            worker_id=self.role,
            success=result.success,
            detail=result.detail,
            error_code=error_code,
        )
        return True
