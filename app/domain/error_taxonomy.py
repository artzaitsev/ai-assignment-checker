from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

# Canonical error vocabulary for all stages.
ErrorCode = Literal[
    "validation_error",
    "unsupported_format",
    "telegram_update_invalid",
    "telegram_file_fetch_failed",
    "artifact_missing",
    "llm_provider_unavailable",
    "schema_validation_failed",
    "delivery_transport_failed",
    "internal_error",
]

RetryClassification = Literal["recoverable", "terminal"]

# Allowed persisted values for last_error_code.
CANONICAL_ERROR_CODES: tuple[ErrorCode, ...] = (
    "validation_error",
    "unsupported_format",
    "telegram_update_invalid",
    "telegram_file_fetch_failed",
    "artifact_missing",
    "llm_provider_unavailable",
    "schema_validation_failed",
    "delivery_transport_failed",
    "internal_error",
)

# Errors that can be retried within stage attempt policy.
RECOVERABLE_ERROR_CODES: frozenset[ErrorCode] = frozenset(
    {
        "telegram_file_fetch_failed",
        "artifact_missing",
        "llm_provider_unavailable",
        "delivery_transport_failed",
        "internal_error",
    }
)

# Stage-specific allowlist. If a stage emits a code outside this map,
# it is normalized to internal_error by resolve_stage_error().
STAGE_ERROR_MAP: Mapping[str, frozenset[ErrorCode]] = {
    "raw": frozenset(
        {
            "telegram_update_invalid",
            "telegram_file_fetch_failed",
            "validation_error",
            "internal_error",
        }
    ),
    "normalized": frozenset(
        {
            "unsupported_format",
            "artifact_missing",
            "schema_validation_failed",
            "validation_error",
            "internal_error",
        }
    ),
    "llm-output": frozenset(
        {
            "artifact_missing",
            "llm_provider_unavailable",
            "schema_validation_failed",
            "validation_error",
            "internal_error",
        }
    ),
    "exports": frozenset(
        {
            "artifact_missing",
            "delivery_transport_failed",
            "schema_validation_failed",
            "validation_error",
            "internal_error",
        }
    ),
}


def is_canonical_error_code(code: str) -> bool:
    return code in CANONICAL_ERROR_CODES


def classify_error(code: ErrorCode) -> RetryClassification:
    if code in RECOVERABLE_ERROR_CODES:
        return "recoverable"
    return "terminal"


def resolve_stage_error(*, stage: str, code: str) -> ErrorCode:
    allowed = STAGE_ERROR_MAP.get(stage, frozenset({"internal_error"}))
    if code in allowed and is_canonical_error_code(code):
        return code
    # Keep persistence stable even if upstream emitted unsupported code.
    return "internal_error"
