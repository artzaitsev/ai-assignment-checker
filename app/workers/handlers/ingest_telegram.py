from __future__ import annotations

from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.models import SortOrder, SubmissionFieldGroup, SubmissionListQuery
from app.domain.models import ProcessResult, WorkItemClaim
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.ingest_telegram.process_claim"


async def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    """Process Telegram intake task into raw artifact persisted in storage."""
    try:
        items = await deps.repository.list_submissions(
            query=SubmissionListQuery(
                submission_ids=(claim.item_id,),
                include=frozenset(
                    {
                        SubmissionFieldGroup.CORE,
                        SubmissionFieldGroup.SOURCE,
                    }
                ),
                sort_order=SortOrder.ASC,
                limit=1,
            )
        )
        if not items or items[0].source is None:
            raise ValueError(f"telegram source is missing for submission: {claim.item_id}")

        source = await deps.repository.find_submission_source(
            source_type="telegram_webhook",
            source_external_id=items[0].source.external_id,
        )
        if source is None:
            raise ValueError(f"telegram source payload is missing for submission: {claim.item_id}")

        file_id_raw = source.metadata_json.get("file_id")
        if not isinstance(file_id_raw, str) or not file_id_raw:
            raise ValueError("telegram webhook metadata.file_id is required")
        file_name_raw = source.metadata_json.get("file_name")
        file_name = file_name_raw if isinstance(file_name_raw, str) and file_name_raw else "submission.bin"

        file_payload = deps.telegram.get_file_bytes(file_id=file_id_raw)
        storage_key = f"raw/{claim.item_id}/{file_name}"
        raw_ref = deps.storage.put_bytes(key=storage_key, payload=file_payload)
    except ValueError as exc:
        error_code = resolve_stage_error(stage="raw", code="telegram_update_invalid")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except KeyError as exc:
        error_code = resolve_stage_error(stage="raw", code="telegram_file_fetch_failed")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )

    return ProcessResult(
        success=True,
        detail="telegram ingest contract satisfied",
        artifact_ref=raw_ref,
        artifact_version=None,
    )
