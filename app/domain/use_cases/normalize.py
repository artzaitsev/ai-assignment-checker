from __future__ import annotations

import json

from app.domain.contracts import StorageClient
from app.domain.dto import NormalizePayloadCommand, NormalizePayloadResult
from app.domain.errors import NormalizationParseError
from app.domain.normalization import extension_from_artifact_ref, parse_payload_to_text, to_unified_markdown

COMPONENT_ID = "domain.normalize.payload"
NORMALIZED_SCHEMA_VERSION = "normalized:v1"


def normalize_payload(cmd: NormalizePayloadCommand, *, storage: StorageClient) -> NormalizePayloadResult:

    extension = extension_from_artifact_ref(artifact_ref=cmd.artifact_ref)
    try:
        raw_payload = storage.get_bytes(ref=cmd.artifact_ref)
    except Exception as exc:
        raise NormalizationParseError(str(exc)) from exc

    text = parse_payload_to_text(extension=extension, payload=raw_payload)
    unified_markdown = to_unified_markdown(text=text)

    normalized_doc = {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "submission_id": cmd.submission_id,
        "source": {
            "artifact_ref": cmd.artifact_ref,
            "format": extension.removeprefix("."),
        },
        "content_markdown": unified_markdown,
        "meta": {
            "char_count": len(unified_markdown),
            "line_count": unified_markdown.count("\n") + (1 if unified_markdown else 0),
        },
    }

    normalized_ref = storage.put_bytes(
        key=f"normalized/{cmd.submission_id}.json",
        payload=json.dumps(normalized_doc, ensure_ascii=False).encode("utf-8"),
    )
    return NormalizePayloadResult(
        normalized_ref=normalized_ref,
        schema_version=NORMALIZED_SCHEMA_VERSION,
    )
