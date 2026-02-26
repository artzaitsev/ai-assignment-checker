from __future__ import annotations

from app.api.handlers.deps import ApiDeps, SubmissionRecord
from app.api.schemas import CreateSubmissionResponse, UploadSubmissionFileResponse
from app.domain.artifacts import put_artifact_ref

COMPONENT_ID = "api.create_submission"


async def create_submission_with_candidate_handler(
    *,
    source_external_id: str,
    candidate_public_id: str,
    assignment_public_id: str,
    api_deps: ApiDeps,
) -> CreateSubmissionResponse:
    persisted = await api_deps.repository.create_submission_with_source(
        candidate_public_id=candidate_public_id,
        assignment_public_id=assignment_public_id,
        source_type="api_upload",
        source_external_id=source_external_id,
        initial_status="uploaded",
        metadata_json={"entrypoint": "api"},
    )
    api_deps.submissions[persisted.submission_id] = SubmissionRecord(
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
    *,
    filename: str,
    payload: bytes,
    candidate_public_id: str,
    assignment_public_id: str,
    api_deps: ApiDeps,
) -> UploadSubmissionFileResponse:
    source_external_id = f"file-{len(api_deps.submissions) + 1}"
    persisted = await api_deps.repository.create_submission_with_source(
        candidate_public_id=candidate_public_id,
        assignment_public_id=assignment_public_id,
        source_type="api_upload",
        source_external_id=source_external_id,
        initial_status="uploaded",
        metadata_json={"filename": filename},
    )
    submission_id = persisted.submission_id
    raw_ref = api_deps.storage.put_bytes(
        key=f"raw/{submission_id}/{filename}",
        payload=payload,
    )
    await api_deps.repository.link_artifact(
        item_id=submission_id,
        stage="raw",
        artifact_ref=raw_ref,
        artifact_version="raw:v1",
    )

    artifacts: dict[str, str] = {}
    put_artifact_ref(artifacts=artifacts, key="raw", artifact_ref=raw_ref)
    api_deps.submissions[submission_id] = SubmissionRecord(
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
