from __future__ import annotations

from app.domain.dto import NormalizePayloadCommand, NormalizePayloadResult

COMPONENT_ID = "domain.normalize.payload"


def normalize_payload(cmd: NormalizePayloadCommand) -> NormalizePayloadResult:
    """Here you can implement production business logic for domain.normalize.payload."""
    return NormalizePayloadResult(
        normalized_ref=f"normalized/{cmd.submission_id}.json",
        schema_version="schema:v0-skeleton",
    )
