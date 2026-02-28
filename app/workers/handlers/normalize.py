from __future__ import annotations

from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.dto import NormalizePayloadCommand
from app.domain.models import ProcessResult, WorkItemClaim
from app.domain.use_cases.normalize import normalize_payload
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.normalize.process_claim"


async def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    """Process normalize stage with strict schema validation."""
    try:
        raw_artifact_ref = await deps.repository.get_artifact_ref(item_id=claim.item_id, stage="raw")
        raw_storage_key = _storage_key_from_ref(raw_artifact_ref)
        deps.storage.get_bytes(key=raw_storage_key)
        result = normalize_payload(
            NormalizePayloadCommand(submission_id=claim.item_id, artifact_ref=raw_artifact_ref)
        )
    except KeyError as exc:
        error_code = resolve_stage_error(stage="normalized", code="artifact_missing")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except ValueError as exc:
        error_code = resolve_stage_error(stage="normalized", code="schema_validation_failed")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )

    return ProcessResult(
        success=True,
        detail="normalize artifact contract satisfied",
        artifact_ref=deps.artifact_repository.save_normalized(
            submission_id=claim.item_id,
            artifact=result.normalized_artifact,
        ),
        artifact_version=result.schema_version,
    )


def _storage_key_from_ref(artifact_ref: str) -> str:
    if "://" in artifact_ref:
        return artifact_ref.split("://", maxsplit=1)[1]
    return artifact_ref
