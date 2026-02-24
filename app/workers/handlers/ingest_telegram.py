from __future__ import annotations

from app.domain.models import ProcessResult, WorkItemClaim
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.ingest_telegram.process_claim"


async def process_claim(claim: WorkItemClaim, deps: WorkerDeps) -> ProcessResult:
    """Here you can implement production business logic for worker.ingest_telegram.process_claim."""
    updates = deps.telegram.poll_updates()
    del updates
    return ProcessResult(
        success=True,
        detail="skeleton ingest telegram handler",
        artifact_ref=f"raw/{claim.item_id}.json",
        artifact_version="v0-skeleton",
    )
