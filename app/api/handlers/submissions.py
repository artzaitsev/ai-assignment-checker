from __future__ import annotations

from app.api.handlers.deps import ApiDeps, SubmissionRecord
from app.api.schemas import CreateSubmissionResponse, UploadSubmissionFileResponse
from app.domain.artifacts import put_artifact_ref

COMPONENT_ID = "api.create_submission"


async def create_submission_with_candidate_handler(
    deps: ApiDeps,
    *,
    source_external_id: str,
    candidate_public_id: str,
    assignment_public_id: str,
) -> CreateSubmissionResponse:
    persisted = await deps.repository.create_submission_with_source(
        candidate_public_id=candidate_public_id,
        assignment_public_id=assignment_public_id,
        source_type="api_upload",
        source_external_id=source_external_id,
        initial_status="uploaded",
        metadata_json={"entrypoint": "api"},
    )
    deps.submissions[persisted.submission_id] = SubmissionRecord(
        submission_id=persisted.submission_id,
        state=persisted.status,
        candidate_public_id=candidate_public_id,
        assignment_public_id=assignment_public_id,
        transitions=[persisted.status],
        artifacts={},
    )
    return CreateSubmissionResponse(
        submission_id=persisted.submission_id,
        state=persisted.status,
    )


async def create_submission_with_file_handler(
    deps: ApiDeps,
    *,
    filename: str,
    payload: bytes,
    candidate_public_id: str,
    assignment_public_id: str,
) -> UploadSubmissionFileResponse:
    source_external_id = f"file-{len(deps.submissions) + 1}"
    persisted = await deps.repository.create_submission_with_source(
        candidate_public_id=candidate_public_id,
        assignment_public_id=assignment_public_id,
        source_type="api_upload",
        source_external_id=source_external_id,
        initial_status="uploaded",
        metadata_json={"filename": filename},
    )
    submission_id = persisted.submission_id
    raw_ref = deps.storage.put_bytes(
        key=f"raw/{submission_id}/{filename}",
        payload=payload,
    )
    await deps.repository.link_artifact(
        item_id=submission_id,
        stage="raw",
        artifact_ref=raw_ref,
        artifact_version=None,
    )
    artifacts: dict[str, str] = {}
    put_artifact_ref(artifacts=artifacts, key="raw", artifact_ref=raw_ref)
    deps.submissions[submission_id] = SubmissionRecord(
        submission_id=submission_id,
        state="uploaded",
        candidate_public_id=candidate_public_id,
        assignment_public_id=assignment_public_id,
        transitions=["uploaded"],
        artifacts=artifacts,
    )
    return UploadSubmissionFileResponse(
        submission_id=submission_id,
        state="uploaded",
        artifacts=artifacts,
    )
