from __future__ import annotations

from app.api.handlers.deps import ApiDeps
from app.api.schemas import SubmissionStatusResponse

COMPONENT_ID = "api.get_submission_status"


async def get_submission_status_handler(submission_id: str) -> SubmissionStatusResponse:
    """Here you can implement production business logic for api.get_submission_status."""
    return SubmissionStatusResponse(
        submission_id=submission_id,
        state="skeleton",
    )


async def get_submission_status_with_trace_handler(
    deps: ApiDeps,
    *,
    submission_id: str,
) -> SubmissionStatusResponse | None:
    """Here you can implement production business logic for api.get_submission_status."""
    record = deps.submissions.get(submission_id)
    if record is None:
        return None
    return SubmissionStatusResponse(
        submission_id=record.submission_id,
        candidate_public_id=record.candidate_public_id,
        assignment_public_id=record.assignment_public_id,
        state=record.state,
        transitions=list(record.transitions),
        artifacts=dict(record.artifacts),
    )
