from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
    error_code: str | None = None


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


