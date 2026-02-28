from __future__ import annotations

from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.dto import BuildFeedbackCommand
from app.domain.models import SortOrder, SubmissionFieldGroup, SubmissionListQuery
from app.domain.models import ProcessResult, WorkItemClaim
from app.domain.use_cases.deliver import build_feedback
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.deliver.process_claim"


async def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    """Process delivery stage by sending result notification."""
    try:
        items = await deps.repository.list_submissions(
            query=SubmissionListQuery(
                submission_ids=(claim.item_id,),
                include=frozenset(
                    {
                        SubmissionFieldGroup.CORE,
                        SubmissionFieldGroup.EVALUATION,
                    }
                ),
                sort_order=SortOrder.ASC,
                limit=1,
            )
        )
        if not items:
            raise KeyError(f"submission not found for delivery: {claim.item_id}")

        item = items[0]
        evaluation = item.evaluation
        candidate_feedback = evaluation.candidate_feedback_json if evaluation else {}
        summary_value = candidate_feedback.get("summary") if isinstance(candidate_feedback, dict) else None
        summary = str(summary_value) if summary_value is not None else None

        feedback = build_feedback(
            BuildFeedbackCommand(
                submission_id=claim.item_id,
                score_1_10=evaluation.score_1_10 if evaluation else None,
                summary=summary,
            )
        )

        try:
            external_message_id = deps.telegram.send_result_notification(
                submission_id=claim.item_id,
                message=feedback.message_text,
            )
        except Exception as exc:  # pragma: no cover - concrete client behavior
            error_code = resolve_stage_error(stage="exports", code="delivery_transport_failed")
            return ProcessResult(
                success=False,
                detail=str(exc),
                error_code=error_code,
                retry_classification=classify_error(error_code),
            )

        await deps.repository.persist_delivery(
            submission_id=claim.item_id,
            channel="telegram",
            status="sent",
            external_message_id=external_message_id,
            attempts=1,
        )
    except KeyError as exc:
        error_code = resolve_stage_error(stage="exports", code="artifact_missing")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except ValueError as exc:
        error_code = resolve_stage_error(stage="exports", code="schema_validation_failed")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )

    return ProcessResult(
        success=True,
        detail="delivery notification sent",
    )
