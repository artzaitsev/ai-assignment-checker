from __future__ import annotations

from app.api.handlers.deps import ApiDeps
from app.domain.models import WorkItemClaim
from app.workers.handlers.deps import WorkerDeps
from app.workers.handlers.deliver import process_claim as deliver_process_claim
from app.workers.handlers.evaluate import process_claim as evaluate_process_claim
from app.workers.handlers.normalize import process_claim as normalize_process_claim

COMPONENT_ID = "api.internal.run_pipeline"


async def run_test_pipeline_handler(*, submission_id: str, api_deps: ApiDeps) -> dict[str, object] | None:
    """Here you can implement production business logic for api.internal.run_pipeline."""
    record = api_deps.submissions.get(submission_id)
    if record is None:
        return None

    worker_deps = WorkerDeps(storage=api_deps.storage, telegram=api_deps.telegram, llm=api_deps.llm)

    record.state = "normalization_in_progress"
    record.transitions.append(record.state)
    normalize_result = normalize_process_claim(
        WorkItemClaim(item_id=submission_id, stage="normalized", attempt=1),
        worker_deps,
    )
    if normalize_result.artifact_ref:
        record.artifacts["normalized"] = normalize_result.artifact_ref
    record.state = "normalized"
    record.transitions.append(record.state)

    record.state = "evaluation_in_progress"
    record.transitions.append(record.state)
    evaluate_result = evaluate_process_claim(
        WorkItemClaim(item_id=submission_id, stage="llm-output", attempt=1),
        worker_deps,
    )
    if evaluate_result.artifact_ref:
        record.artifacts["llm-output"] = evaluate_result.artifact_ref
    record.artifacts["feedback"] = f"stub://feedback/{submission_id}.json"
    record.state = "evaluated"
    record.transitions.append(record.state)

    record.state = "delivery_in_progress"
    record.transitions.append(record.state)
    deliver_result = deliver_process_claim(
        WorkItemClaim(item_id=submission_id, stage="exports", attempt=1),
        worker_deps,
    )
    if deliver_result.artifact_ref:
        record.artifacts["exports"] = deliver_result.artifact_ref
    record.state = "delivered"
    record.transitions.append(record.state)

    return {
        "submission_id": record.submission_id,
        "state": record.state,
        "transitions": list(record.transitions),
        "artifacts": dict(record.artifacts),
    }
