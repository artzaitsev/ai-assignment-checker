from __future__ import annotations

from pydantic import BaseModel, Field


CANDIDATE_ID_PATTERN = r"^cand_[0-9A-HJKMNP-TV-Z]{26}$"
ASSIGNMENT_ID_PATTERN = r"^asg_[0-9A-HJKMNP-TV-Z]{26}$"
SUBMISSION_ID_PATTERN = r"^sub_[0-9A-HJKMNP-TV-Z]{26}$"


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
    submission_id: str
    feedback_ref: str


class ExportResultsResponse(BaseModel):
    submission_id: str
    export_ref: str


class RunPipelineResponse(BaseModel):
    submission_id: str = Field(pattern=SUBMISSION_ID_PATTERN)
    state: str
    transitions: list[str]
    artifacts: dict[str, str]
