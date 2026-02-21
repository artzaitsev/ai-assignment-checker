from __future__ import annotations

from app.domain.dto import BuildFeedbackCommand, PrepareExportCommand
from app.domain.models import ProcessResult, WorkItemClaim
from app.domain.use_cases.deliver import build_feedback, prepare_export
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.deliver.process_claim"


def process_claim(claim: WorkItemClaim, deps: WorkerDeps) -> ProcessResult:
    """Here you can implement production business logic for worker.deliver.process_claim."""
    feedback = build_feedback(
        BuildFeedbackCommand(
            submission_id=claim.item_id,
            llm_output_ref=f"llm-output/{claim.item_id}.json",
        )
    )
    export = prepare_export(
        PrepareExportCommand(submission_id=claim.item_id, feedback_ref=feedback.feedback_ref),
        storage=deps.storage,
    )
    return ProcessResult(
        success=True,
        detail="skeleton deliver handler",
        artifact_ref=export.export_ref,
        artifact_version="v0-skeleton",
    )
