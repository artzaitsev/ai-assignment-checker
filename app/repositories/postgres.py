from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
from typing import Any

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
from app.repositories.sql_loader import load_sql

try:
    asyncpg_module = importlib.import_module("asyncpg")
except ModuleNotFoundError:  # pragma: no cover
    asyncpg_module = None  # type: ignore[assignment]


SQL_CREATE_CANDIDATE = load_sql("create_candidate.sql")
SQL_CREATE_CANDIDATE_SOURCE = load_sql("create_candidate_source.sql")
SQL_FIND_CANDIDATE_BY_SOURCE = load_sql("find_candidate_by_source.sql")
SQL_CREATE_ASSIGNMENT = load_sql("create_assignment.sql")
SQL_LIST_ASSIGNMENTS = load_sql("list_assignments.sql")
SQL_CLAIM_NEXT = load_sql("claim_next.sql")
SQL_CREATE_SUBMISSION = load_sql("create_submission.sql")
SQL_FIND_SOURCE = load_sql("find_source.sql")
SQL_CREATE_SOURCE = load_sql("create_source.sql")
SQL_GET_SUBMISSION = load_sql("get_submission.sql")
SQL_GET_ARTIFACT_REFS = load_sql("get_artifact_refs.sql")
SQL_HEARTBEAT_CLAIM = load_sql("heartbeat_claim.sql")
SQL_FINALIZE_SUCCESS = load_sql("finalize_success.sql")
SQL_FINALIZE_FAILURE_RETRY = load_sql("finalize_failure_retry.sql")
SQL_FINALIZE_FAILURE_DEAD = load_sql("finalize_failure_dead_letter.sql")
SQL_RECLAIM_RETRY = load_sql("reclaim_retry.sql")
SQL_RECLAIM_DEAD = load_sql("reclaim_dead_letter.sql")
SQL_TRANSITION_STATE = load_sql("transition_state.sql")
SQL_LINK_ARTIFACT = load_sql("link_artifact.sql")
SQL_INSERT_EVALUATION = load_sql("insert_evaluation.sql")
SQL_INSERT_LLM_RUN = load_sql("insert_llm_run.sql")
SQL_INSERT_DELIVERY = load_sql("insert_delivery.sql")


def _is_unique_violation(exc: Exception) -> bool:
    return getattr(exc, "sqlstate", None) == "23505"


@dataclass
class AsyncpgPoolManager:
    dsn: str
    pool: Any | None = None

    async def startup(self) -> None:
        if asyncpg_module is None:  # pragma: no cover
            raise RuntimeError("asyncpg is required for postgres repository mode")
        self.pool = await asyncpg_module.create_pool(dsn=self.dsn, min_size=1, max_size=5)

    async def shutdown(self) -> None:
        if self.pool is None:
            return
        await self.pool.close()
        self.pool = None


@dataclass
class PostgresWorkRepository:
    pool_manager: AsyncpgPoolManager

    def _pool(self) -> Any:
        if self.pool_manager.pool is None:
            raise RuntimeError("postgres pool is not initialized")
        return self.pool_manager.pool

    async def create_candidate(self, *, first_name: str, last_name: str) -> CandidateSnapshot:
        pool = self._pool()
        async with pool.acquire() as conn:
            for _ in range(5):
                candidate_public_id = new_candidate_public_id()
                try:
                    row = await conn.fetchrow(SQL_CREATE_CANDIDATE, candidate_public_id, first_name, last_name)
                    if row is None:
                        raise DomainInvariantError("failed to create candidate")
                    return CandidateSnapshot(
                        candidate_public_id=row["public_id"],
                        first_name=row["first_name"],
                        last_name=row["last_name"],
                    )
                except Exception as exc:
                    if _is_unique_violation(exc):
                        continue
                    raise
        raise DomainInvariantError("failed to allocate unique candidate public id")

    async def get_or_create_candidate_by_source(
        self,
        *,
        source_type: str,
        source_external_id: str,
        first_name: str,
        last_name: str,
        metadata_json: dict[str, object] | None = None,
    ) -> CandidateSnapshot:
        pool = self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(SQL_FIND_CANDIDATE_BY_SOURCE, source_type, source_external_id)
                if existing is not None:
                    return CandidateSnapshot(
                        candidate_public_id=existing["public_id"],
                        first_name=existing["first_name"],
                        last_name=existing["last_name"],
                    )

                created: CandidateSnapshot | None = None
                for _ in range(5):
                    candidate_public_id = new_candidate_public_id()
                    try:
                        row = await conn.fetchrow(SQL_CREATE_CANDIDATE, candidate_public_id, first_name, last_name)
                        if row is None:
                            raise DomainInvariantError("failed to create candidate")
                        created = CandidateSnapshot(
                            candidate_public_id=row["public_id"],
                            first_name=row["first_name"],
                            last_name=row["last_name"],
                        )
                        break
                    except Exception as exc:
                        if _is_unique_violation(exc):
                            continue
                        raise
                if created is None:
                    raise DomainInvariantError("failed to allocate unique candidate public id")

                inserted = await conn.fetchval(
                    SQL_CREATE_CANDIDATE_SOURCE,
                    created.candidate_public_id,
                    source_type,
                    source_external_id,
                    json.dumps(metadata_json or {}),
                )
                if inserted is None:
                    existing = await conn.fetchrow(SQL_FIND_CANDIDATE_BY_SOURCE, source_type, source_external_id)
                    if existing is None:
                        raise DomainInvariantError("candidate source create conflict without row")
                    return CandidateSnapshot(
                        candidate_public_id=existing["public_id"],
                        first_name=existing["first_name"],
                        last_name=existing["last_name"],
                    )
                return created

    async def create_assignment(
        self,
        *,
        title: str,
        description: str,
        is_active: bool = True,
    ) -> AssignmentSnapshot:
        pool = self._pool()
        async with pool.acquire() as conn:
            for _ in range(5):
                assignment_public_id = new_assignment_public_id()
                try:
                    row = await conn.fetchrow(SQL_CREATE_ASSIGNMENT, assignment_public_id, title, description, is_active)
                    if row is None:
                        raise DomainInvariantError("failed to create assignment")
                    return AssignmentSnapshot(
                        assignment_public_id=row["public_id"],
                        title=row["title"],
                        description=row["description"],
                        is_active=row["is_active"],
                    )
                except Exception as exc:
                    if _is_unique_violation(exc):
                        continue
                    raise
        raise DomainInvariantError("failed to allocate unique assignment public id")

    async def list_assignments(self, *, active_only: bool = True) -> list[AssignmentSnapshot]:
        pool = self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(SQL_LIST_ASSIGNMENTS, active_only)
        return [
            AssignmentSnapshot(
                assignment_public_id=row["public_id"],
                title=row["title"],
                description=row["description"],
                is_active=row["is_active"],
            )
            for row in rows
        ]

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
        pool = self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(SQL_FIND_SOURCE, source_type, source_external_id)
                if existing is not None:
                    submission = await conn.fetchrow(SQL_GET_SUBMISSION, existing["submission_id"])
                    if submission is None:
                        raise DomainInvariantError("source references missing submission")
                    return UpsertSourceResult(
                        submission_id=existing["submission_id"],
                        status=submission["status"],
                        created=False,
                    )

                for _ in range(5):
                    submission_id = new_submission_public_id()
                    try:
                        submission_pk = await conn.fetchval(
                            SQL_CREATE_SUBMISSION,
                            submission_id,
                            candidate_public_id,
                            assignment_public_id,
                            initial_status,
                        )
                        if submission_pk is None:
                            raise DomainInvariantError("candidate or assignment is not found")
                        inserted = await conn.fetchval(
                            SQL_CREATE_SOURCE,
                            submission_pk,
                            source_type,
                            source_external_id,
                            source_payload_ref,
                            json.dumps(metadata_json or {}),
                        )
                        if inserted is None:
                            existing = await conn.fetchrow(SQL_FIND_SOURCE, source_type, source_external_id)
                            if existing is None:
                                raise DomainInvariantError("source create conflict without existing row")
                            submission = await conn.fetchrow(SQL_GET_SUBMISSION, existing["submission_id"])
                            if submission is None:
                                raise DomainInvariantError("source references missing submission")
                            return UpsertSourceResult(
                                submission_id=existing["submission_id"],
                                status=submission["status"],
                                created=False,
                            )
                        return UpsertSourceResult(
                            submission_id=submission_id,
                            status=initial_status,
                            created=True,
                        )
                    except Exception as exc:
                        if _is_unique_violation(exc):
                            continue
                        raise

                raise DomainInvariantError("failed to allocate unique submission public id")

    async def find_submission_source(
        self,
        *,
        source_type: str,
        source_external_id: str,
    ) -> SubmissionSourceSnapshot | None:
        pool = self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(SQL_FIND_SOURCE, source_type, source_external_id)
        if row is None:
            return None
        return SubmissionSourceSnapshot(
            submission_id=row["submission_id"],
            source_type=row["source_type"],
            source_external_id=row["source_external_id"],
            metadata_json=_json_object(row["metadata_json"]),
        )

    async def get_submission(self, *, submission_id: str) -> SubmissionSnapshot | None:
        pool = self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(SQL_GET_SUBMISSION, submission_id)
        if row is None:
            return None
        return SubmissionSnapshot(
            submission_id=row["public_id"],
            candidate_public_id=row["candidate_public_id"],
            assignment_public_id=row["assignment_public_id"],
            status=row["status"],
            attempt_telegram_ingest=row["attempt_telegram_ingest"],
            attempt_normalization=row["attempt_normalization"],
            attempt_evaluation=row["attempt_evaluation"],
            attempt_delivery=row["attempt_delivery"],
            claimed_by=row["claimed_by"],
            claimed_at=row["claimed_at"],
            lease_expires_at=row["lease_expires_at"],
            last_error_code=row["last_error_code"],
            last_error_message=row["last_error_message"],
        )

    async def get_artifact_refs(self, *, item_id: str) -> dict[str, str]:
        pool = self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(SQL_GET_ARTIFACT_REFS, item_id)

        refs: dict[str, str] = {}
        for row in rows:
            stage = str(row["stage"])
            object_key = str(row["object_key"])
            bucket = str(row["bucket"])
            if bucket == "skeleton":
                refs[stage] = f"stub://{object_key}"
            else:
                refs[stage] = object_key
        return refs

    async def claim_next(self, *, stage: str, worker_id: str, lease_seconds: int = 30) -> WorkItemClaim | None:
        lifecycle = STAGE_LIFECYCLES[stage]
        query = SQL_CLAIM_NEXT.format(attempt_field=lifecycle.attempt_field)
        pool = self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    query,
                    lifecycle.source_state,
                    lifecycle.in_progress_state,
                    worker_id,
                    lease_seconds,
                )
        if row is None:
            return None
        return WorkItemClaim(
            item_id=row["public_id"],
            stage=lifecycle.in_progress_state,
            attempt=row[lifecycle.attempt_field] + 1,
            lease_expires_at=row["lease_expires_at"],
        )

    async def heartbeat_claim(
        self,
        *,
        item_id: str,
        stage: str,
        worker_id: str,
        lease_seconds: int = 30,
    ) -> bool:
        lifecycle = STAGE_LIFECYCLES[stage]
        pool = self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                SQL_HEARTBEAT_CLAIM,
                item_id,
                lifecycle.in_progress_state,
                worker_id,
                lease_seconds,
            )
        return row is not None

    async def reclaim_expired_claims(self, *, stage: str) -> int:
        lifecycle = STAGE_LIFECYCLES[stage]
        query_retry = SQL_RECLAIM_RETRY.format(attempt_field=lifecycle.attempt_field)
        query_dead = SQL_RECLAIM_DEAD.format(attempt_field=lifecycle.attempt_field)
        pool = self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                retry_rows = await conn.fetch(
                    query_retry,
                    lifecycle.in_progress_state,
                    lifecycle.source_state,
                    "lease_expired",
                    "claim lease expired and was reclaimed",
                    lifecycle.max_attempts,
                )
                dead_rows = await conn.fetch(
                    query_dead,
                    lifecycle.in_progress_state,
                    "lease_expired",
                    "claim lease expired and reached max attempts",
                    lifecycle.max_attempts,
                )
        return len(retry_rows) + len(dead_rows)

    async def transition_state(self, *, item_id: str, from_state: str, to_state: str) -> None:
        if from_state == to_state:
            return
        allowed = ALLOWED_TRANSITIONS.get(from_state, set())
        if to_state not in allowed:
            raise DomainInvariantError(f"invalid transition: {from_state} -> {to_state}")

        pool = self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(SQL_TRANSITION_STATE, item_id, from_state, to_state)
        if row is None:
            raise DomainInvariantError("transition rejected")

    async def link_artifact(
        self,
        *,
        item_id: str,
        stage: str,
        artifact_ref: str,
        artifact_version: str | None,
    ) -> None:
        bucket = "skeleton"
        object_key = artifact_ref.replace("stub://", "")
        pool = self._pool()
        async with pool.acquire() as conn:
            await conn.execute(SQL_LINK_ARTIFACT, item_id, stage, bucket, object_key, artifact_version)

    async def persist_evaluation(self, *, submission_id: str, score_1_10: int) -> None:
        pool = self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                SQL_INSERT_EVALUATION,
                submission_id,
                score_1_10,
                json.dumps({}),
                json.dumps({}),
                json.dumps({}),
                0.0,
                0.0,
            )

    async def persist_llm_run(self, *, submission_id: str, provider: str, model: str) -> None:
        pool = self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                SQL_INSERT_LLM_RUN,
                submission_id,
                provider,
                model,
                "https://example.invalid",
                "prompt:v0",
                "chain:v0",
                "rubric:v0",
                "result:v0",
                0.0,
                0,
                0,
                0,
                0,
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
        pool = self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                SQL_INSERT_DELIVERY,
                submission_id,
                channel,
                status,
                external_message_id,
                attempts,
                last_error_code,
            )

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
        lifecycle = STAGE_LIFECYCLES[stage]
        pool = self._pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                if success:
                    row = await conn.fetchrow(
                        SQL_FINALIZE_SUCCESS,
                        item_id,
                        lifecycle.in_progress_state,
                        worker_id,
                        lifecycle.success_state,
                    )
                    if row is None:
                        raise DomainInvariantError("finalize rejected by ownership guard")
                    return

                retry_query = SQL_FINALIZE_FAILURE_RETRY.format(attempt_field=lifecycle.attempt_field)
                row = await conn.fetchrow(
                    retry_query,
                    item_id,
                    lifecycle.in_progress_state,
                    worker_id,
                    lifecycle.source_state,
                    error_code or "internal_error",
                    detail,
                    lifecycle.max_attempts,
                )
                if row is not None:
                    return

                dead_query = SQL_FINALIZE_FAILURE_DEAD.format(attempt_field=lifecycle.attempt_field)
                dead_row = await conn.fetchrow(
                    dead_query,
                    item_id,
                    lifecycle.in_progress_state,
                    worker_id,
                    error_code or "internal_error",
                    detail,
                    lifecycle.max_attempts,
                )
                if dead_row is None:
                    raise DomainInvariantError("finalize rejected by ownership guard")


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return {}



