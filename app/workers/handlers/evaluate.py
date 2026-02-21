from __future__ import annotations

from app.domain.dto import EvaluateSubmissionCommand
from app.domain.models import ProcessResult, WorkItemClaim
from app.domain.use_cases.llm_eval import evaluate_submission
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.evaluate.process_claim"


def process_claim(claim: WorkItemClaim, deps: WorkerDeps) -> ProcessResult:
    """Here you can implement production business logic for worker.evaluate.process_claim."""
    result = evaluate_submission(
        EvaluateSubmissionCommand(
            submission_id=claim.item_id,
            normalized_ref=f"normalized/{claim.item_id}.json",
            model_version="model:v0-skeleton",
        ),
        llm=deps.llm,
        storage=deps.storage,
    )
    return ProcessResult(
        success=True,
        detail="skeleton evaluate handler",
        artifact_ref=result.llm_output_ref,
        artifact_version=result.model_version,
    )
