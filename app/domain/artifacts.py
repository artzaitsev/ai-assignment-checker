from __future__ import annotations

from app.domain.lifecycle import STAGE_LIFECYCLES

ALLOWED_ARTIFACT_KEYS: tuple[str, ...] = (
    "raw",
    "normalized",
    "exports",
)

# Artifact keys are trace labels used by API/status payloads.
# Current MVP stores normalized and exports artifacts in object storage.
STAGE_ARTIFACT_KEYS: dict[str, tuple[str, ...]] = {
    "raw": ("raw",),
    "normalized": ("normalized",),
    "llm-output": tuple(),
    "exports": ("exports",),
}


def artifact_keys_for_stage(*, stage: str) -> tuple[str, ...]:
    if stage not in STAGE_LIFECYCLES:
        raise ValueError(f"unsupported stage: {stage}")
    keys = STAGE_ARTIFACT_KEYS.get(stage)
    if keys is None:
        raise ValueError(f"artifact keys are not configured for stage: {stage}")
    return keys


def put_artifact_ref(*, artifacts: dict[str, str], key: str, artifact_ref: str) -> None:
    if key not in ALLOWED_ARTIFACT_KEYS:
        raise ValueError(f"unsupported artifact key: {key}")
    artifacts[key] = artifact_ref
