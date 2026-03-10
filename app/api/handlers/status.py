from __future__ import annotations

from app.api.handlers.deps import ApiDeps, SubmissionRecord
from app.api.schemas import SubmissionStatusResponse

COMPONENT_ID = "api.get_submission_status"
_ARTIFACT_STAGES = ("raw", "normalized", "llm-output", "exports")


async def get_submission_status_handler(
    deps: ApiDeps,
    *,
    submission_id: str,
) -> SubmissionStatusResponse | None:
    """Resolve submission status from repository with optional debug trace context."""
    snapshot = await deps.repository.get_submission(submission_id=submission_id)
    if snapshot is None:
        return None

    artifacts = await _resolve_artifacts(deps=deps, submission_id=submission_id)
    debug_record = deps.submissions.get(submission_id)

    transitions: list[str] | None = None
    if debug_record is not None:
        transitions = list(debug_record.transitions)
        artifacts = _merge_debug_artifacts(artifacts=artifacts, record=debug_record)

    return SubmissionStatusResponse(
        submission_id=submission_id,
        candidate_public_id=snapshot.candidate_public_id,
        assignment_public_id=snapshot.assignment_public_id,
        state=snapshot.status,
        transitions=transitions,
        artifacts=artifacts or None,
    )


async def get_submission_status_with_trace_handler(
    deps: ApiDeps,
    *,
    submission_id: str,
) -> SubmissionStatusResponse | None:
    return await get_submission_status_handler(deps=deps, submission_id=submission_id)


def _merge_debug_artifacts(*, artifacts: dict[str, str], record: SubmissionRecord) -> dict[str, str]:
    merged = dict(artifacts)
    for key, value in record.artifacts.items():
        merged.setdefault(key, value)
    return merged


async def _resolve_artifacts(*, deps: ApiDeps, submission_id: str) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for stage in _ARTIFACT_STAGES:
        try:
            artifact_ref = await deps.repository.get_artifact_ref(item_id=submission_id, stage=stage)
        except KeyError:
            continue
        artifacts[stage] = artifact_ref
    return artifacts
