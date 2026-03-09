from __future__ import annotations

from app.api.handlers.deps import ApiDeps, SubmissionRecord
from app.api.schemas import SubmissionStatusResponse
from app.domain.models import SubmissionSnapshot

COMPONENT_ID = "api.get_submission_status"

_STATE_RANK = {
    "telegram_update_received": 10,
    "telegram_ingest_in_progress": 20,
    "uploaded": 30,
    "normalization_in_progress": 40,
    "normalized": 50,
    "evaluation_in_progress": 60,
    "evaluated": 70,
    "delivery_in_progress": 80,
    "delivered": 90,
    "failed_telegram_ingest": 100,
    "failed_normalization": 100,
    "failed_evaluation": 100,
    "failed_delivery": 100,
    "dead_letter": 110,
}
_ARTIFACT_STAGES = ("raw", "normalized", "llm-output", "exports")


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
    """Resolve submission status from repository with in-memory trace fallback."""
    record = deps.submissions.get(submission_id)
    snapshot = await deps.repository.get_submission(submission_id=submission_id)
    if record is None and snapshot is None:
        return None

    candidate_public_id, assignment_public_id = _resolve_identity(record=record, snapshot=snapshot)
    state = _resolve_state(record=record, snapshot=snapshot)
    artifacts = await _resolve_artifacts(deps=deps, submission_id=submission_id)

    transitions: list[str] | None = None
    if record is not None and state == record.state:
        transitions = list(record.transitions)
        merged_artifacts = dict(record.artifacts)
        merged_artifacts.update(artifacts)
        artifacts = merged_artifacts

    return SubmissionStatusResponse(
        submission_id=submission_id,
        candidate_public_id=candidate_public_id,
        assignment_public_id=assignment_public_id,
        state=state,
        transitions=transitions,
        artifacts=artifacts or None,
    )


def _resolve_identity(
    *,
    record: SubmissionRecord | None,
    snapshot: SubmissionSnapshot | None,
) -> tuple[str | None, str | None]:
    if snapshot is not None:
        return snapshot.candidate_public_id, snapshot.assignment_public_id
    if record is not None:
        return record.candidate_public_id, record.assignment_public_id
    return None, None


def _resolve_state(*, record: SubmissionRecord | None, snapshot: SubmissionSnapshot | None) -> str:
    if record is None and snapshot is not None:
        return snapshot.status
    if snapshot is None and record is not None:
        return record.state
    if record is None or snapshot is None:
        return "uploaded"

    record_rank = _STATE_RANK.get(record.state, 0)
    snapshot_rank = _STATE_RANK.get(snapshot.status, 0)
    return record.state if record_rank >= snapshot_rank else snapshot.status


async def _resolve_artifacts(*, deps: ApiDeps, submission_id: str) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for stage in _ARTIFACT_STAGES:
        try:
            artifact_ref = await deps.repository.get_artifact_ref(item_id=submission_id, stage=stage)
        except KeyError:
            continue
        artifacts[stage] = artifact_ref
    return artifacts
