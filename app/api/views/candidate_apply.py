from __future__ import annotations

from app.domain.models import AssignmentSnapshot


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
