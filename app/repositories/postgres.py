from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
from typing import Any

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
SQL_HEARTBEAT_CLAIM = load_sql("heartbeat_claim.sql")
SQL_FINALIZE_SUCCESS = load_sql("finalize_success.sql")
SQL_FINALIZE_FAILURE_RETRY = load_sql("finalize_failure_retry.sql")
SQL_FINALIZE_FAILURE_DEAD = load_sql("finalize_failure_dead_letter.sql")
SQL_FINALIZE_FAILURE_TERMINAL = load_sql("finalize_failure_terminal.sql")
SQL_RECLAIM_RETRY = load_sql("reclaim_retry.sql")
SQL_RECLAIM_DEAD = load_sql("reclaim_dead_letter.sql")
SQL_TRANSITION_STATE = load_sql("transition_state.sql")
SQL_LINK_ARTIFACT = load_sql("link_artifact.sql")
SQL_GET_ARTIFACT_REF = load_sql("get_artifact_ref.sql")
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

        async def _init_connection(conn: Any) -> None:
            await conn.set_type_codec(
                "json",
                encoder=json.dumps,
                decoder=json.loads,
                schema="pg_catalog",
            )
            await conn.set_type_codec(
                "jsonb",
                encoder=json.dumps,
                decoder=json.loads,
                schema="pg_catalog",
            )

        self.pool = await asyncpg_module.create_pool(
            dsn=self.dsn,
            min_size=1,
            max_size=5,
            init=_init_connection,
        )

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
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def list_submissions(self, *, query: SubmissionListQuery) -> list[SubmissionListItem]:
        include = set(query.include) | {SubmissionFieldGroup.CORE}
        select_columns = [
            "s.id",
            "s.public_id AS core_public_id",
            "s.status AS core_status",
            "s.created_at AS core_created_at",
            "s.updated_at AS core_updated_at",
        ]
        joins: list[str] = []
        where_parts: list[str] = []
        args: list[object] = []

        if SubmissionFieldGroup.CANDIDATE in include:
            joins.append("JOIN candidates c ON c.id = s.candidate_id")
            select_columns.append("c.public_id AS candidate_public_id")
            if query.candidate_public_id is not None:
                args.append(query.candidate_public_id)
                where_parts.append(f"c.public_id = ${len(args)}")
        elif query.candidate_public_id is not None:
            args.append(query.candidate_public_id)
            where_parts.append(
                "EXISTS ("
                "SELECT 1 FROM candidates c "
                f"WHERE c.id = s.candidate_id AND c.public_id = ${len(args)}"
                ")"
            )

        if SubmissionFieldGroup.ASSIGNMENT in include:
            joins.append("JOIN assignments a ON a.id = s.assignment_id")
            select_columns.append("a.public_id AS assignment_public_id")
            if query.assignment_public_id is not None:
                args.append(query.assignment_public_id)
                where_parts.append(f"a.public_id = ${len(args)}")
        elif query.assignment_public_id is not None:
            args.append(query.assignment_public_id)
            where_parts.append(
                "EXISTS ("
                "SELECT 1 FROM assignments a "
                f"WHERE a.id = s.assignment_id AND a.public_id = ${len(args)}"
                ")"
            )

        if SubmissionFieldGroup.SOURCE in include:
            joins.append("LEFT JOIN submission_sources ss ON ss.submission_id = s.id")
            select_columns.extend(["ss.source_type", "ss.source_external_id"])
            if query.source_type is not None:
                args.append(query.source_type)
                where_parts.append(f"ss.source_type = ${len(args)}")
        elif query.source_type is not None:
            args.append(query.source_type)
            where_parts.append(
                "EXISTS ("
                "SELECT 1 FROM submission_sources ss "
                f"WHERE ss.submission_id = s.id AND ss.source_type = ${len(args)}"
                ")"
            )

        if SubmissionFieldGroup.EVALUATION in include:
            joins.append(
                "LEFT JOIN LATERAL ("
                "SELECT score_1_10, criteria_scores_json, organizer_feedback_json, candidate_feedback_json, updated_at "
                "FROM evaluations e "
                "WHERE e.submission_id = s.id "
                "ORDER BY e.updated_at DESC "
                "LIMIT 1"
                ") ev ON TRUE"
            )
            select_columns.extend(
                [
                    "ev.score_1_10",
                    "ev.criteria_scores_json",
                    "ev.organizer_feedback_json",
                    "ev.candidate_feedback_json",
                ]
            )
            joins.append(
                "LEFT JOIN LATERAL ("
                "SELECT chain_version, spec_version, response_language, model "
                "FROM llm_runs lr "
                "WHERE lr.submission_id = s.id "
                "ORDER BY lr.created_at DESC "
                "LIMIT 1"
                ") llm ON TRUE"
            )
            select_columns.extend(
                [
                    "llm.chain_version",
                    "llm.spec_version",
                    "llm.response_language",
                    "llm.model",
                ]
            )

        if SubmissionFieldGroup.OPS in include or query.has_error is not None:
            if SubmissionFieldGroup.OPS in include:
                select_columns.extend(["s.last_error_code", "s.last_error_message"])
            if query.has_error is True:
                where_parts.append("s.last_error_code IS NOT NULL")
            elif query.has_error is False:
                where_parts.append("s.last_error_code IS NULL")

        if query.statuses:
            args.append(list(query.statuses))
            where_parts.append(f"s.status = ANY(${len(args)}::text[])")
        if query.submission_ids:
            args.append(list(query.submission_ids))
            where_parts.append(f"s.public_id = ANY(${len(args)}::text[])")
        if query.created_from is not None:
            args.append(query.created_from)
            where_parts.append(f"s.created_at >= ${len(args)}")
        if query.created_to is not None:
            args.append(query.created_to)
            where_parts.append(f"s.created_at <= ${len(args)}")

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        if query.sort_by == SubmissionSortBy.SCORE_1_10:
            if SubmissionFieldGroup.EVALUATION in include:
                order_column = "COALESCE(ev.score_1_10, 0)"
            else:
                order_column = (
                    "COALESCE(("
                    "SELECT e.score_1_10 FROM evaluations e "
                    "WHERE e.submission_id = s.id "
                    "ORDER BY e.updated_at DESC "
                    "LIMIT 1"
                    "), 0)"
                )
        else:
            order_column = {
                SubmissionSortBy.CREATED_AT: "s.created_at",
                SubmissionSortBy.UPDATED_AT: "s.updated_at",
                SubmissionSortBy.STATUS: "s.status",
            }[query.sort_by]
        order_direction = "DESC" if query.sort_order == SortOrder.DESC else "ASC"

        args.extend([query.limit, query.offset])
        limit_placeholder = f"${len(args) - 1}"
        offset_placeholder = f"${len(args)}"
        sql = (
            f"SELECT {', '.join(select_columns)} "
            f"FROM submissions s {' '.join(joins)} {where_sql} "
            f"ORDER BY {order_column} {order_direction}, s.id ASC "
            f"LIMIT {limit_placeholder} OFFSET {offset_placeholder}"
        )

        pool = self._pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)

        return [
            SubmissionListItem(
                id=_as_int(row["id"]) or 0,
                core=SubmissionListItem.Core(
                    public_id=_as_str(row["core_public_id"]) or "",
                    status=_as_str(row["core_status"]) or "",
                    created_at=row["core_created_at"],
                    updated_at=row["core_updated_at"],
                ),
                candidate=SubmissionListItem.Candidate(public_id=_as_str(row["candidate_public_id"]) or "")
                if SubmissionFieldGroup.CANDIDATE in include and _record_get(row, "candidate_public_id") is not None
                else None,
                assignment=SubmissionListItem.Assignment(public_id=_as_str(row["assignment_public_id"]) or "")
                if SubmissionFieldGroup.ASSIGNMENT in include and _record_get(row, "assignment_public_id") is not None
                else None,
                source=SubmissionListItem.Source(
                    type=_as_str(row["source_type"]) or "",
                    external_id=_as_str(row["source_external_id"]) or "",
                )
                if SubmissionFieldGroup.SOURCE in include
                and _record_get(row, "source_type") is not None
                and _record_get(row, "source_external_id") is not None
                else None,
                evaluation=SubmissionListItem.Evaluation(
                    score_1_10=_as_int(_record_get(row, "score_1_10")),
                    criteria_scores_json=_json_object_or_none(_record_get(row, "criteria_scores_json")),
                    organizer_feedback_json=_json_object_or_none(_record_get(row, "organizer_feedback_json")),
                    candidate_feedback_json=_json_object_or_none(_record_get(row, "candidate_feedback_json")),
                    chain_version=_as_str(_record_get(row, "chain_version")),
                    model=_as_str(_record_get(row, "model")),
                    spec_version=_as_str(_record_get(row, "spec_version")),
                    response_language=_as_str(_record_get(row, "response_language")),
                )
                if SubmissionFieldGroup.EVALUATION in include
                else None,
                ops=SubmissionListItem.Ops(
                    last_error_code=_as_str(_record_get(row, "last_error_code")),
                    last_error_message=_as_str(_record_get(row, "last_error_message")),
                )
                if SubmissionFieldGroup.OPS in include
                else None,
            )
            for row in rows
        ]

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
        object_key = _storage_key_from_ref(artifact_ref)
        pool = self._pool()
        async with pool.acquire() as conn:
            await conn.execute(SQL_LINK_ARTIFACT, item_id, stage, bucket, object_key, artifact_version)

    async def get_artifact_ref(self, *, item_id: str, stage: str) -> str:
        pool = self._pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(SQL_GET_ARTIFACT_REF, item_id, stage)
        if row is None:
            raise KeyError(f"artifact ref not found for submission={item_id} stage={stage}")
        object_key = _as_str(_record_get(row, "object_key"))
        if object_key is None:
            raise KeyError(f"artifact object key is missing for submission={item_id} stage={stage}")
        return object_key
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
        criteria_payload = dict(criteria_scores_json)
        # Keep version trace co-located with evaluation payload for quick reads
        # in delivery/export paths.
        criteria_payload["_reproducibility"] = dict(reproducibility_subset)
        pool = self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                SQL_INSERT_EVALUATION,
                submission_id,
                score_1_10,
                criteria_payload,
                organizer_feedback_json,
                candidate_feedback_json,
                ai_assistance_likelihood,
                ai_assistance_confidence,
            )

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
        pool = self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                SQL_INSERT_LLM_RUN,
                submission_id,
                provider,
                model,
                api_base,
                chain_version,
                spec_version,
                response_language,
                temperature,
                seed,
                tokens_input,
                tokens_output,
                latency_ms,
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
                resolved_error_code = resolve_stage_error(stage=stage, code=error_code or "internal_error")
                # Terminal errors go to stage-specific failed_* states, recoverable
                # errors follow retry/dead-letter policy.
                if classify_error(resolved_error_code) == "terminal":
                    terminal_row = await conn.fetchrow(
                        SQL_FINALIZE_FAILURE_TERMINAL,
                        item_id,
                        lifecycle.in_progress_state,
                        worker_id,
                        lifecycle.failed_state,
                        resolved_error_code,
                        detail,
                    )
                    if terminal_row is None:
                        raise DomainInvariantError("finalize rejected by ownership guard")
                    return

                row = await conn.fetchrow(
                    retry_query,
                    item_id,
                    lifecycle.in_progress_state,
                    worker_id,
                    lifecycle.source_state,
                    resolved_error_code,
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
                    resolved_error_code,
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


def _storage_key_from_ref(artifact_ref: str) -> str:
    if "://" in artifact_ref:
        return artifact_ref.split("://", maxsplit=1)[1]
    return artifact_ref


def _record_get(row: object, key: str) -> object | None:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]  # type: ignore[index]
    except Exception:
        return None


def _as_str(value: object | None) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _as_int(value: object | None) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _json_object_or_none(value: object | None) -> dict[str, object] | None:
    if value is None:
        return None
    return _json_object(value)
