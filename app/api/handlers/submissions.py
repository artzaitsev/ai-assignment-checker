from __future__ import annotations

from app.api.handlers.deps import ApiDeps, SubmissionRecord
from app.domain.dto import CreateSubmissionCommand
from app.domain.use_cases.submissions import create_submission

COMPONENT_ID = "api.create_submission"


async def create_submission_handler(source_external_id: str) -> dict[str, str]:
    """Here you can implement production business logic for api.create_submission."""
    result = create_submission(
        CreateSubmissionCommand(
            source_type="api_upload",
            source_external_id=source_external_id,
        )
    )
    return {
        "submission_id": result.submission_id,
        "state": result.state,
    }


async def create_submission_with_file_handler(
    *,
    filename: str,
    payload: bytes,
    api_deps: ApiDeps,
) -> dict[str, object]:
    """Here you can implement production business logic for api.create_submission."""
    source_external_id = f"file-{len(api_deps.submissions) + 1}"
    result = create_submission(
        CreateSubmissionCommand(
            source_type="api_upload",
            source_external_id=source_external_id,
        )
    )
    raw_ref = api_deps.storage.put_bytes(
        key=f"raw/{result.submission_id}/{filename}",
        payload=payload,
    )
    api_deps.submissions[result.submission_id] = SubmissionRecord(
        submission_id=result.submission_id,
        state=result.state,
        transitions=[result.state],
        artifacts={"raw": raw_ref},
    )
    return {
        "submission_id": result.submission_id,
        "state": result.state,
        "artifacts": {"raw": raw_ref},
    }
