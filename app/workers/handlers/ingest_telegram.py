from __future__ import annotations

import logging

from app.domain.models import ProcessResult, WorkItemClaim
from app.domain.use_cases.telegram_entry_links import build_candidate_apply_link, sign_entry_token
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.ingest_telegram.process_claim"
TELEGRAM_UPDATES_STREAM = "telegram_updates"
UNSUPPORTED_EVENT_HELP = (
    "Я помогу Вам начать подачу заявки. Отправьте /start, чтобы получить защищенную ссылку на форму."
)

logger = logging.getLogger("runtime")


async def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    """Poll Telegram and respond with entrypoint links/help."""
    del claim
    try:
        last_update_id = await deps.repository.get_stream_cursor(stream=TELEGRAM_UPDATES_STREAM)
        events = deps.telegram.poll_events(timeout=30, offset=last_update_id)

        if not events:
            return ProcessResult(
                success=True,
                detail="no new telegram events",
                artifact_ref=None,
                artifact_version=None,
            )

        responded_count = 0
        skipped_count = 0
        for event in events:
            if deps.telegram_link_settings is None:
                raise RuntimeError("telegram link settings are not configured")

            command = _resolve_command(event)
            assignment_hint = _assignment_hint_from_event(event)

            if command == "/start":
                token = sign_entry_token(
                    chat_id=event.chat_id,
                    assignment_hint=assignment_hint,
                    settings=deps.telegram_link_settings,
                )
                signed_link = build_candidate_apply_link(settings=deps.telegram_link_settings, token=token)
                deps.telegram.send_text(
                    chat_id=event.chat_id,
                    message=f"Начните здесь: {signed_link}",
                )
            else:
                deps.telegram.send_text(chat_id=event.chat_id, message=UNSUPPORTED_EVENT_HELP)

            await deps.repository.set_stream_cursor(stream=TELEGRAM_UPDATES_STREAM, cursor=event.update_id)
            responded_count += 1
            logger.info(
                "telegram event handled",
                extra={"update_id": event.update_id, "command": command or "<none>"},
            )

        return ProcessResult(
            success=True,
            detail=f"processed {responded_count} telegram events (skipped {skipped_count})",
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


def _resolve_command(event: object) -> str | None:
    command = getattr(event, "command", None)
    if isinstance(command, str) and command:
        return command
    text = getattr(event, "text", None)
    if not isinstance(text, str):
        return None
    normalized = text.strip()
    if not normalized.startswith("/"):
        return None
    return normalized.split(maxsplit=1)[0]


def _assignment_hint_from_event(event: object) -> str | None:
    text = getattr(event, "text", None)
    if not isinstance(text, str):
        return None
    normalized = text.strip()
    if not normalized.startswith("/start"):
        return None
    parts = normalized.split(maxsplit=1)
    if len(parts) < 2:
        return None
    hint = parts[1].strip()
    return hint or None
