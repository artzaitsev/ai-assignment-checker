from __future__ import annotations

from app.domain.models import AssignmentSnapshot


_QUEUED_STATES = {
    "uploaded",
}
_IN_PROGRESS_STATES = {
    "normalization_in_progress",
    "normalized",
    "evaluation_in_progress",
    "delivery_in_progress",
}
_SUCCESS_STATES = {
    "evaluated",
    "delivered",
}
_FAILURE_STATES = {
    "failed_telegram_ingest",
    "failed_normalization",
    "failed_evaluation",
    "failed_delivery",
    "dead_letter",
}


def page_context(*, error_message: str | None = None) -> dict[str, object]:
    return {"error_message": error_message}


def form_context(
    *,
    assignments: list[AssignmentSnapshot],
    assignment_hint: str | None,
    assignment_locked: bool = False,
) -> dict[str, object]:
    return {
        "assignments": assignments,
        "assignment_hint": assignment_hint,
        "assignment_locked": assignment_locked,
    }


def result_context(*, success: bool, title: str, message: str, submission_id: str | None = None) -> dict[str, object]:
    return {
        "success": success,
        "title": title,
        "message": message,
        "submission_id": submission_id,
    }


def result_page_context(*, submission_id: str) -> dict[str, object]:
    return {"submission_id": submission_id}


def result_panel_context(
    *,
    submission_id: str,
    state: str,
    feedback_item: dict[str, object] | None,
) -> dict[str, object]:
    status_kind = "in_progress"
    title = "Проверяется"
    message = "Мы обрабатываем Вашу работу. Обновление происходит автоматически."
    status_label = "проверяется"
    poll_enabled = True

    if state in _QUEUED_STATES:
        status_kind = "queued"
        title = "Проверяется"
        message = "Мы проверяем Вашу работу. Обычно это занимает несколько минут."
    elif state in _IN_PROGRESS_STATES:
        status_kind = "in_progress"
        title = "Проверяется"
        message = "Мы проверяем Вашу работу. Обычно это занимает несколько минут."
    elif state in _SUCCESS_STATES:
        status_kind = "success"
        title = "Проверка завершена"
        message = "Результаты готовы."
        status_label = "готово"
        poll_enabled = feedback_item is None
    elif state in _FAILURE_STATES:
        status_kind = "failure"
        title = "Проверка завершилась с ошибкой"
        message = "Попробуйте отправить обновленную версию файла."
        status_label = "ошибка"
        poll_enabled = False

    return {
        "submission_id": submission_id,
        "status_label": status_label,
        "status_kind": status_kind,
        "title": title,
        "message": message,
        "poll_enabled": poll_enabled,
        "feedback_item": feedback_item,
    }
