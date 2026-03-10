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
) -> dict[str, object]:
    return {
        "assignments": assignments,
        "assignment_hint": assignment_hint,
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
    title = "Идет проверка"
    message = "Мы обрабатываем Вашу работу. Обновление происходит автоматически."
    poll_enabled = True

    if state in _QUEUED_STATES:
        status_kind = "queued"
        title = "Работа в очереди"
        message = "Файл получен и поставлен в очередь на нормализацию."
    elif state in _IN_PROGRESS_STATES:
        status_kind = "in_progress"
        title = "Идет проверка"
        message = "Нормализация и оценка выполняются. Обычно это занимает до нескольких минут."
    elif state in _SUCCESS_STATES:
        status_kind = "success"
        title = "Проверка завершена"
        message = "Результаты готовы."
        poll_enabled = feedback_item is None
    elif state in _FAILURE_STATES:
        status_kind = "failure"
        title = "Проверка завершилась с ошибкой"
        message = "Попробуйте отправить обновленную версию файла."
        poll_enabled = False

    return {
        "submission_id": submission_id,
        "state": state,
        "status_kind": status_kind,
        "title": title,
        "message": message,
        "poll_enabled": poll_enabled,
        "feedback_item": feedback_item,
    }
