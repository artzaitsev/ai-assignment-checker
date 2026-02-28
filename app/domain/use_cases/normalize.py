from __future__ import annotations

from pydantic import ValidationError

from app.lib.artifacts.types import NormalizedArtifact
from app.domain.dto import NormalizePayloadCommand, NormalizePayloadResult

COMPONENT_ID = "domain.normalize.payload"


def normalize_payload(cmd: NormalizePayloadCommand) -> NormalizePayloadResult:
    """Build a schema-valid normalized artifact reference and version."""
    try:
        # Contract-validation scaffold: values below are placeholders until
        # real normalization reads assignment/source/content from repositories.
        normalized_artifact = NormalizedArtifact(
            submission_public_id=cmd.submission_id,
            assignment_public_id="asg_00000000000000000000000000",
            source_type="api_upload",
            content_markdown=f"# normalized\n\nsource: {cmd.artifact_ref}",
            normalization_metadata={"producer": COMPONENT_ID},
            schema_version="normalized:v1",
        )
    except ValidationError as exc:
        raise ValueError("normalized artifact schema validation failed") from exc

    # Artifact key convention is fixed by contract; producer may later derive
    # a richer object key (e.g. with attempt/version suffix) without changing prefix.
    return NormalizePayloadResult(
        normalized_artifact=normalized_artifact,
        schema_version="normalized:v1",
    )
