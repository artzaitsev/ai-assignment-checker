from __future__ import annotations

import logging

from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.models import ProcessResult, WorkItemClaim
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.ingest_telegram.process_claim"

logger = logging.getLogger("runtime")


async def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    """Poll Telegram for new updates and create submissions for each update.

    Uses long-polling with timeout to avoid blocking the worker loop.
    Timeout ensures worker doesn't get stuck and can handle graceful shutdown.
    """
    try:
        # Poll Telegram with timeout (long-polling behavior)
        updates = deps.telegram.poll_updates(timeout=30)

        if not updates:
            # No new updates, idle tick
            return ProcessResult(
                success=True,
                detail="no new telegram updates",
                artifact_ref=None,
                artifact_version=None,
            )

        created_count = 0
        for update in updates:
            update_id = update.get("update_id")
            candidate_public_id = update.get("candidate_public_id")
            assignment_public_id = update.get("assignment_public_id")
            file_id = update.get("file_id")
            file_name = update.get("file_name", "submission.bin")

            if not all([update_id, candidate_public_id, assignment_public_id, file_id]):
                logger.warning(
                    "telegram update missing required fields",
                    extra={"update": update},
                )
                continue

            # Create submission with telegram poll source (idempotent by update_id)
            persisted = await deps.repository.create_submission_with_source(
                candidate_public_id=candidate_public_id,
                assignment_public_id=assignment_public_id,
                source_type="telegram_poll",
                source_external_id=update_id,
                initial_status="telegram_update_received",
                metadata_json={
                    "update_id": update_id,
                    "file_id": file_id,
                    "file_name": file_name,
                    "entrypoint": "telegram_poll",
                },
            )
            created_count += 1
            logger.info(
                "created submission from telegram poll",
                extra={
                    "submission_id": persisted.submission_id,
                    "update_id": update_id,
                },
            )

        return ProcessResult(
            success=True,
            detail=f"processed {created_count} telegram updates",
            artifact_ref=None,
            artifact_version=None,
        )

    except Exception as exc:
        error_code = resolve_stage_error(stage="raw", code="telegram_poll_failed")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
