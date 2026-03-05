from __future__ import annotations

import logging
from typing import cast

from app.domain.models import ProcessResult, WorkItemClaim
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.ingest_telegram.process_claim"

logger = logging.getLogger("runtime")


async def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    """Poll Telegram and ingest new updates.

    This role is transport-facing. It does not rely on the DB claim loop to run;
    instead it polls Telegram and creates `uploaded` submissions directly.
    """
    del claim
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
        skipped_count = 0
        for update in updates:
            update_id = update.get("update_id")
            assignment_public_id = update.get("assignment_public_id")
            file_id = update.get("file_id")
            file_name = update.get("file_name", "submission.bin")
            candidate_public_id = update.get("candidate_public_id")
            telegram_user_id = update.get("telegram_user_id")
            first_name = update.get("first_name")
            last_name = update.get("last_name")

            if not (
                isinstance(update_id, str)
                and isinstance(assignment_public_id, str)
                and isinstance(file_id, str)
            ):
                logger.warning(
                    "telegram update missing required fields",
                    extra={"update": update},
                )
                skipped_count += 1
                continue

            if not isinstance(file_name, str) or not file_name:
                file_name = "submission.bin"

            update_id = cast(str, update_id)
            assignment_public_id = cast(str, assignment_public_id)
            file_id = cast(str, file_id)

            resolved_candidate_public_id = await _resolve_candidate_public_id(
                deps,
                update=update,
                candidate_public_id=candidate_public_id,
                telegram_user_id=telegram_user_id,
                first_name=first_name,
                last_name=last_name,
            )
            if resolved_candidate_public_id is None:
                logger.warning(
                    "telegram update missing candidate identity",
                    extra={"update": update},
                )
                skipped_count += 1
                continue

            # Idempotency is enforced by repository uniqueness on (source_type, source_external_id).
            persisted = await deps.repository.create_submission_with_source(
                candidate_public_id=resolved_candidate_public_id,
                assignment_public_id=assignment_public_id,
                source_type="telegram",
                source_external_id=update_id,
                initial_status="uploaded",
                metadata_json={
                    "update_id": update_id,
                    "file_id": file_id,
                    "file_name": file_name,
                    "entrypoint": "telegram_poll",
                },
            )
            if not persisted.created:
                continue

            payload = deps.telegram.get_file_bytes(file_id=file_id)
            raw_ref = deps.storage.put_bytes(
                key=f"raw/{persisted.submission_id}/{file_name}",
                payload=payload,
            )
            await deps.repository.link_artifact(
                item_id=persisted.submission_id,
                stage="raw",
                artifact_ref=raw_ref,
                artifact_version=None,
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
            detail=f"processed {created_count} telegram updates (skipped {skipped_count})",
            artifact_ref=None,
            artifact_version=None,
        )

    except Exception as exc:
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code="internal_error",
            retry_classification="recoverable",
        )


async def _resolve_candidate_public_id(
    deps: WorkerDeps,
    *,
    update: dict[str, str],
    candidate_public_id: object,
    telegram_user_id: object,
    first_name: object,
    last_name: object,
) -> str | None:
    if isinstance(candidate_public_id, str):
        return candidate_public_id

    if not isinstance(telegram_user_id, str) or not telegram_user_id:
        return None

    resolved_first_name = first_name if isinstance(first_name, str) and first_name else "Telegram"
    resolved_last_name = last_name if isinstance(last_name, str) and last_name else "Candidate"
    snapshot = await deps.repository.get_or_create_candidate_by_source(
        source_type="telegram",
        source_external_id=telegram_user_id,
        first_name=resolved_first_name,
        last_name=resolved_last_name,
        metadata_json={"telegram_user_id": telegram_user_id, "raw_update": dict(update)},
    )
    return snapshot.candidate_public_id
