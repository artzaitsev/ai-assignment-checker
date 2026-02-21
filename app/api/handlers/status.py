from __future__ import annotations

from app.api.handlers.deps import ApiDeps

COMPONENT_ID = "api.get_submission_status"


async def get_submission_status_handler(submission_id: str) -> dict[str, object]:
    """Here you can implement production business logic for api.get_submission_status."""
    return {
        "submission_id": submission_id,
        "state": "skeleton",
    }


async def get_submission_status_with_trace_handler(
    *,
    submission_id: str,
    api_deps: ApiDeps,
) -> dict[str, object] | None:
    """Here you can implement production business logic for api.get_submission_status."""
    record = api_deps.submissions.get(submission_id)
    if record is None:
        return None
    return {
        "submission_id": record.submission_id,
        "state": record.state,
        "transitions": list(record.transitions),
        "artifacts": dict(record.artifacts),
    }
