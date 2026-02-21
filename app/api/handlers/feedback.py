from __future__ import annotations

COMPONENT_ID = "api.list_feedback"


async def list_feedback_handler(submission_id: str | None = None) -> dict[str, object]:
    """Here you can implement production business logic for api.list_feedback."""
    return {
        "submission_id": submission_id,
        "items": [],
        "mode": "skeleton",
    }
