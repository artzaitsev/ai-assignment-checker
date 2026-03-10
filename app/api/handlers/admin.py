from __future__ import annotations

from app.api.handlers.deps import ApiDeps
from app.api.handlers.exports import export_results_handler
from app.api.schemas import ExportResultsResponse
from app.domain.models import (
    SortOrder,
    SubmissionFieldGroup,
    SubmissionListItem,
    SubmissionListQuery,
    SubmissionSortBy,
    SubmissionStatus,
)


async def list_admin_submissions_handler(
    deps: ApiDeps,
    *,
    status: SubmissionStatus | None,
    candidate_public_id: str | None,
    assignment_public_id: str | None,
    sort_by: SubmissionSortBy,
    sort_order: SortOrder,
    limit: int,
    offset: int,
) -> list[SubmissionListItem]:
    statuses = (status,) if status is not None else None
    return await deps.repository.list_submissions(
        query=SubmissionListQuery(
            statuses=statuses,
            candidate_public_id=candidate_public_id,
            assignment_public_id=assignment_public_id,
            include=frozenset(
                {
                    SubmissionFieldGroup.CORE,
                    SubmissionFieldGroup.CANDIDATE,
                    SubmissionFieldGroup.ASSIGNMENT,
                    SubmissionFieldGroup.EVALUATION,
                    SubmissionFieldGroup.OPS,
                }
            ),
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    )


async def get_admin_submission_detail_handler(
    deps: ApiDeps,
    *,
    submission_id: str,
) -> SubmissionListItem | None:
    items = await deps.repository.list_submissions(
        query=SubmissionListQuery(
            submission_ids=(submission_id,),
            include=frozenset(
                {
                    SubmissionFieldGroup.CORE,
                    SubmissionFieldGroup.CANDIDATE,
                    SubmissionFieldGroup.ASSIGNMENT,
                    SubmissionFieldGroup.SOURCE,
                    SubmissionFieldGroup.EVALUATION,
                    SubmissionFieldGroup.OPS,
                }
            ),
            sort_order=SortOrder.ASC,
            limit=1,
            offset=0,
        )
    )
    if not items:
        return None
    return items[0]


async def create_admin_export_handler(
    deps: ApiDeps,
    *,
    status: SubmissionStatus | None,
    candidate_public_id: str | None,
    assignment_public_id: str | None,
    sort_by: SubmissionSortBy,
    sort_order: SortOrder,
    limit: int,
    offset: int,
) -> ExportResultsResponse:
    statuses = (status,) if status is not None else None
    return await export_results_handler(
        deps,
        statuses=statuses,
        candidate_public_id=candidate_public_id,
        assignment_public_id=assignment_public_id,
        source_type=None,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
