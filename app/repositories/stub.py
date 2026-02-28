from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain.errors import DomainInvariantError
from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.ids import new_assignment_public_id, new_candidate_public_id, new_submission_public_id
from app.domain.lifecycle import ALLOWED_TRANSITIONS, STAGE_LIFECYCLES
from app.domain.models import (
    AssignmentSnapshot,
    CandidateSnapshot,
    SortOrder,
    SubmissionFieldGroup,
    SubmissionListItem,
    SubmissionListQuery,
    SubmissionSortBy,
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
    id: int
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
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


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
    llm_runs: list[dict[str, object]] = field(default_factory=list)
    evaluations: list[dict[str, object]] = field(default_factory=list)
    deliveries: list[dict[str, object]] = field(default_factory=list)
    next_submission_id: int = 1

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
            id=self.next_submission_id,
            submission_id=submission_id,
            candidate_public_id=candidate_public_id,
            assignment_public_id=assignment_public_id,
            status=initial_status,
        )
        self.next_submission_id += 1
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
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def list_submissions(self, *, query: SubmissionListQuery) -> list[SubmissionListItem]:
        items: list[SubmissionListItem] = []
        source_by_submission = {source.submission_id: source for source in self.sources.values()}
        eval_by_submission = {row["submission_id"]: row for row in self.evaluations}
        llm_by_submission = {row["submission_id"]: row for row in self.llm_runs}

        for row in self.submissions.values():
            if query.submission_ids is not None and row.submission_id not in set(query.submission_ids):
                continue
            if query.statuses is not None and row.status not in set(query.statuses):
                continue
            if query.candidate_public_id is not None and row.candidate_public_id != query.candidate_public_id:
                continue
            if query.assignment_public_id is not None and row.assignment_public_id != query.assignment_public_id:
                continue
            source = source_by_submission.get(row.submission_id)
            if query.source_type is not None and (source is None or source.source_type != query.source_type):
                continue
            if query.has_error is True and row.last_error_code is None:
                continue
            if query.has_error is False and row.last_error_code is not None:
                continue
            if query.created_from is not None and row.created_at < query.created_from:
                continue
            if query.created_to is not None and row.created_at > query.created_to:
                continue

            eval_row = eval_by_submission.get(row.submission_id)
            llm_row = llm_by_submission.get(row.submission_id)

            include = set(query.include) | {SubmissionFieldGroup.CORE}
            score_value = _as_int(eval_row.get("score_1_10") if eval_row else None)
            criteria_json = _as_json_dict(eval_row.get("criteria_scores_json") if eval_row else None)
            organizer_json = _as_json_dict(eval_row.get("organizer_feedback_json") if eval_row else None)
            candidate_json = _as_json_dict(eval_row.get("candidate_feedback_json") if eval_row else None)

            items.append(
                SubmissionListItem(
                    id=row.id,
                    core=SubmissionListItem.Core(
                        public_id=row.submission_id,
                        status=row.status,
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                    ),
                    candidate=SubmissionListItem.Candidate(public_id=row.candidate_public_id)
                    if SubmissionFieldGroup.CANDIDATE in include
                    else None,
                    assignment=SubmissionListItem.Assignment(public_id=row.assignment_public_id)
                    if SubmissionFieldGroup.ASSIGNMENT in include
                    else None,
                    source=SubmissionListItem.Source(type=source.source_type, external_id=source.source_external_id)
                    if SubmissionFieldGroup.SOURCE in include and source is not None
                    else None,
                    evaluation=SubmissionListItem.Evaluation(
                        score_1_10=score_value,
                        criteria_scores_json=criteria_json if eval_row else None,
                        organizer_feedback_json=organizer_json if eval_row else None,
                        candidate_feedback_json=candidate_json if eval_row else None,
                        chain_version=str(llm_row["chain_version"]) if llm_row else None,
                        model=str(llm_row["model"]) if llm_row else None,
                        spec_version=str(llm_row["spec_version"]) if llm_row else None,
                        response_language=str(llm_row["response_language"]) if llm_row else None,
                    )
                    if SubmissionFieldGroup.EVALUATION in include
                    else None,
                    ops=SubmissionListItem.Ops(
                        last_error_code=row.last_error_code,
                        last_error_message=row.last_error_message,
                    )
                    if SubmissionFieldGroup.OPS in include
                    else None,
                )
            )

        reverse = query.sort_order == SortOrder.DESC
        if query.sort_by == SubmissionSortBy.STATUS:
            items.sort(key=lambda item: (item.core.status, item.id), reverse=reverse)
        elif query.sort_by == SubmissionSortBy.SCORE_1_10:
            items.sort(
                key=lambda item: ((item.evaluation.score_1_10 if item.evaluation else 0) or 0, item.id),
                reverse=reverse,
            )
        elif query.sort_by == SubmissionSortBy.UPDATED_AT:
            items.sort(key=lambda item: (item.core.updated_at, item.id), reverse=reverse)
        else:
            items.sort(key=lambda item: (item.core.created_at, item.id), reverse=reverse)

        start = query.offset
        end = query.offset + query.limit
        return items[start:end]

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

    async def get_artifact_ref(self, *, item_id: str, stage: str) -> str:
        for artifact_item_id, artifact_stage, artifact_ref, _artifact_version in reversed(self.artifacts):
            if artifact_item_id == item_id and artifact_stage == stage:
                return artifact_ref
        raise KeyError(f"artifact ref not found for submission={item_id} stage={stage}")

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
            resolved_error_code = resolve_stage_error(stage=stage, code=error_code or "internal_error")
            row.last_error_code = resolved_error_code
            row.last_error_message = detail
            # Mirror Postgres behavior: terminal -> failed_*, recoverable -> retry/dead_letter.
            if classify_error(resolved_error_code) == "terminal":
                self.transitions.append((item_id, lifecycle.in_progress_state, lifecycle.failed_state))
                row.status = lifecycle.failed_state
            elif attempts >= lifecycle.max_attempts:
                self.transitions.append((item_id, lifecycle.in_progress_state, "dead_letter"))
                row.status = "dead_letter"
            else:
                self.transitions.append((item_id, lifecycle.in_progress_state, lifecycle.source_state))
                row.status = lifecycle.source_state

        row.updated_at = now

        row.claimed_by = None
        row.claimed_at = None
        row.lease_expires_at = None

    async def persist_evaluation(
        self,
        *,
        submission_id: str,
        score_1_10: int,
        criteria_scores_json: dict[str, object],
        organizer_feedback_json: dict[str, object],
        candidate_feedback_json: dict[str, object],
        ai_assistance_likelihood: float,
        ai_assistance_confidence: float,
        reproducibility_subset: dict[str, str],
    ) -> None:
        existing = next((row for row in self.evaluations if row["submission_id"] == submission_id), None)
        payload = {
            "submission_id": submission_id,
            "score_1_10": score_1_10,
            "criteria_scores_json": dict(criteria_scores_json),
            "organizer_feedback_json": dict(organizer_feedback_json),
            "candidate_feedback_json": dict(candidate_feedback_json),
            "ai_assistance_likelihood": ai_assistance_likelihood,
            "ai_assistance_confidence": ai_assistance_confidence,
            "reproducibility_subset": dict(reproducibility_subset),
            "updated_at": datetime.now(tz=UTC),
        }
        if existing is None:
            self.evaluations.append(payload)
        else:
            existing.update(payload)

    async def persist_llm_run(
        self,
        *,
        submission_id: str,
        provider: str,
        model: str,
        api_base: str,
        chain_version: str,
        spec_version: str,
        response_language: str,
        temperature: float,
        seed: int | None,
        tokens_input: int,
        tokens_output: int,
        latency_ms: int,
    ) -> None:
        self.llm_runs.append(
            {
                "submission_id": submission_id,
                "provider": provider,
                "model": model,
                "api_base": api_base,
                "chain_version": chain_version,
                "spec_version": spec_version,
                "response_language": response_language,
                "temperature": temperature,
                "seed": seed,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
                "latency_ms": latency_ms,
            }
        )

    async def persist_delivery(
        self,
        *,
        submission_id: str,
        channel: str,
        status: str,
        external_message_id: str | None = None,
        attempts: int = 0,
        last_error_code: str | None = None,
    ) -> None:
        self.deliveries.append(
            {
                "submission_id": submission_id,
                "channel": channel,
                "status": status,
                "external_message_id": external_message_id,
                "attempts": attempts,
                "last_error_code": last_error_code,
            }
        )


def _as_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _as_json_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): val for key, val in value.items()}
