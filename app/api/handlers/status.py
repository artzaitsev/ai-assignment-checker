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
    *,
    submission_id: str,
    api_deps: ApiDeps,
) -> SubmissionStatusResponse | None:
    """Combines in-memory trace with repository-backed status/artifact refs."""
    record = api_deps.submissions.get(submission_id)
    snapshot = await api_deps.repository.get_submission(submission_id=submission_id)

    if record is None and snapshot is None:
        return None

    repo_artifacts = await api_deps.repository.get_artifact_refs(item_id=submission_id)
    merged_artifacts: dict[str, str] = {}
    if record is not None:
        merged_artifacts.update(record.artifacts)
    merged_artifacts.update(repo_artifacts)

    if snapshot is not None:
        state = snapshot.status
        candidate_public_id = snapshot.candidate_public_id
        assignment_public_id = snapshot.assignment_public_id
    else:
        state = record.state
        candidate_public_id = record.candidate_public_id
        assignment_public_id = record.assignment_public_id

    return SubmissionStatusResponse(
        submission_id=submission_id,
        candidate_public_id=candidate_public_id,
        assignment_public_id=assignment_public_id,
        state=state,
        transitions=list(record.transitions) if record is not None else None,
        artifacts=merged_artifacts or None,
    )
