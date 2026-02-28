from __future__ import annotations

from app.api.handlers.deps import ApiDeps, SubmissionRecord
from app.api.schemas import TelegramWebhookResponse

COMPONENT_ID = "api.telegram_webhook"


async def telegram_webhook_handler(
    deps: ApiDeps,
    *,
    update_id: str,
    candidate_public_id: str,
    assignment_public_id: str,
    file_id: str,
    file_name: str | None,
) -> TelegramWebhookResponse:
    """Persist Telegram intake update idempotently for ingest worker."""
    persisted = await deps.repository.create_submission_with_source(
        candidate_public_id=candidate_public_id,
        assignment_public_id=assignment_public_id,
        source_type="telegram_webhook",
        source_external_id=update_id,
        initial_status="telegram_update_received",
        metadata_json={
            "update_id": update_id,
            "file_id": file_id,
            "file_name": file_name or "submission.bin",
            "entrypoint": "telegram_webhook",
        },
    )

    record = deps.submissions.get(persisted.submission_id)
    if record is None:
        deps.submissions[persisted.submission_id] = SubmissionRecord(
            submission_id=persisted.submission_id,
            state=persisted.status,
            candidate_public_id=candidate_public_id,
            assignment_public_id=assignment_public_id,
            transitions=[persisted.status],
            artifacts={},
        )
    else:
        record.state = persisted.status
        if not record.transitions:
            record.transitions.append(persisted.status)

    return TelegramWebhookResponse(
        submission_id=persisted.submission_id,
        state=persisted.status,
        created=persisted.created,
    )
