from __future__ import annotations

import asyncio
import os

import pytest

from app.clients.s3 import build_s3_storage_client
from app.domain.errors import DomainInvariantError
from app.domain.models import CandidateSourceType
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

            resolved = await repo.get_or_create_candidate_by_source(
                source_type=CandidateSourceType.TELEGRAM_CHAT,
                source_external_id="chat-11",
                first_name="Should",
                last_name="NotCreate",
                metadata_json={"chat_id": "chat-11"},
            )
            assert resolved.candidate_public_id != candidate
            resolved_again = await repo.get_or_create_candidate_by_source(
                source_type=CandidateSourceType.TELEGRAM_CHAT,
                source_external_id="chat-11",
                first_name="Ignored",
                last_name="Ignored",
                metadata_json={"chat_id": "chat-11"},
            )
            assert resolved_again.candidate_public_id == resolved.candidate_public_id
            assert resolved_again.first_name == resolved.first_name
            assert resolved_again.last_name == resolved.last_name
            chat_external_id = await repo.find_candidate_source_external_id(
                candidate_public_id=resolved.candidate_public_id,
                source_type=CandidateSourceType.TELEGRAM_CHAT,
            )
            assert chat_external_id == "chat-11"
            original_candidate_chat_external_id = await repo.find_candidate_source_external_id(
                candidate_public_id=candidate,
                source_type=CandidateSourceType.TELEGRAM_CHAT,
            )
            assert original_candidate_chat_external_id is None
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


@pytest.mark.integration
def test_artifact_link_persists_real_bucket_and_object_key() -> None:
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
                source_external_id="artifact-link-1",
                initial_status="uploaded",
            )
            artifact_ref = f"s3://real-bucket/raw/{created.submission_id}/input.txt"
            await repo.link_artifact(
                item_id=created.submission_id,
                stage="raw",
                artifact_ref=artifact_ref,
                artifact_version=None,
            )

            pool = manager.pool
            assert pool is not None
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT bucket, object_key FROM artifacts ORDER BY id DESC LIMIT 1"
                )

            assert row is not None
            assert row["bucket"] == "real-bucket"
            assert row["object_key"] == f"raw/{created.submission_id}/input.txt"
            assert await repo.get_artifact_ref(item_id=created.submission_id, stage="raw") == artifact_ref
        finally:
            await manager.shutdown()

    asyncio.run(_run())


@pytest.mark.integration
def test_artifact_link_rejects_malformed_ref_without_persisting_row() -> None:
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
                source_external_id="artifact-link-bad-ref",
                initial_status="uploaded",
            )

            with pytest.raises(DomainInvariantError, match="invalid artifact reference"):
                await repo.link_artifact(
                    item_id=created.submission_id,
                    stage="raw",
                    artifact_ref="s3://real-bucket/tmp/not-allowed.txt",
                    artifact_version=None,
                )

            pool = manager.pool
            assert pool is not None
            async with pool.acquire() as conn:
                count = await conn.fetchval("SELECT COUNT(*) FROM artifacts")

            assert count == 0
        finally:
            await manager.shutdown()

    asyncio.run(_run())


@pytest.mark.integration
def test_real_s3_artifact_link_round_trip_when_credentials_available() -> None:
    dsn = require_postgres()
    s3_settings = _require_real_s3_settings()

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
                source_external_id="artifact-link-real-s3",
                initial_status="uploaded",
            )

            client = build_s3_storage_client(
                endpoint_url=s3_settings["endpoint_url"],
                bucket=s3_settings["bucket"],
                access_key_id=s3_settings["access_key_id"],
                secret_access_key=s3_settings["secret_access_key"],
                region=s3_settings["region"],
            )
            object_key = f"raw/{created.submission_id}/artifact-metadata-real-bucket-key.txt"
            payload = b"artifact-metadata-real-bucket-key"
            artifact_ref = client.put_bytes(key=object_key, payload=payload)
            assert client.get_bytes(key=object_key) == payload

            await repo.link_artifact(
                item_id=created.submission_id,
                stage="raw",
                artifact_ref=artifact_ref,
                artifact_version=None,
            )

            pool = manager.pool
            assert pool is not None
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT bucket, object_key FROM artifacts ORDER BY id DESC LIMIT 1"
                )

            assert row is not None
            assert row["bucket"] == s3_settings["bucket"]
            assert row["object_key"] == object_key
        finally:
            await manager.shutdown()

    asyncio.run(_run())


async def _seed_candidate_assignment(repo: PostgresWorkRepository) -> tuple[str, str]:
    candidate = await repo.create_candidate(first_name="Seed", last_name="Candidate")
    assignment = await repo.create_assignment(title="Seed Assignment", description="seed")
    return candidate.candidate_public_id, assignment.assignment_public_id


def _require_real_s3_settings() -> dict[str, str]:
    endpoint_url = os.getenv("S3_ENDPOINT_URL", "").strip()
    bucket = os.getenv("S3_BUCKET", "").strip()
    access_key_id = os.getenv("S3_ACCESS_KEY_ID", "").strip()
    secret_access_key = os.getenv("S3_SECRET_ACCESS_KEY", "").strip()
    region = os.getenv("S3_REGION", "us-east-1").strip()

    if not bucket or not access_key_id or not secret_access_key:
        pytest.skip("real S3 credentials are not configured")

    return {
        "endpoint_url": endpoint_url,
        "bucket": bucket,
        "access_key_id": access_key_id,
        "secret_access_key": secret_access_key,
        "region": region,
    }
