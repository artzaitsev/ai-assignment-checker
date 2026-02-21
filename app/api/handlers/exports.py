from __future__ import annotations

from app.domain.contracts import StorageClient
from app.domain.dto import PrepareExportCommand
from app.domain.use_cases.deliver import prepare_export

COMPONENT_ID = "api.export_results"


async def export_results_handler(
    *,
    submission_id: str,
    feedback_ref: str,
    storage: StorageClient,
) -> dict[str, str]:
    """Here you can implement production business logic for api.export_results."""
    result = prepare_export(
        PrepareExportCommand(submission_id=submission_id, feedback_ref=feedback_ref),
        storage=storage,
    )
    return {
        "submission_id": submission_id,
        "export_ref": result.export_ref,
    }
