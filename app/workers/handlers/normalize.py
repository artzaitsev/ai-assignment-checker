from __future__ import annotations

import asyncio

from app.clients.llm import LLMAdapterError, LLMRetryableError
from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.dto import NormalizePayloadCommand, NormalizationTaskInput
from app.domain.models import ProcessResult, WorkItemClaim
from app.domain.use_cases.normalize import normalize_payload
from app.lib.artifacts.refs import storage_key_from_ref
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.normalize.process_claim"


async def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    """Process normalize stage with strict schema validation."""
    try:
        raw_artifact_ref = await deps.repository.get_artifact_ref(item_id=claim.item_id, stage="raw")
        raw_storage_key = storage_key_from_ref(raw_artifact_ref)
        raw_payload = await asyncio.to_thread(deps.storage.get_bytes, key=raw_storage_key)
        filename = raw_storage_key.rsplit("/", maxsplit=1)[-1]

        submission = await deps.repository.get_submission(submission_id=claim.item_id)
        if submission is None:
            raise KeyError(f"submission not found: {claim.item_id}")

        assignments = await deps.repository.list_assignments(active_only=False, include_task_schema=True)
        assignment = next((item for item in assignments if item.assignment_public_id == submission.assignment_public_id), None)
        if assignment is None or assignment.task_schema is None:
            raise KeyError(f"assignment not found: {submission.assignment_public_id}")

        assignment_tasks = tuple(
            NormalizationTaskInput(task_id=task.task_id, task_index=index, task_text=task.title)
            for index, task in enumerate(assignment.task_schema.tasks, start=1)
        )
        result = await asyncio.to_thread(
            normalize_payload,
            NormalizePayloadCommand(
                submission_id=claim.item_id,
                artifact_ref=raw_artifact_ref,
                filename=filename,
                source_type="api_upload",
                persisted_mime=None,
                raw_payload=raw_payload,
                assignment_public_id=assignment.assignment_public_id,
                assignment_language=assignment.language,
                assignment_tasks=assignment_tasks,
            ),
            llm=deps.llm,
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
        if "unsupported submission format" in str(exc):
            code = "unsupported_format"
        elif "could not be parsed" in str(exc):
            code = "file_parse_failed"
        else:
            code = "schema_validation_failed"
        error_code = resolve_stage_error(stage="normalized", code=code)
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except LLMRetryableError as exc:
        error_code = resolve_stage_error(stage="normalized", code="llm_provider_unavailable")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except LLMAdapterError as exc:
        error_code = resolve_stage_error(stage="normalized", code="validation_error")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )

    artifact_ref = await asyncio.to_thread(
        deps.artifact_repository.save_normalized,
        submission_id=claim.item_id,
        artifact=result.normalized_artifact,
    )
    return ProcessResult(
        success=True,
        detail="normalize artifact contract satisfied",
        artifact_ref=artifact_ref,
        artifact_version=result.schema_version,
    )
