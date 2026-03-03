from __future__ import annotations

from pydantic import ValidationError

from app.domain.dto import NormalizePayloadCommand, NormalizePayloadResult
from app.domain.normalization import extension_from_artifact_ref, parse_payload_to_text, to_unified_markdown
from app.lib.artifacts.types import NormalizedArtifact

COMPONENT_ID = "domain.normalize.payload"
NORMALIZED_SCHEMA_VERSION = "normalized:v1"
_DEFAULT_ASSIGNMENT_PUBLIC_ID = "asg_00000000000000000000000000"


def normalize_payload(cmd: NormalizePayloadCommand, *, raw_payload: bytes) -> NormalizePayloadResult:
    """Convert raw payload to a schema-valid normalized artifact (contract v1)."""
    extension = extension_from_artifact_ref(artifact_ref=cmd.artifact_ref)
    text = parse_payload_to_text(extension=extension, payload=raw_payload)
    unified_markdown = to_unified_markdown(text=text)

    source_type = cmd.source_type or "api_upload"
    if source_type not in ("api_upload", "telegram_webhook"):
        source_type = "api_upload"
    assignment_public_id = cmd.assignment_public_id or _DEFAULT_ASSIGNMENT_PUBLIC_ID

    try:
        normalized_artifact = NormalizedArtifact(
            submission_public_id=cmd.submission_id,
            assignment_public_id=assignment_public_id,
            source_type=source_type,
            content_markdown=unified_markdown,
            normalization_metadata={
                "producer": COMPONENT_ID,
                "source_artifact_ref": cmd.artifact_ref,
                "source_format": extension.removeprefix("."),
                "char_count": len(unified_markdown),
                "line_count": unified_markdown.count("\n") + (1 if unified_markdown else 0),
            },
            schema_version=NORMALIZED_SCHEMA_VERSION,
        )
    except ValidationError as exc:
        raise ValueError("normalized artifact schema validation failed") from exc

    return NormalizePayloadResult(
        normalized_artifact=normalized_artifact,
        schema_version=NORMALIZED_SCHEMA_VERSION,
    )
