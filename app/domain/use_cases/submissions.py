from __future__ import annotations

from app.domain.dto import CreateSubmissionCommand, CreateSubmissionResult

COMPONENT_ID = "domain.submission.create"


def create_submission(cmd: CreateSubmissionCommand) -> CreateSubmissionResult:
    """Here you can implement production business logic for domain.submission.create."""
    submission_id = f"sub-{cmd.source_type}-{cmd.source_external_id}"
    return CreateSubmissionResult(submission_id=submission_id, state="uploaded")
