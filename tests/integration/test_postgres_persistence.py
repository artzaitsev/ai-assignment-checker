from __future__ import annotations

import asyncio

import pytest

from app.domain.errors import DomainInvariantError
from app.repositories.postgres import AsyncpgPoolManager, PostgresWorkRepository
from tests.integration.postgres_test_utils import apply_down, apply_up, require_postgres, reset_public_schema


@pytest.mark.integration
def test_migration_up_down_up_contract() -> None:
    dsn = require_postgres()

    async def _run() -> None:
        await reset_public_schema(dsn=dsn)
        await apply_up(dsn=dsn)
        manager = AsyncpgPoolManager(dsn=dsn)
        await manager.startup()
        try:
            repo = PostgresWorkRepository(pool_manager=manager)
            snapshot = await repo.get_submission(submission_id="missing")
            assert snapshot is None
        finally:
            await manager.shutdown()

        await apply_down(dsn=dsn)
        await apply_up(dsn=dsn)

    asyncio.run(_run())


@pytest.mark.integration
def test_concurrent_claim_exclusivity_skip_locked() -> None:
    dsn = require_postgres()

    async def _run() -> None:
        await reset_public_schema(dsn=dsn)
        await apply_up(dsn=dsn)

        manager = AsyncpgPoolManager(dsn=dsn)
        await manager.startup()
        repo = PostgresWorkRepository(pool_manager=manager)
        try:
            candidate, assignment = await _seed_candidate_assignment(repo)
            for idx in range(3):
                await repo.create_submission_with_source(
                    candidate_public_id=candidate,
                    assignment_public_id=assignment,
                    source_type="api_upload",
                    source_external_id=f"claim-{idx}",
                    initial_status="uploaded",
                )

            claims = await asyncio.gather(
                repo.claim_next(stage="normalized", worker_id="w-1"),
                repo.claim_next(stage="normalized", worker_id="w-2"),
                repo.claim_next(stage="normalized", worker_id="w-3"),
            )
            claim_ids = [claim.item_id for claim in claims if claim is not None]
            assert len(claim_ids) == len(set(claim_ids))
            assert len(claim_ids) == 3
        finally:
            await manager.shutdown()

    asyncio.run(_run())


@pytest.mark.integration
def test_retry_progression_and_dead_letter_transition() -> None:
    dsn = require_postgres()

    async def _run() -> None:
        await reset_public_schema(dsn=dsn)
        await apply_up(dsn=dsn)

        manager = AsyncpgPoolManager(dsn=dsn)
        await manager.startup()
        repo = PostgresWorkRepository(pool_manager=manager)
        try:
            candidate, assignment = await _seed_candidate_assignment(repo)
            created = await repo.create_submission_with_source(
                candidate_public_id=candidate,
                assignment_public_id=assignment,
                source_type="api_upload",
                source_external_id="retry-1",
                initial_status="uploaded",
            )
            for _ in range(3):
                claim = await repo.claim_next(stage="normalized", worker_id="worker-normalize", lease_seconds=5)
                assert claim is not None
                await repo.finalize(
                    item_id=claim.item_id,
                    stage="normalized",
                    worker_id="worker-normalize",
                    success=False,
                    detail="boom",
                    error_code="internal_error",
                )
            snapshot = await repo.get_submission(submission_id=created.submission_id)
            assert snapshot is not None
            assert snapshot.attempt_normalization == 3
            assert snapshot.last_error_code == "internal_error"
            assert snapshot.status == "dead_letter"
        finally:
            await manager.shutdown()

    asyncio.run(_run())


@pytest.mark.integration
def test_source_tracking_and_idempotency_for_api_and_telegram() -> None:
    dsn = require_postgres()

    async def _run() -> None:
        await reset_public_schema(dsn=dsn)
        await apply_up(dsn=dsn)

        manager = AsyncpgPoolManager(dsn=dsn)
        await manager.startup()
        repo = PostgresWorkRepository(pool_manager=manager)
        try:
            candidate, assignment = await _seed_candidate_assignment(repo)
            api_first = await repo.create_submission_with_source(
                candidate_public_id=candidate,
                assignment_public_id=assignment,
                source_type="api_upload",
                source_external_id="api-dup",
                initial_status="uploaded",
                metadata_json={"correlation_id": "corr-1"},
            )
            api_second = await repo.create_submission_with_source(
                candidate_public_id=candidate,
                assignment_public_id=assignment,
                source_type="api_upload",
                source_external_id="api-dup",
                initial_status="uploaded",
                metadata_json={"correlation_id": "corr-1"},
            )
            assert api_first.created is True
            assert api_second.created is False
            assert api_first.submission_id == api_second.submission_id

            tg = await repo.create_submission_with_source(
                candidate_public_id=candidate,
                assignment_public_id=assignment,
                source_type="telegram_webhook",
                source_external_id="update-11",
                initial_status="telegram_update_received",
                metadata_json={"update_id": "11", "file_id": "f-11"},
            )
            source = await repo.find_submission_source(
                source_type="telegram_webhook",
                source_external_id="update-11",
            )
            assert tg.created is True
            assert source is not None
            assert source.submission_id == tg.submission_id
            assert source.metadata_json["file_id"] == "f-11"
        finally:
            await manager.shutdown()

    asyncio.run(_run())


@pytest.mark.integration
def test_reclaim_and_stale_owner_guards() -> None:
    dsn = require_postgres()

    async def _run() -> None:
        await reset_public_schema(dsn=dsn)
        await apply_up(dsn=dsn)

        manager = AsyncpgPoolManager(dsn=dsn)
        await manager.startup()
        repo = PostgresWorkRepository(pool_manager=manager)
        try:
            candidate, assignment = await _seed_candidate_assignment(repo)
            created = await repo.create_submission_with_source(
                candidate_public_id=candidate,
                assignment_public_id=assignment,
                source_type="api_upload",
                source_external_id="lease-1",
                initial_status="uploaded",
            )
            claim = await repo.claim_next(stage="normalized", worker_id="worker-a", lease_seconds=1)
            assert claim is not None
            await asyncio.sleep(1.05)
            reclaimed = await repo.reclaim_expired_claims(stage="normalized")
            assert reclaimed == 1

            heartbeat_ok = await repo.heartbeat_claim(
                item_id=created.submission_id,
                stage="normalized",
                worker_id="worker-a",
                lease_seconds=10,
            )
            assert heartbeat_ok is False

            with pytest.raises(DomainInvariantError):
                await repo.finalize(
                    item_id=created.submission_id,
                    stage="normalized",
                    worker_id="worker-a",
                    success=True,
                    detail="stale",
                )
        finally:
            await manager.shutdown()

    asyncio.run(_run())


@pytest.mark.integration
def test_terminal_error_maps_to_failed_stage_state() -> None:
    dsn = require_postgres()

    async def _run() -> None:
        await reset_public_schema(dsn=dsn)
        await apply_up(dsn=dsn)

        manager = AsyncpgPoolManager(dsn=dsn)
        await manager.startup()
        repo = PostgresWorkRepository(pool_manager=manager)
        try:
            candidate, assignment = await _seed_candidate_assignment(repo)
            created = await repo.create_submission_with_source(
                candidate_public_id=candidate,
                assignment_public_id=assignment,
                source_type="api_upload",
                source_external_id="terminal-1",
                initial_status="uploaded",
            )
            claim = await repo.claim_next(stage="normalized", worker_id="worker-normalize", lease_seconds=5)
            assert claim is not None
            await repo.finalize(
                item_id=claim.item_id,
                stage="normalized",
                worker_id="worker-normalize",
                success=False,
                detail="invalid payload",
                error_code="schema_validation_failed",
            )
            snapshot = await repo.get_submission(submission_id=created.submission_id)
            assert snapshot is not None
            assert snapshot.status == "failed_normalization"
            assert snapshot.last_error_code == "schema_validation_failed"
        finally:
            await manager.shutdown()

    asyncio.run(_run())


@pytest.mark.integration
def test_reproducibility_metadata_persistence_contract() -> None:
    dsn = require_postgres()

    async def _run() -> None:
        await reset_public_schema(dsn=dsn)
        await apply_up(dsn=dsn)

        manager = AsyncpgPoolManager(dsn=dsn)
        await manager.startup()
        repo = PostgresWorkRepository(pool_manager=manager)
        try:
            candidate, assignment = await _seed_candidate_assignment(repo)
            created = await repo.create_submission_with_source(
                candidate_public_id=candidate,
                assignment_public_id=assignment,
                source_type="api_upload",
                source_external_id="repro-1",
                initial_status="normalized",
            )
            reproducibility = {
                "chain_version": "chain:v1",
                "spec_version": "chain-spec:v1",
                "model": "model:v1",
                "response_language": "ru",
            }
            await repo.persist_llm_run(
                submission_id=created.submission_id,
                provider="openai-compatible",
                model="model:v1",
                api_base="https://example.invalid",
                chain_version="chain:v1",
                spec_version="chain-spec:v1",
                response_language="ru",
                temperature=0.1,
                seed=42,
                tokens_input=123,
                tokens_output=456,
                latency_ms=789,
            )
            await repo.persist_evaluation(
                submission_id=created.submission_id,
                score_1_10=8,
                criteria_scores_json={"correctness": 8},
                organizer_feedback_json={"strengths": ["clear"]},
                candidate_feedback_json={"summary": "good"},
                ai_assistance_likelihood=0.35,
                ai_assistance_confidence=0.55,
                reproducibility_subset=reproducibility,
            )

            pool = manager.pool
            assert pool is not None
            async with pool.acquire() as conn:
                llm_row = await conn.fetchrow(
                    "SELECT chain_version, spec_version, response_language, model FROM llm_runs LIMIT 1"
                )
                eval_row = await conn.fetchrow(
                    "SELECT criteria_scores_json, ai_assistance_likelihood, confidence FROM evaluations LIMIT 1"
                )

            assert llm_row is not None
            assert llm_row["chain_version"] == "chain:v1"
            assert llm_row["spec_version"] == "chain-spec:v1"
            assert llm_row["response_language"] == "ru"
            assert llm_row["model"] == "model:v1"
            assert eval_row is not None
            assert eval_row["criteria_scores_json"]["_reproducibility"]["chain_version"] == "chain:v1"
            assert eval_row["criteria_scores_json"]["_reproducibility"]["spec_version"] == "chain-spec:v1"
            assert eval_row["ai_assistance_likelihood"] == pytest.approx(0.35)
            assert eval_row["confidence"] == pytest.approx(0.55)
        finally:
            await manager.shutdown()

    asyncio.run(_run())


async def _seed_candidate_assignment(repo: PostgresWorkRepository) -> tuple[str, str]:
    candidate = await repo.create_candidate(first_name="Seed", last_name="Candidate")
    assignment = await repo.create_assignment(title="Seed Assignment", description="seed")
    return candidate.candidate_public_id, assignment.assignment_public_id
