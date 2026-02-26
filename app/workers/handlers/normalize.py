from __future__ import annotations

from app.domain.dto import NormalizePayloadCommand
from app.domain.errors import NormalizationParseError, UnsupportedFormatError
from app.domain.models import ProcessResult, WorkItemClaim
from app.domain.use_cases.normalize import normalize_payload
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.normalize.process_claim"


async def process_claim(claim: WorkItemClaim, deps: WorkerDeps) -> ProcessResult:
    """Production normalize handler for raw -> normalized artifact stage."""
    artifact_refs = await deps.repository.get_artifact_refs(item_id=claim.item_id)
    raw_ref = artifact_refs.get("raw")
    if raw_ref is None:
        return ProcessResult(
            success=False,
            detail="raw artifact is missing",
            error_code="normalization_parse_error",
        )

    try:
        result = normalize_payload(
            NormalizePayloadCommand(submission_id=claim.item_id, artifact_ref=raw_ref),
            storage=deps.storage,
        )
    except UnsupportedFormatError as exc:
        return ProcessResult(success=False, detail=str(exc), error_code="unsupported_format")
    except NormalizationParseError as exc:
        return ProcessResult(success=False, detail=str(exc), error_code="normalization_parse_error")

    return ProcessResult(
        success=True,
        detail="normalize completed",
        artifact_ref=result.normalized_ref,
        artifact_version=result.schema_version,
    )
