from __future__ import annotations

from dataclasses import dataclass

from app.domain.evaluation_chain import EvaluationChainSpec
from app.domain.models import SubmissionListItem
from app.lib.artifacts.types import ExportRowArtifact, NormalizedArtifact


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
    normalized_artifact: NormalizedArtifact
    schema_version: str


@dataclass(frozen=True)
class EvaluateSubmissionCommand:
    submission_id: str
    normalized_artifact: NormalizedArtifact
    assignment_title: str
    assignment_description: str
    chain_spec: EvaluationChainSpec


@dataclass(frozen=True)
class LLMClientRequest:
    system_prompt: str
    user_prompt: str
    model: str
    temperature: float
    seed: int | None
    response_language: str


@dataclass(frozen=True)
class LLMClientResult:
    raw_text: str
    raw_json: dict[str, object] | None
    tokens_input: int
    tokens_output: int
    latency_ms: int


@dataclass(frozen=True)
class EvaluateSubmissionResult:
    model: str
    chain_version: str
    response_language: str
    temperature: float
    seed: int | None
    tokens_input: int
    tokens_output: int
    latency_ms: int
    score_1_10: int
    criteria_scores_json: dict[str, object]
    organizer_feedback_json: dict[str, object]
    candidate_feedback_json: dict[str, object]
    ai_assistance_likelihood: float
    ai_assistance_confidence: float
    reproducibility_subset: dict[str, str]


@dataclass(frozen=True)
class BuildFeedbackCommand:
    submission_id: str
    score_1_10: int | None
    summary: str | None


@dataclass(frozen=True)
class BuildFeedbackResult:
    message_text: str


@dataclass(frozen=True)
class PrepareExportCommand:
    items: list[SubmissionListItem]


@dataclass(frozen=True)
class PrepareExportResult:
    export_rows: list[ExportRowArtifact]
