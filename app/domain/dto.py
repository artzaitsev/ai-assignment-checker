from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CreateSubmissionCommand:
    source_type: str
    source_external_id: str
    payload_ref: str | None = None


@dataclass(frozen=True)
class CreateSubmissionResult:
    submission_id: str
    state: str


@dataclass(frozen=True)
class TransitionStateCommand:
    submission_id: str
    from_state: str
    to_state: str


@dataclass(frozen=True)
class LinkArtifactCommand:
    submission_id: str
    stage: str
    artifact_ref: str
    artifact_version: str | None = None


@dataclass(frozen=True)
class NormalizePayloadCommand:
    submission_id: str
    artifact_ref: str


@dataclass(frozen=True)
class NormalizePayloadResult:
    normalized_ref: str
    schema_version: str


@dataclass(frozen=True)
class EvaluateSubmissionCommand:
    submission_id: str
    normalized_ref: str
    model_version: str


@dataclass(frozen=True)
class EvaluateSubmissionResult:
    llm_output_ref: str
    feedback_ref: str
    model_version: str


@dataclass(frozen=True)
class BuildFeedbackCommand:
    submission_id: str
    llm_output_ref: str


@dataclass(frozen=True)
class BuildFeedbackResult:
    feedback_ref: str


@dataclass(frozen=True)
class PrepareExportCommand:
    submission_id: str
    feedback_ref: str


@dataclass(frozen=True)
class PrepareExportResult:
    export_ref: str
