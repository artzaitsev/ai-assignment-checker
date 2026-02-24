from __future__ import annotations

from app.domain.contracts import StorageClient
from app.domain.dto import (
    BuildFeedbackCommand,
    BuildFeedbackResult,
    PrepareExportCommand,
    PrepareExportResult,
)

COMPONENT_ID_FEEDBACK = "domain.feedback.build"
COMPONENT_ID_EXPORT = "domain.export.prepare"


def build_feedback(cmd: BuildFeedbackCommand) -> BuildFeedbackResult:
    """Here you can implement production business logic for domain.feedback.build."""
    return BuildFeedbackResult(feedback_ref=f"feedback/{cmd.submission_id}.json")


def prepare_export(cmd: PrepareExportCommand, *, storage: StorageClient) -> PrepareExportResult:
    """Here you can implement production business logic for domain.export.prepare."""
    export_ref = storage.put_bytes(
        key=f"exports/{cmd.submission_id}.csv",
        payload=b"submission_id,status\n",
    )
    return PrepareExportResult(export_ref=export_ref)
