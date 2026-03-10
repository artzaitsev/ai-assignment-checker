from __future__ import annotations

from app.api.handlers.deps import ApiDeps
from app.api.schemas import FeedbackListResponse
from app.domain.models import SortOrder, SubmissionFieldGroup, SubmissionListQuery

COMPONENT_ID = "api.list_feedback"


async def list_feedback_handler(
    deps: ApiDeps,
    *,
    submission_id: str | None = None,
) -> FeedbackListResponse:
    items = await deps.repository.list_submissions(
        query=SubmissionListQuery(
            submission_ids=(submission_id,) if submission_id is not None else None,
            include=frozenset({SubmissionFieldGroup.CORE, SubmissionFieldGroup.EVALUATION}),
            sort_order=SortOrder.DESC,
            limit=1000,
            offset=0,
        )
    )

    response_items: list[dict[str, object]] = []
    for item in items:
        evaluation = item.evaluation
        if evaluation is None:
            continue

        response_items.append(
            {
                "submission_id": item.core.public_id,
                "score_1_10": evaluation.score_1_10,
                "organizer_feedback": evaluation.organizer_feedback.to_dict() if evaluation.organizer_feedback is not None else None,
                "candidate_feedback": evaluation.candidate_feedback.to_dict() if evaluation.candidate_feedback is not None else None,
                "ai_assistance": {
                    "likelihood": evaluation.ai_assistance_likelihood,
                    "confidence": evaluation.ai_assistance_confidence,
                },
            }
        )

    return FeedbackListResponse(items=response_items)
