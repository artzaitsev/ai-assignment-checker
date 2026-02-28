from __future__ import annotations

from app.api.handlers.deps import ApiDeps
from app.api.schemas import RunPipelineResponse
from app.domain.artifacts import put_artifact_ref
from app.domain.models import WorkItemClaim
from app.workers.handlers.deps import WorkerDeps
from app.workers.handlers.deliver import process_claim as deliver_process_claim
from app.workers.handlers.evaluate import process_claim as evaluate_process_claim
from app.workers.handlers.normalize import process_claim as normalize_process_claim

COMPONENT_ID = "api.internal.run_pipeline"


def _pipeline_response(*, submission_id: str, state: str, transitions: list[str], artifacts: dict[str, str]) -> RunPipelineResponse:
    """Build a stable API response snapshot from in-memory submission trace."""
    return RunPipelineResponse(
        submission_id=submission_id,
        state=state,
        transitions=list(transitions),
        artifacts=dict(artifacts),
    )


async def run_test_pipeline_handler(deps: ApiDeps, *, submission_id: str) -> RunPipelineResponse | None:
    """Run synthetic normalize->evaluate->deliver pipeline for one submission.

    The handler is intentionally fail-fast: if any stage returns `success=False`,
    execution stops immediately and the final state is set to the corresponding
    `failed_*` status.

    Stage outputs are chained through persistence contracts:
    - normalize writes a normalized artifact
    - evaluate reads normalized artifact and persists evaluation rows
    - deliver reads persisted evaluation and sends notification
    """
    record = deps.submissions.get(submission_id)
    if record is None:
        return None

    # Build worker dependencies once and pass the same context to every stage.
    worker_deps = WorkerDeps(
        repository=deps.repository,
        artifact_repository=deps.artifact_repository,
        storage=deps.storage,
        telegram=deps.telegram,
        llm=deps.llm,
    )

    # 1) Normalize raw submission payload into normalized artifact.
    record.state = "normalization_in_progress"
    record.transitions.append(record.state)
    normalize_result = await normalize_process_claim(
        worker_deps,
        claim=WorkItemClaim(item_id=submission_id, stage="normalized", attempt=1),
    )
    if not normalize_result.success:
        # Fail fast: do not continue to evaluation or delivery.
        record.state = "failed_normalization"
        record.transitions.append(record.state)
        return _pipeline_response(
            submission_id=record.submission_id,
            state=record.state,
            transitions=record.transitions,
            artifacts=record.artifacts,
        )
    if normalize_result.artifact_ref:
        await deps.repository.link_artifact(
            item_id=submission_id,
            stage="normalized",
            artifact_ref=normalize_result.artifact_ref,
            artifact_version=normalize_result.artifact_version,
        )
        # Keep artifact links in API trace for debugging/inspection endpoints.
        put_artifact_ref(
            artifacts=record.artifacts,
            key="normalized",
            artifact_ref=normalize_result.artifact_ref,
        )
    record.state = "normalized"
    record.transitions.append(record.state)

    # 2) Evaluate normalized artifact via LLM and persist evaluation in repository.
    record.state = "evaluation_in_progress"
    record.transitions.append(record.state)
    evaluate_result = await evaluate_process_claim(
        worker_deps,
        claim=WorkItemClaim(item_id=submission_id, stage="llm-output", attempt=1),
    )
    if not evaluate_result.success:
        # Fail fast: delivery depends on persisted evaluation fields.
        record.state = "failed_evaluation"
        record.transitions.append(record.state)
        return _pipeline_response(
            submission_id=record.submission_id,
            state=record.state,
            transitions=record.transitions,
            artifacts=record.artifacts,
        )
    record.state = "evaluated"
    record.transitions.append(record.state)

    # 3) Deliver feedback using data persisted by evaluation stage.
    record.state = "delivery_in_progress"
    record.transitions.append(record.state)
    deliver_result = await deliver_process_claim(
        worker_deps,
        claim=WorkItemClaim(item_id=submission_id, stage="exports", attempt=1),
    )
    if not deliver_result.success:
        # Fail fast: no successful delivery state is recorded.
        record.state = "failed_delivery"
        record.transitions.append(record.state)
        return _pipeline_response(
            submission_id=record.submission_id,
            state=record.state,
            transitions=record.transitions,
            artifacts=record.artifacts,
        )
    if deliver_result.artifact_ref:
        # Delivery may optionally return a produced artifact link.
        put_artifact_ref(
            artifacts=record.artifacts,
            key="exports",
            artifact_ref=deliver_result.artifact_ref,
        )
    record.state = "delivered"
    record.transitions.append(record.state)

    return _pipeline_response(
        submission_id=record.submission_id,
        state=record.state,
        transitions=record.transitions,
        artifacts=record.artifacts,
    )
