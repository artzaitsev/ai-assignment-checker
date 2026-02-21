from __future__ import annotations

from app.domain.dto import NormalizePayloadCommand
from app.domain.models import ProcessResult, WorkItemClaim
from app.domain.use_cases.normalize import normalize_payload
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.normalize.process_claim"


async def process_claim(claim: WorkItemClaim, deps: WorkerDeps) -> ProcessResult:
    """Here you can implement production business logic for worker.normalize.process_claim."""
    del deps
    result = normalize_payload(
        NormalizePayloadCommand(submission_id=claim.item_id, artifact_ref=f"raw/{claim.item_id}.bin")
    )
    return ProcessResult(
        success=True,
        detail="skeleton normalize handler",
        artifact_ref=result.normalized_ref,
        artifact_version=result.schema_version,
    )
