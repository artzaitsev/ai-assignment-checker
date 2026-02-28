from __future__ import annotations

from datetime import UTC, datetime
import secrets

from app.api.handlers.deps import ApiDeps
from app.api.schemas import ExportResultsResponse
from app.domain.dto import PrepareExportCommand
from app.domain.models import SortOrder, SubmissionFieldGroup, SubmissionListQuery, SubmissionSortBy, SubmissionStatus
from app.domain.use_cases.deliver import prepare_export

COMPONENT_ID = "api.export_results"


async def export_results_handler(
    deps: ApiDeps,
    *,
    statuses: tuple[SubmissionStatus, ...] | None,
    candidate_public_id: str | None,
    assignment_public_id: str | None,
    source_type: str | None,
    sort_by: SubmissionSortBy,
    sort_order: SortOrder,
    limit: int,
    offset: int,
) -> ExportResultsResponse:
    """Build and persist CSV export from batch query of evaluated data."""
    items = await deps.repository.list_submissions(
        query=SubmissionListQuery(
            statuses=statuses,
            candidate_public_id=candidate_public_id,
            assignment_public_id=assignment_public_id,
            source_type=source_type,
            include=frozenset(
                {
                    SubmissionFieldGroup.CORE,
                    SubmissionFieldGroup.CANDIDATE,
                    SubmissionFieldGroup.ASSIGNMENT,
                    SubmissionFieldGroup.EVALUATION,
                }
            ),
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
    )
    result = prepare_export(PrepareExportCommand(items=items))
    export_id = _new_export_id()
    export_ref = deps.artifact_repository.save_export_rows(export_id=export_id, rows=result.export_rows)
    return ExportResultsResponse(
        export_id=export_id,
        rows_count=len(result.export_rows),
        download_url=f"/exports/{export_id}/download",
        export_ref=export_ref,
    )


def _new_export_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    random_part = f"{secrets.randbelow(1_000_000):06d}"
    return f"exp_{timestamp}_{random_part}"
