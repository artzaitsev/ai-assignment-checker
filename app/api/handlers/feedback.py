from __future__ import annotations

from app.api.schemas import FeedbackListResponse

COMPONENT_ID = "api.list_feedback"


async def list_feedback_handler(submission_id: str | None = None) -> FeedbackListResponse:
    """Here you can implement production business logic for api.list_feedback."""
    del submission_id
    return FeedbackListResponse(items=[])
