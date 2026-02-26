from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain.errors import DomainInvariantError
from app.domain.ids import new_assignment_public_id, new_candidate_public_id, new_submission_public_id
from app.domain.lifecycle import ALLOWED_TRANSITIONS, STAGE_LIFECYCLES
from app.domain.models import (
    AssignmentSnapshot,
    CandidateSnapshot,
    SubmissionSnapshot,
    SubmissionSourceSnapshot,
    UpsertSourceResult,
    WorkItemClaim,
)


@dataclass
class _CandidateRow:
    candidate_public_id: str
    first_name: str
    last_name: str


@dataclass
class _AssignmentRow:
    assignment_public_id: str
    title: str
    description: str
    is_active: bool


@dataclass
class _SubmissionRow:
    submission_id: str
    candidate_public_id: str
    assignment_public_id: str
    status: str
    attempt_telegram_ingest: int = 0
    attempt_normalization: int = 0
    attempt_evaluation: int = 0
    attempt_delivery: int = 0
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    lease_expires_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None


@dataclass
class InMemoryWorkRepository:
    """Non-network repository with deterministic behavior for skeleton mode."""

    queue: list[WorkItemClaim] = field(default_factory=list)
    transitions: list[tuple[str, str, str]] = field(default_factory=list)
    artifacts: list[tuple[str, str, str, str | None]] = field(default_factory=list)
    finalizations: list[tuple[str, str, bool, str]] = field(default_factory=list)
    submissions: dict[str, _SubmissionRow] = field(default_factory=dict)
    sources: dict[tuple[str, str], SubmissionSourceSnapshot] = field(default_factory=dict)
    candidates: dict[str, _CandidateRow] = field(default_factory=dict)
    candidate_sources: dict[tuple[str, str], str] = field(default_factory=dict)
    assignments: dict[str, _AssignmentRow] = field(default_factory=dict)

    async def create_candidate(self, *, first_name: str, last_name: str) -> CandidateSnapshot:
        candidate_public_id = new_candidate_public_id()
        row = _CandidateRow(
            candidate_public_id=candidate_public_id,
            first_name=first_name,
            last_name=last_name,
        )
        self.candidates[candidate_public_id] = row
        return CandidateSnapshot(
            candidate_public_id=row.candidate_public_id,
            first_name=row.first_name,
            last_name=row.last_name,
        )

    async def get_or_create_candidate_by_source(
        self,
        *,
        source_type: str,
        source_external_id: str,
        first_name: str,
        last_name: str,
        metadata_json: dict[str, object] | None = None,
    ) -> CandidateSnapshot:
        del metadata_json
        key = (source_type, source_external_id)
        existing = self.candidate_sources.get(key)
        if existing is not None:
            row = self.candidates[existing]
            return CandidateSnapshot(
                candidate_public_id=row.candidate_public_id,
                first_name=row.first_name,
                last_name=row.last_name,
            )

        created = await self.create_candidate(first_name=first_name, last_name=last_name)
        self.candidate_sources[key] = created.candidate_public_id
        return created

    async def create_assignment(
        self,
        *,
        title: str,
        description: str,
        is_active: bool = True,
    ) -> AssignmentSnapshot:
        assignment_public_id = new_assignment_public_id()
        row = _AssignmentRow(
            assignment_public_id=assignment_public_id,
            title=title,
            description=description,
            is_active=is_active,
        )
        self.assignments[assignment_public_id] = row
        return AssignmentSnapshot(
            assignment_public_id=row.assignment_public_id,
            title=row.title,
            description=row.description,
            is_active=row.is_active,
        )

    async def list_assignments(self, *, active_only: bool = True) -> list[AssignmentSnapshot]:
        items = [
            AssignmentSnapshot(
                assignment_public_id=row.assignment_public_id,
                title=row.title,
                description=row.description,
                is_active=row.is_active,
            )
            for row in self.assignments.values()
            if (not active_only) or row.is_active
        ]
        items.sort(key=lambda item: item.assignment_public_id)
        return items

    async def create_submission_with_source(
        self,
        *,
        candidate_public_id: str,
        assignment_public_id: str,
        source_type: str,
        source_external_id: str,
        initial_status: str,
        metadata_json: dict[str, object] | None = None,
        source_payload_ref: str | None = None,
    ) -> UpsertSourceResult:
        del source_payload_ref
        if candidate_public_id not in self.candidates:
            raise DomainInvariantError("candidate is not found")
        if assignment_public_id not in self.assignments:
            raise DomainInvariantError("assignment is not found")

        key = (source_type, source_external_id)
        existing = self.sources.get(key)
        if existing is not None:
            submission = self.submissions[existing.submission_id]
            return UpsertSourceResult(submission_id=existing.submission_id, status=submission.status, created=False)

        submission_id = new_submission_public_id()
        self.submissions[submission_id] = _SubmissionRow(
            submission_id=submission_id,
            candidate_public_id=candidate_public_id,
            assignment_public_id=assignment_public_id,
            status=initial_status,
        )
        self.sources[key] = SubmissionSourceSnapshot(
            submission_id=submission_id,
            source_type=source_type,
            source_external_id=source_external_id,
            metadata_json=dict(metadata_json or {}),
        )
        return UpsertSourceResult(submission_id=submission_id, status=initial_status, created=True)

    async def find_submission_source(
        self,
        *,
        source_type: str,
        source_external_id: str,
    ) -> SubmissionSourceSnapshot | None:
        return self.sources.get((source_type, source_external_id))

    async def get_submission(self, *, submission_id: str) -> SubmissionSnapshot | None:
        row = self.submissions.get(submission_id)
        if row is None:
            return None
        return SubmissionSnapshot(
            submission_id=row.submission_id,
            candidate_public_id=row.candidate_public_id,
            assignment_public_id=row.assignment_public_id,
            status=row.status,
            attempt_telegram_ingest=row.attempt_telegram_ingest,
            attempt_normalization=row.attempt_normalization,
            attempt_evaluation=row.attempt_evaluation,
            attempt_delivery=row.attempt_delivery,
            claimed_by=row.claimed_by,
            claimed_at=row.claimed_at,
            lease_expires_at=row.lease_expires_at,
            last_error_code=row.last_error_code,
            last_error_message=row.last_error_message,
        )

    async def get_artifact_refs(self, *, item_id: str) -> dict[str, str]:
        refs: dict[str, str] = {}
        for submission_id, stage, artifact_ref, _artifact_version in self.artifacts:
            if submission_id != item_id:
                continue
            refs[stage] = artifact_ref
        return refs

    async def claim_next(self, *, stage: str, worker_id: str, lease_seconds: int = 30) -> WorkItemClaim | None:
        lifecycle = STAGE_LIFECYCLES[stage]
        now = datetime.now(tz=UTC)

        for row in self.submissions.values():
            if row.status == lifecycle.source_state:
                row.status = lifecycle.in_progress_state
                row.claimed_by = worker_id
                row.claimed_at = now
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                attempt = getattr(row, lifecycle.attempt_field) + 1
                self.transitions.append((row.submission_id, lifecycle.source_state, lifecycle.in_progress_state))
                return WorkItemClaim(
                    item_id=row.submission_id,
                    stage=lifecycle.in_progress_state,
                    attempt=attempt,
                    lease_expires_at=row.lease_expires_at,
                )

        for index, item in enumerate(self.queue):
            if item.stage == stage:
                claim = self.queue.pop(index)
                return WorkItemClaim(
                    item_id=claim.item_id,
                    stage=STAGE_LIFECYCLES[stage].in_progress_state,
                    attempt=claim.attempt,
                )
        return None

    async def heartbeat_claim(
        self,
        *,
        item_id: str,
        stage: str,
        worker_id: str,
        lease_seconds: int = 30,
    ) -> bool:
        row = self.submissions.get(item_id)
        if row is None:
            return False
        lifecycle = STAGE_LIFECYCLES[stage]
        now = datetime.now(tz=UTC)
        if (
            row.status != lifecycle.in_progress_state
            or row.claimed_by != worker_id
            or row.lease_expires_at is None
            or row.lease_expires_at <= now
        ):
            return False
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        return True

    async def reclaim_expired_claims(self, *, stage: str) -> int:
        lifecycle = STAGE_LIFECYCLES[stage]
        reclaimed = 0
        now = datetime.now(tz=UTC)
        for row in self.submissions.values():
            if (
                row.status == lifecycle.in_progress_state
                and row.lease_expires_at is not None
                and row.lease_expires_at <= now
            ):
                attempts = getattr(row, lifecycle.attempt_field) + 1
                setattr(row, lifecycle.attempt_field, attempts)
                row.last_error_code = "lease_expired"
                row.last_error_message = "claim lease expired and was reclaimed"
                row.claimed_by = None
                row.claimed_at = None
                row.lease_expires_at = None
                row.status = lifecycle.source_state if attempts < lifecycle.max_attempts else "dead_letter"
                reclaimed += 1
        return reclaimed

    async def transition_state(self, *, item_id: str, from_state: str, to_state: str) -> None:
        if from_state == to_state:
            return
        allowed = ALLOWED_TRANSITIONS.get(from_state, set())
        if to_state not in allowed:
            raise DomainInvariantError(f"invalid transition: {from_state} -> {to_state}")
        self.transitions.append((item_id, from_state, to_state))
        row = self.submissions.get(item_id)
        if row is not None and row.status == from_state:
            row.status = to_state

    async def link_artifact(
        self,
        *,
        item_id: str,
        stage: str,
        artifact_ref: str,
        artifact_version: str | None,
    ) -> None:
        self.artifacts.append((item_id, stage, artifact_ref, artifact_version))

    async def finalize(
        self,
        *,
        item_id: str,
        stage: str,
        worker_id: str,
        success: bool,
        detail: str,
        error_code: str | None = None,
    ) -> None:
        self.finalizations.append((item_id, stage, success, detail))
        lifecycle = STAGE_LIFECYCLES[stage]
        row = self.submissions.get(item_id)
        if row is None:
            return
        now = datetime.now(tz=UTC)
        if (
            row.status != lifecycle.in_progress_state
            or row.claimed_by != worker_id
            or row.lease_expires_at is None
            or row.lease_expires_at <= now
        ):
            raise DomainInvariantError("claim ownership is stale")

        if success:
            self.transitions.append((item_id, lifecycle.in_progress_state, lifecycle.success_state))
            row.status = lifecycle.success_state
            row.last_error_code = None
            row.last_error_message = None
        else:
            attempts = getattr(row, lifecycle.attempt_field) + 1
            setattr(row, lifecycle.attempt_field, attempts)
            row.last_error_code = error_code or "internal_error"
            row.last_error_message = detail
            if attempts >= lifecycle.max_attempts:
                self.transitions.append((item_id, lifecycle.in_progress_state, "dead_letter"))
                row.status = "dead_letter"
            else:
                self.transitions.append((item_id, lifecycle.in_progress_state, lifecycle.source_state))
                row.status = lifecycle.source_state

        row.claimed_by = None
        row.claimed_at = None
        row.lease_expires_at = None



