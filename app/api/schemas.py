from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.evaluation_contracts import (
    TaskSchema,
    TaskSchemaCriterion,
    TaskSchemaTask,
    parse_task_schema,
    validate_language_code,
)
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


class TaskSchemaCriterionPayload(BaseModel):
    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    weight: float = Field(gt=0)

    def to_domain(self) -> TaskSchemaCriterion:
        return TaskSchemaCriterion(
            criterion_id=self.criterion_id,
            description=self.description,
            weight=self.weight,
        )

    @classmethod
    def from_domain(cls, criterion: TaskSchemaCriterion) -> TaskSchemaCriterionPayload:
        return cls(
            criterion_id=criterion.criterion_id,
            description=criterion.description,
            weight=criterion.weight,
        )


class TaskSchemaTaskPayload(BaseModel):
    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    weight: float = Field(gt=0)
    criteria: list[TaskSchemaCriterionPayload]

    def to_domain(self) -> TaskSchemaTask:
        return TaskSchemaTask(
            task_id=self.task_id,
            title=self.title,
            weight=self.weight,
            criteria=tuple(criterion.to_domain() for criterion in self.criteria),
        )

    @classmethod
    def from_domain(cls, task: TaskSchemaTask) -> TaskSchemaTaskPayload:
        return cls(
            task_id=task.task_id,
            title=task.title,
            weight=task.weight,
            criteria=[TaskSchemaCriterionPayload.from_domain(criterion) for criterion in task.criteria],
        )


class TaskSchemaPayload(BaseModel):
    schema_version: str = Field(min_length=1)
    tasks: list[TaskSchemaTaskPayload]

    @model_validator(mode="after")
    def _validate_structure(self) -> TaskSchemaPayload:
        parse_task_schema(self.model_dump())
        return self

    def to_domain(self) -> TaskSchema:
        return TaskSchema(
            schema_version=self.schema_version,
            tasks=tuple(task.to_domain() for task in self.tasks),
        )

    @classmethod
    def from_domain(cls, task_schema: TaskSchema) -> TaskSchemaPayload:
        return cls(
            schema_version=task_schema.schema_version,
            tasks=[TaskSchemaTaskPayload.from_domain(task) for task in task_schema.tasks],
        )


class CreateAssignmentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1)
    language: str = Field(min_length=2, max_length=8)
    task_schema: TaskSchemaPayload
    is_active: bool = True

    @field_validator("language")
    @classmethod
    def _validate_language(cls, value: str) -> str:
        validate_language_code(value)
        return value


class AssignmentResponse(BaseModel):
    assignment_public_id: str = Field(pattern=ASSIGNMENT_ID_PATTERN)
    title: str
    description: str
    language: str
    is_active: bool
    task_schema: TaskSchemaPayload | None = None


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


class RunPipelineResponse(BaseModel):
    submission_id: str = Field(pattern=SUBMISSION_ID_PATTERN)
    state: str
    transitions: list[str]
    artifacts: dict[str, str]
