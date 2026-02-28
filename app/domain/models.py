from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.error_taxonomy import ErrorCode, RetryClassification


@dataclass(frozen=True)
class WorkItemClaim:
    item_id: str
    stage: str
    attempt: int
    lease_expires_at: datetime | None = None


@dataclass(frozen=True)
class ProcessResult:
    success: bool
    detail: str = ""
    artifact_ref: str | None = None
    artifact_version: str | None = None
    error_code: ErrorCode | None = None
    retry_classification: RetryClassification | None = None


@dataclass(frozen=True)
class SubmissionSnapshot:
    submission_id: str
    candidate_public_id: str
    assignment_public_id: str
    status: str
    attempt_telegram_ingest: int
    attempt_normalization: int
    attempt_evaluation: int
    attempt_delivery: int
    claimed_by: str | None
    claimed_at: datetime | None
    lease_expires_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class SubmissionSourceSnapshot:
    submission_id: str
    source_type: str
    source_external_id: str
    metadata_json: dict[str, object]


@dataclass(frozen=True)
class UpsertSourceResult:
    submission_id: str
    status: str
    created: bool


@dataclass(frozen=True)
class CandidateSnapshot:
    candidate_public_id: str
    first_name: str
    last_name: str


@dataclass(frozen=True)
class AssignmentSnapshot:
    assignment_public_id: str
    title: str
    description: str
    is_active: bool


class SubmissionFieldGroup(StrEnum):
    CORE = "core"
    CANDIDATE = "candidate"
    ASSIGNMENT = "assignment"
    SOURCE = "source"
    EVALUATION = "evaluation"
    OPS = "ops"


# Canonical submission lifecycle states.
#
# IMPORTANT:
# - Keep this enum synchronized with app/domain/lifecycle.py
#   (STAGE_LIFECYCLES and ALLOWED_TRANSITIONS).
# - Keep this enum synchronized with the DB status CHECK constraint in
#   db/migrations/000001_bootstrap.up.sql.
# - Any status add/remove/rename must be done atomically across all these files.
class SubmissionStatus(StrEnum):
    # Ingress states.
    TELEGRAM_UPDATE_RECEIVED = "telegram_update_received"

    # In-progress states.
    TELEGRAM_INGEST_IN_PROGRESS = "telegram_ingest_in_progress"
    NORMALIZATION_IN_PROGRESS = "normalization_in_progress"
    EVALUATION_IN_PROGRESS = "evaluation_in_progress"
    DELIVERY_IN_PROGRESS = "delivery_in_progress"

    # Success states.
    UPLOADED = "uploaded"
    NORMALIZED = "normalized"
    EVALUATED = "evaluated"
    DELIVERED = "delivered"

    # Failure states.
    FAILED_TELEGRAM_INGEST = "failed_telegram_ingest"
    FAILED_NORMALIZATION = "failed_normalization"
    FAILED_EVALUATION = "failed_evaluation"
    FAILED_DELIVERY = "failed_delivery"

    # Terminal state.
    DEAD_LETTER = "dead_letter"


class SubmissionSortBy(StrEnum):
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    SCORE_1_10 = "score_1_10"
    STATUS = "status"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True)
class SubmissionListQuery:
    statuses: tuple[SubmissionStatus, ...] | None = None
    submission_ids: tuple[str, ...] | None = None
    candidate_public_id: str | None = None
    assignment_public_id: str | None = None
    source_type: str | None = None
    has_error: bool | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    include: frozenset[SubmissionFieldGroup] = frozenset({SubmissionFieldGroup.CORE})
    sort_by: SubmissionSortBy = SubmissionSortBy.CREATED_AT
    sort_order: SortOrder = SortOrder.DESC
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True)
class SubmissionListItem:
    @dataclass(frozen=True)
    class Core:
        public_id: str
        status: str
        created_at: datetime
        updated_at: datetime

    @dataclass(frozen=True)
    class Candidate:
        public_id: str

    @dataclass(frozen=True)
    class Assignment:
        public_id: str

    @dataclass(frozen=True)
    class Source:
        type: str
        external_id: str

    @dataclass(frozen=True)
    class Evaluation:
        score_1_10: int | None
        criteria_scores_json: dict[str, object] | None
        organizer_feedback_json: dict[str, object] | None
        candidate_feedback_json: dict[str, object] | None
        chain_version: str | None
        model: str | None
        spec_version: str | None
        response_language: str | None

    @dataclass(frozen=True)
    class Ops:
        last_error_code: str | None
        last_error_message: str | None

    id: int
    core: Core
    candidate: Candidate | None = None
    assignment: Assignment | None = None
    source: Source | None = None
    evaluation: Evaluation | None = None
    ops: Ops | None = None
