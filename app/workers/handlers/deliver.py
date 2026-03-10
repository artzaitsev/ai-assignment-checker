from __future__ import annotations

import logging

from app.clients.telegram import TelegramNonRetryableError, TelegramRetryableError
from app.domain.dto import BuildFeedbackCommand
from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.models import (
    CandidateSourceType,
    ProcessResult,
    SortOrder,
    SubmissionFieldGroup,
    SubmissionListQuery,
    WorkItemClaim,
)
from app.domain.use_cases.deliver import build_feedback
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.deliver.process_claim"
logger = logging.getLogger("runtime")


async def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    """Process delivery stage by sending result notification."""
    try:
        items = await deps.repository.list_submissions(
            query=SubmissionListQuery(
                submission_ids=(claim.item_id,),
                include=frozenset(
                    {
                        SubmissionFieldGroup.CORE,
                        SubmissionFieldGroup.CANDIDATE,
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
        candidate_feedback = evaluation.candidate_feedback if evaluation else None
        summary = candidate_feedback.summary if candidate_feedback is not None else None

        feedback = build_feedback(
            BuildFeedbackCommand(
                submission_id=claim.item_id,
                score_1_10=evaluation.score_1_10 if evaluation else None,
                summary=summary,
            )
        )

        if item.candidate is None:
            raise KeyError(f"candidate is required for delivery: {claim.item_id}")

        chat_id = await deps.repository.find_candidate_source_external_id(
            candidate_public_id=item.candidate.public_id,
            source_type=CandidateSourceType.TELEGRAM_CHAT,
        )
        if chat_id is None:
            await deps.repository.persist_delivery(
                submission_id=claim.item_id,
                channel="telegram",
                status="skipped",
                attempts=claim.attempt,
            )
            logger.info(
                "delivery skipped: candidate has no telegram chat mapping",
                extra={"component": COMPONENT_ID, "submission_id": claim.item_id},
            )
            return ProcessResult(
                success=True,
                detail="delivery skipped: candidate has no telegram chat mapping",
            )

        result_link = _build_result_link(deps=deps, submission_id=claim.item_id)
        message_text = feedback.message_text
        if result_link is not None:
            message_text = (
                f"{feedback.message_text}\n\n"
                f"Результат проверки: {result_link}"
            )

        try:
            external_message_id = deps.telegram.send_text(
                chat_id=chat_id,
                message=message_text,
            )
        except TelegramRetryableError as exc:
            error_code = resolve_stage_error(stage="exports", code="delivery_transport_failed")
            await _persist_failed_delivery(
                deps,
                claim=claim,
                error_code=error_code,
            )
            return ProcessResult(
                success=False,
                detail=str(exc),
                error_code=error_code,
                retry_classification=classify_error(error_code),
            )
        except TelegramNonRetryableError as exc:
            error_code = resolve_stage_error(stage="exports", code="validation_error")
            await _persist_failed_delivery(
                deps,
                claim=claim,
                error_code=error_code,
            )
            return ProcessResult(
                success=False,
                detail=str(exc),
                error_code=error_code,
                retry_classification=classify_error(error_code),
            )
        except Exception as exc:  # pragma: no cover - concrete client behavior
            error_code = resolve_stage_error(stage="exports", code="delivery_transport_failed")
            await _persist_failed_delivery(
                deps,
                claim=claim,
                error_code=error_code,
            )
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
            attempts=claim.attempt,
        )
    except KeyError as exc:
        error_code = resolve_stage_error(stage="exports", code="artifact_missing")
        await _persist_failed_delivery(
            deps,
            claim=claim,
            error_code=error_code,
        )
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except ValueError as exc:
        error_code = resolve_stage_error(stage="exports", code="schema_validation_failed")
        await _persist_failed_delivery(
            deps,
            claim=claim,
            error_code=error_code,
        )
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


async def _persist_failed_delivery(
    deps: WorkerDeps,
    *,
    claim: WorkItemClaim,
    error_code: str,
) -> None:
    await deps.repository.persist_delivery(
        submission_id=claim.item_id,
        channel="telegram",
        status="failed",
        attempts=claim.attempt,
        last_error_code=error_code,
    )


def _build_result_link(*, deps: WorkerDeps, submission_id: str) -> str | None:
    if deps.telegram_link_settings is None:
        return None
    base_url = deps.telegram_link_settings.public_web_base_url.strip().rstrip("/")
    if not base_url:
        return None
    return f"{base_url}/candidate/apply/result/{submission_id}"
