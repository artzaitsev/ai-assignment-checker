from __future__ import annotations

import logging
from html import escape

from app.clients.telegram import TelegramNonRetryableError, TelegramRetryableError
from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.models import ProcessResult, WorkItemClaim
from app.domain.telegram_settings import TELEGRAM_DEFAULT_ASSIGNMENT_STREAM
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
        events = deps.telegram.poll_events(timeout=30, offset=_build_poll_offset(last_update_id))

        if not events:
            return ProcessResult(
                success=True,
                detail="no new telegram events",
                artifact_ref=None,
                artifact_version=None,
            )

        responded_count = 0
        skipped_count = 0
        configured_assignment_id = await _configured_assignment_id(deps)
        for event in events:
            if deps.telegram_link_settings is None:
                raise RuntimeError("telegram link settings are not configured")

            command = _resolve_command(event)
            if command == "/start":
                if configured_assignment_id is None:
                    await deps.repository.set_stream_cursor(stream=TELEGRAM_UPDATES_STREAM, cursor=event.update_id)
                    skipped_count += 1
                    continue
                token = sign_entry_token(
                    chat_id=event.chat_id,
                    assignment_hint=configured_assignment_id,
                    settings=deps.telegram_link_settings,
                )
                signed_link = build_candidate_apply_link(
                    settings=deps.telegram_link_settings,
                    token=token,
                    assignment_public_id=configured_assignment_id,
                )
                deps.telegram.send_text(
                    chat_id=event.chat_id,
                    message=_build_start_link_message(signed_link),
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

    except TelegramRetryableError as exc:
        error_code = resolve_stage_error(stage="raw", code="telegram_file_fetch_failed")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except TelegramNonRetryableError as exc:
        error_code = resolve_stage_error(stage="raw", code="validation_error")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
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


async def _configured_assignment_id(deps: WorkerDeps) -> str | None:
    assignment_id = await deps.repository.get_stream_cursor(stream=TELEGRAM_DEFAULT_ASSIGNMENT_STREAM)
    if assignment_id is None:
        return None
    normalized = assignment_id.strip()
    if not normalized:
        return None
    assignment = await deps.repository.get_assignment_by_public_id(
        assignment_public_id=normalized,
        include_task_schema=False,
    )
    if assignment is None or not assignment.is_active:
        return None
    return assignment.assignment_public_id


def _build_poll_offset(last_update_id: str | None) -> str | None:
    if last_update_id is None:
        return None
    try:
        return str(int(last_update_id) + 1)
    except ValueError:
        return last_update_id


def _build_start_link_message(link: str) -> str:
    safe_link = escape(link, quote=True)
    return (
        "Мы получили Вашу команду. "
        # f'Перейдите по ссылке, чтобы отправить решение: <a href="{safe_link}">Открыть форму</a>'
        f'Перейдите по ссылке, чтобы отправить решение: {safe_link}'
    )
