from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.models import SubmissionStatus


CANDIDATE_ID_PATTERN = r"^cand_[0-9A-HJKMNP-TV-Z]{26}$"
ASSIGNMENT_ID_PATTERN = r"^asg_[0-9A-HJKMNP-TV-Z]{26}$"
SUBMISSION_ID_PATTERN = r"^sub_[0-9A-HJKMNP-TV-Z]{26}$"
EXPORT_ID_PATTERN = r"^exp_[0-9]{14}_[0-9]{6}$"


class ErrorResponse(BaseModel):
    detail: str


class WorkerMetrics(BaseModel):
    started: bool
    stopped: bool
    ticks_total: int
    claims_total: int
    idle_ticks_total: int
    errors_total: int


class HealthResponse(BaseModel):
    status: str
    role: str
    mode: str


class ReadyResponse(BaseModel):
    status: str
    role: str
    mode: str
    worker_loop_enabled: bool
    worker_loop_ready: bool
    worker_metrics: WorkerMetrics


class CreateCandidateRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=128)
    last_name: str = Field(min_length=1, max_length=128)
    source_type: str | None = Field(default=None, min_length=1, max_length=64)
    source_external_id: str | None = Field(default=None, min_length=1, max_length=256)


class CandidateResponse(BaseModel):
    candidate_public_id: str = Field(pattern=CANDIDATE_ID_PATTERN)
    first_name: str
    last_name: str


class CreateAssignmentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1)
    is_active: bool = True


class AssignmentResponse(BaseModel):
    assignment_public_id: str = Field(pattern=ASSIGNMENT_ID_PATTERN)
    title: str
    description: str
    is_active: bool


class ListAssignmentsResponse(BaseModel):
    items: list[AssignmentResponse]


class CreateSubmissionRequest(BaseModel):
    source_external_id: str = Field(min_length=1, max_length=256)
    candidate_public_id: str = Field(pattern=CANDIDATE_ID_PATTERN)
    assignment_public_id: str = Field(pattern=ASSIGNMENT_ID_PATTERN)


class CreateSubmissionResponse(BaseModel):
    submission_id: str = Field(pattern=SUBMISSION_ID_PATTERN)
    state: str


class UploadSubmissionFileResponse(BaseModel):
    submission_id: str = Field(pattern=SUBMISSION_ID_PATTERN)
    state: str
    artifacts: dict[str, str]


class SubmissionStatusResponse(BaseModel):
    submission_id: str
    candidate_public_id: str | None = Field(default=None, pattern=CANDIDATE_ID_PATTERN)
    assignment_public_id: str | None = Field(default=None, pattern=ASSIGNMENT_ID_PATTERN)
    state: str
    transitions: list[str] | None = None
    artifacts: dict[str, str] | None = None


class FeedbackListResponse(BaseModel):
    items: list[dict[str, object]]


class ExportResultsRequest(BaseModel):
    statuses: list[SubmissionStatus] | None = None
    candidate_public_id: str | None = Field(default=None, pattern=CANDIDATE_ID_PATTERN)
    assignment_public_id: str | None = Field(default=None, pattern=ASSIGNMENT_ID_PATTERN)
    source_type: str | None = None
    sort_by: Literal["created_at", "updated_at", "score_1_10", "status"] = "created_at"
    sort_order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class ExportResultsResponse(BaseModel):
    export_id: str = Field(pattern=EXPORT_ID_PATTERN)
    rows_count: int = Field(ge=0)
    download_url: str
    export_ref: str


class TelegramWebhookRequest(BaseModel):
    update_id: str = Field(min_length=1, max_length=128)
    candidate_public_id: str = Field(pattern=CANDIDATE_ID_PATTERN)
    assignment_public_id: str = Field(pattern=ASSIGNMENT_ID_PATTERN)
    file_id: str = Field(min_length=1, max_length=256)
    file_name: str | None = Field(default=None, min_length=1, max_length=256)


class TelegramWebhookResponse(BaseModel):
    submission_id: str = Field(pattern=SUBMISSION_ID_PATTERN)
    state: str
    created: bool


class RunPipelineResponse(BaseModel):
    submission_id: str = Field(pattern=SUBMISSION_ID_PATTERN)
    state: str
    transitions: list[str]
    artifacts: dict[str, str]
