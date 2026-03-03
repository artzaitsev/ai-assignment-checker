from __future__ import annotations

from pydantic import ValidationError

from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.dto import NormalizePayloadCommand
from app.domain.errors import NormalizationParseError, UnsupportedFormatError
from app.domain.models import ProcessResult, SortOrder, SubmissionFieldGroup, SubmissionListQuery, WorkItemClaim
from app.domain.use_cases.normalize import normalize_payload
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.normalize.process_claim"


async def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    """Process normalize stage with supported-format parsing and contract validation."""
    try:
        raw_artifact_ref = await deps.repository.get_artifact_ref(item_id=claim.item_id, stage="raw")
        raw_storage_key = _storage_key_from_ref(raw_artifact_ref)
        raw_payload = deps.storage.get_bytes(key=raw_storage_key)

        submission = await deps.repository.get_submission(submission_id=claim.item_id)
        source_type = await _resolve_source_type(deps=deps, submission_id=claim.item_id)

        result = normalize_payload(
            NormalizePayloadCommand(
                submission_id=claim.item_id,
                artifact_ref=raw_artifact_ref,
                assignment_public_id=submission.assignment_public_id if submission is not None else None,
                source_type=source_type,
            ),
            raw_payload=raw_payload,
        )
    except KeyError as exc:
        error_code = resolve_stage_error(stage="normalized", code="artifact_missing")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except UnsupportedFormatError as exc:
        error_code = resolve_stage_error(stage="normalized", code="unsupported_format")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except NormalizationParseError as exc:
        error_code = resolve_stage_error(stage="normalized", code="normalization_parse_error")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except (ValueError, ValidationError) as exc:
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


async def _resolve_source_type(*, deps: WorkerDeps, submission_id: str) -> str:
    items = await deps.repository.list_submissions(
        query=SubmissionListQuery(
            submission_ids=(submission_id,),
            include=frozenset({SubmissionFieldGroup.SOURCE}),
            sort_order=SortOrder.ASC,
            limit=1,
        )
    )
    if not items:
        return "api_upload"
    source = items[0].source
    if source is None:
        return "api_upload"
    return source.type


def _storage_key_from_ref(artifact_ref: str) -> str:
    if "://" in artifact_ref:
        return artifact_ref.split("://", maxsplit=1)[1]
    return artifact_ref
