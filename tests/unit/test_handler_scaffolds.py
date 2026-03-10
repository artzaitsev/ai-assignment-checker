import asyncio
from pathlib import Path

import pytest

from app.api.handlers import assignments, candidates, exports, feedback, status, submissions
from app.api.handlers.deps import ApiDeps, SubmissionRecord
from app.clients.llm import LLMRetryableError
from app.api.handlers.pipeline import run_test_pipeline_handler
from app.clients.stub import StubLLMClient, StubStorageClient, StubTelegramClient
from app.clients.telegram import TelegramNonRetryableError, TelegramRetryableError
from app.domain.evaluation_contracts import CandidateFeedback, OrganizerFeedback, ScoreBreakdown, TaskScoreBreakdown, CriterionScore, parse_task_schema
from app.lib.artifacts import build_artifact_repository
from app.lib.artifacts.types import NormalizedArtifact
from app.domain.models import (
    CandidateSourceType,
    SortOrder,
    SubmissionSortBy,
    SubmissionStatus,
    TelegramInboundEvent,
    TelegramLinkSettings,
    WorkItemClaim,
)
from app.domain.telegram_settings import TELEGRAM_DEFAULT_ASSIGNMENT_STREAM
from app.repositories.stub import InMemoryWorkRepository
from app.workers.handlers import deliver, evaluate, ingest_telegram, normalize
from app.workers.handlers.deps import WorkerDeps


def _task_schema():
    return parse_task_schema(
        {
            "schema_version": "task-criteria:v1",
            "tasks": [
                {
                    "task_id": "task_main",
                    "title": "Main task",
                    "weight": 1.0,
                    "criteria": [
                        {"criterion_id": "correctness", "description": "c", "weight": 1.0},
                    ],
                }
            ],
        }
    )


def _score_breakdown(*, chain_snapshot: dict[str, object] | None = None):
    return ScoreBreakdown(
        schema_version="task-criteria:v1",
        tasks=(
            TaskScoreBreakdown(
                task_id="task_main",
                score_1_10=8,
                weight=1.0,
                criteria=(
                    CriterionScore(
                        criterion_id="correctness",
                        score=8,
                        reason="ok",
                        weight=1.0,
                    ),
                ),
            ),
        ),
        overall_score_1_10_derived=8,
        chain_snapshot=chain_snapshot,
    )


@pytest.mark.unit
def test_api_handler_component_ids_are_stable() -> None:
    assert submissions.COMPONENT_ID == "api.create_submission"
    assert status.COMPONENT_ID == "api.get_submission_status"
    assert feedback.COMPONENT_ID == "api.list_feedback"
    assert exports.COMPONENT_ID == "api.export_results"
    assert candidates.COMPONENT_ID == "api.create_candidate"
    assert assignments.COMPONENT_ID_CREATE == "api.create_assignment"
    assert assignments.COMPONENT_ID_LIST == "api.list_assignments"


@pytest.mark.unit
def test_worker_handler_component_ids_are_stable() -> None:
    assert ingest_telegram.COMPONENT_ID == "worker.ingest_telegram.process_claim"
    assert normalize.COMPONENT_ID == "worker.normalize.process_claim"
    assert evaluate.COMPONENT_ID == "worker.evaluate.process_claim"
    assert deliver.COMPONENT_ID == "worker.deliver.process_claim"


@pytest.mark.unit
def test_ingest_telegram_handler_polls_updates() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    telegram = StubTelegramClient()
    llm = StubLLMClient()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
        telegram_link_settings=TelegramLinkSettings(
            public_web_base_url="https://portal.example.com",
            signing_secret="test-secret-012345",
            ttl_seconds=600,
        ),
    )

    async def _run() -> None:
        assignment = await repository.create_assignment(
            title="Telegram Assignment",
            description="Desc",
            language="ru",
            task_schema=_task_schema(),
        )
        await repository.set_stream_cursor(
            stream=TELEGRAM_DEFAULT_ASSIGNMENT_STREAM,
            cursor=assignment.assignment_public_id,
        )
        telegram.events.append(
            TelegramInboundEvent(
                update_id="upd_001",
                chat_id="chat_1",
                telegram_user_id="tg_user_1",
                kind="message",
                command="/start",
                text="/start",
            )
        )

        claim = WorkItemClaim(item_id="poll-tick-1", stage="raw", attempt=1)
        result = await ingest_telegram.process_claim(deps, claim=claim)

        assert result.success is True
        assert "processed 1 telegram events" in result.detail
        assert telegram.sent_texts
        assert telegram.sent_texts[0][0] == "chat_1"
        assert "/candidate/assignments/" in telegram.sent_texts[0][1]
        assert "/apply?token=" in telegram.sent_texts[0][1]

    asyncio.run(_run())


@pytest.mark.unit
def test_ingest_telegram_handler_idle_when_no_updates() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    telegram = StubTelegramClient()
    llm = StubLLMClient()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
        telegram_link_settings=TelegramLinkSettings(
            public_web_base_url="https://portal.example.com",
            signing_secret="test-secret-012345",
            ttl_seconds=600,
        ),
    )

    async def _run() -> None:
        claim = WorkItemClaim(item_id="poll-tick-2", stage="raw", attempt=1)
        result = await ingest_telegram.process_claim(deps, claim=claim)

        assert result.success is True
        assert "no new telegram events" in result.detail

    asyncio.run(_run())


@pytest.mark.unit
def test_ingest_telegram_handler_skips_start_when_assignment_not_configured() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    telegram = StubTelegramClient()
    llm = StubLLMClient()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
        telegram_link_settings=TelegramLinkSettings(
            public_web_base_url="https://portal.example.com",
            signing_secret="test-secret-012345",
            ttl_seconds=600,
        ),
    )

    async def _run() -> None:
        telegram.events.append(
            TelegramInboundEvent(
                update_id="upd_001a",
                chat_id="chat_1a",
                telegram_user_id="tg_user_1a",
                kind="message",
                command="/start",
                text="/start",
            )
        )

        claim = WorkItemClaim(item_id="poll-tick-1a", stage="raw", attempt=1)
        result = await ingest_telegram.process_claim(deps, claim=claim)

        assert result.success is True
        assert "processed 0 telegram events" in result.detail
        assert "skipped 1" in result.detail
        assert telegram.sent_texts == []

    asyncio.run(_run())


@pytest.mark.unit
def test_ingest_telegram_handler_sends_standard_help_for_unsupported_events() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    telegram = StubTelegramClient()
    llm = StubLLMClient()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
        telegram_link_settings=TelegramLinkSettings(
            public_web_base_url="https://portal.example.com",
            signing_secret="test-secret-012345",
            ttl_seconds=600,
        ),
    )

    async def _run() -> None:
        telegram.events.append(
            TelegramInboundEvent(
                update_id="upd_002",
                chat_id="chat_2",
                telegram_user_id="tg_user_2",
                kind="message",
                command="/help",
                text="/help",
            )
        )
        claim = WorkItemClaim(item_id="poll-tick-3", stage="raw", attempt=1)
        result = await ingest_telegram.process_claim(deps, claim=claim)

        assert result.success is True
        assert "processed 1 telegram events" in result.detail
        assert len(telegram.sent_texts) == 1
        assert telegram.sent_texts[0][1].startswith("Я помогу Вам начать подачу заявки")
        assert repository.submissions == {}
        assert storage.writes == []

    asyncio.run(_run())


@pytest.mark.unit
def test_ingest_telegram_handler_uses_next_offset_from_stream_cursor() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    telegram = StubTelegramClient()
    llm = StubLLMClient()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
        telegram_link_settings=TelegramLinkSettings(
            public_web_base_url="https://portal.example.com",
            signing_secret="test-secret-012345",
            ttl_seconds=600,
        ),
    )

    async def _run() -> None:
        await repository.set_stream_cursor(stream=ingest_telegram.TELEGRAM_UPDATES_STREAM, cursor="41")
        claim = WorkItemClaim(item_id="poll-tick-4", stage="raw", attempt=1)
        result = await ingest_telegram.process_claim(deps, claim=claim)

        assert result.success is True
        assert telegram.last_poll_offset == "42"

    asyncio.run(_run())


@pytest.mark.unit
def test_ingest_telegram_handler_maps_retryable_telegram_errors() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    llm = StubLLMClient()

    class _FailingTelegramClient:
        def poll_events(self, *, timeout: int = 30, offset: str | None = None) -> list[TelegramInboundEvent]:
            del timeout, offset
            raise TelegramRetryableError("temporary network issue")

        def send_text(self, *, chat_id: str, message: str) -> str | None:
            del chat_id, message
            return None

    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=_FailingTelegramClient(),
        llm=llm,
        telegram_link_settings=TelegramLinkSettings(
            public_web_base_url="https://portal.example.com",
            signing_secret="test-secret-012345",
            ttl_seconds=600,
        ),
    )

    async def _run() -> None:
        claim = WorkItemClaim(item_id="poll-tick-5", stage="raw", attempt=1)
        result = await ingest_telegram.process_claim(deps, claim=claim)

        assert result.success is False
        assert result.error_code == "telegram_file_fetch_failed"
        assert result.retry_classification == "recoverable"

    asyncio.run(_run())


@pytest.mark.unit
def test_ingest_telegram_handler_maps_non_retryable_telegram_errors() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    llm = StubLLMClient()

    class _FailingTelegramClient:
        def poll_events(self, *, timeout: int = 30, offset: str | None = None) -> list[TelegramInboundEvent]:
            del timeout, offset
            raise TelegramNonRetryableError("invalid bot token")

        def send_text(self, *, chat_id: str, message: str) -> str | None:
            del chat_id, message
            return None

    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=_FailingTelegramClient(),
        llm=llm,
        telegram_link_settings=TelegramLinkSettings(
            public_web_base_url="https://portal.example.com",
            signing_secret="test-secret-012345",
            ttl_seconds=600,
        ),
    )

    async def _run() -> None:
        claim = WorkItemClaim(item_id="poll-tick-6", stage="raw", attempt=1)
        result = await ingest_telegram.process_claim(deps, claim=claim)

        assert result.success is False
        assert result.error_code == "validation_error"
        assert result.retry_classification == "terminal"

    asyncio.run(_run())


@pytest.mark.unit
def test_handlers_execute_skeleton_flow() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    telegram = StubTelegramClient()
    llm = StubLLMClient()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
    )

    async def _run() -> None:
        candidate = await repository.get_or_create_candidate_by_source(
            source_type="telegram_chat",
            source_external_id="chat-flow",
            first_name="A",
            last_name="B",
        )
        assignment = await repository.create_assignment(
            title="Title",
            description="Desc",
            language="en",
            task_schema=_task_schema(),
        )
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="s-1",
            initial_status="normalized",
        )
        artifact_repository.save_normalized(
            submission_id=created.submission_id,
            artifact=NormalizedArtifact(
                submission_public_id=created.submission_id,
                assignment_public_id=assignment.assignment_public_id,
                source_type="api_upload",
                submission_text="# normalized",
                task_solutions=[],
                unmapped_text="",
            ),
        )
        await repository.link_artifact(
            item_id=created.submission_id,
            stage="normalized",
            artifact_ref=f"normalized/{created.submission_id}.json",
            artifact_version="normalized:v2",
        )

        claim = WorkItemClaim(item_id=created.submission_id, stage="llm-output", attempt=1)

        evaluate_result = await evaluate.process_claim(deps, claim=claim)
        await repository.transition_state(item_id=claim.item_id, from_state="evaluated", to_state="delivery_in_progress")
        deliver_result = await deliver.process_claim(deps, claim=claim)

        assert evaluate_result.success is True
        assert evaluate_result.artifact_ref is None
        assert deliver_result.success is True
        assert deliver_result.artifact_ref is None
        assert repository.llm_runs
        assert repository.llm_runs[0]["api_base"] == llm.base_url
        assert repository.evaluations
        assert repository.deliveries
        assert telegram.sent_texts
        assert llm.calls
        prompt = llm.calls[0].user_prompt
        assert "Title" in prompt
        assert "Desc" in prompt
        repro = repository.evaluations[0]["reproducibility_subset"]
        assert isinstance(repro, dict)
        assert repro["chain_version"] == "chain:v1"
        assert repro["spec_version"] == "chain-spec:v1"
        score_payload = repository.evaluations[0]["score_breakdown"]
        assert isinstance(score_payload, dict)
        snapshot = score_payload["_chain_snapshot"]
        assert isinstance(snapshot, dict)
        assert isinstance(snapshot.get("chain_digest"), str)
        assert snapshot.get("mismatch_policy") == "warn-only"
        assert isinstance(snapshot.get("resolved_chain_spec"), dict)
        diagnostics = snapshot.get("evaluation_diagnostics")
        assert isinstance(diagnostics, dict)
        assert diagnostics.get("fallback_used") is False

    asyncio.run(_run())


@pytest.mark.unit
def test_export_handler_uses_storage_contract() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()

    async def _run() -> None:
        candidate = await repository.create_candidate(first_name="A", last_name="B")
        assignment = await repository.create_assignment(title="T", description="D", language="en", task_schema=_task_schema())
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="sub-1",
            initial_status="evaluated",
        )
        await repository.persist_llm_run(
            submission_id=created.submission_id,
            provider="openai-compatible",
            model="model:v1",
            api_base="https://example.invalid",
            chain_version="chain:v1",
            spec_version="chain-spec:v1",
            response_language="ru",
            temperature=0.1,
            seed=42,
            tokens_input=0,
            tokens_output=0,
            latency_ms=0,
        )
        await repository.persist_evaluation(
            submission_id=created.submission_id,
            score_1_10=8,
            score_breakdown=_score_breakdown(),
            organizer_feedback=OrganizerFeedback(strengths=("s",), issues=("i",), recommendations=("r",)),
            candidate_feedback=CandidateFeedback(summary="ok", what_went_well=("w",), what_to_improve=("m",)),
            ai_assistance_likelihood=0.35,
            ai_assistance_confidence=0.55,
            reproducibility_subset={
                "chain_version": "chain:v1",
                "spec_version": "chain-spec:v1",
                "model": "model:v1",
                "response_language": "ru",
            },
        )

        result = await exports.export_results_handler(
            ApiDeps(
                repository=repository,
                artifact_repository=artifact_repository,
                storage=storage,
                telegram=StubTelegramClient(),
                llm=StubLLMClient(),
                submissions={},
            ),
            statuses=(SubmissionStatus.EVALUATED,),
            candidate_public_id=None,
            assignment_public_id=None,
            source_type=None,
            sort_by=SubmissionSortBy.CREATED_AT,
            sort_order=SortOrder.DESC,
            limit=100,
            offset=0,
        )
        assert result.rows_count == 1
        assert result.export_id.startswith("exp_")
        assert result.download_url == f"/exports/{result.export_id}/download"
        assert result.export_ref.startswith("s3://exports/")

    asyncio.run(_run())


@pytest.mark.unit
def test_feedback_handler_reads_persisted_feedback_with_optional_filter() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    deps = ApiDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
        submissions={},
    )

    async def _run() -> None:
        candidate = await repository.create_candidate(first_name="F", last_name="B")
        assignment = await repository.create_assignment(title="A", description="D", language="en", task_schema=_task_schema())
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="feedback-1",
            initial_status="evaluated",
        )
        await repository.persist_evaluation(
            submission_id=created.submission_id,
            score_1_10=9,
            score_breakdown=_score_breakdown(),
            organizer_feedback=OrganizerFeedback(strengths=("s",), issues=("i",), recommendations=("r",)),
            candidate_feedback=CandidateFeedback(summary="ok", what_went_well=("w",), what_to_improve=("m",)),
            ai_assistance_likelihood=0.25,
            ai_assistance_confidence=0.5,
            reproducibility_subset={
                "chain_version": "chain:v1",
                "spec_version": "chain-spec:v1",
                "model": "model:v1",
                "response_language": "en",
            },
        )

        payload = await feedback.list_feedback_handler(deps=deps, submission_id=None)
        assert len(payload.items) == 1
        assert payload.items[0]["submission_id"] == created.submission_id
        ai_assistance = payload.items[0]["ai_assistance"]
        assert isinstance(ai_assistance, dict)
        assert ai_assistance["likelihood"] == pytest.approx(0.25)
        assert ai_assistance["confidence"] == pytest.approx(0.5)

        filtered = await feedback.list_feedback_handler(deps=deps, submission_id=created.submission_id)
        assert len(filtered.items) == 1
        empty = await feedback.list_feedback_handler(deps=deps, submission_id="sub_01H0000000000000000000000")
        assert empty.items == []

    asyncio.run(_run())


@pytest.mark.unit
def test_deliver_handler_skips_when_telegram_chat_mapping_is_missing() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    telegram = StubTelegramClient()
    llm = StubLLMClient()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=telegram,
        llm=llm,
    )

    async def _run() -> None:
        candidate = await repository.create_candidate(first_name="Del", last_name="Iv")
        assignment = await repository.create_assignment(title="D", description="D", language="en", task_schema=_task_schema())
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="deliver-missing-chat",
            initial_status="evaluated",
        )
        await repository.persist_evaluation(
            submission_id=created.submission_id,
            score_1_10=8,
            score_breakdown=_score_breakdown(),
            organizer_feedback=OrganizerFeedback(strengths=(), issues=(), recommendations=()),
            candidate_feedback=CandidateFeedback(summary="ok", what_went_well=(), what_to_improve=()),
            ai_assistance_likelihood=0.1,
            ai_assistance_confidence=0.1,
            reproducibility_subset={
                "chain_version": "chain:v1",
                "spec_version": "spec:v1",
                "model": "model:v1",
                "response_language": "en",
            },
        )
        result = await deliver.process_claim(
            deps,
            claim=WorkItemClaim(item_id=created.submission_id, stage="exports", attempt=1),
        )
        assert result.success is True
        assert telegram.sent_texts == []
        assert len(repository.deliveries) == 1
        assert repository.deliveries[0]["status"] == "skipped"
        assert repository.deliveries[0]["attempts"] == 1
        assert repository.deliveries[0]["last_error_code"] is None

    asyncio.run(_run())


@pytest.mark.unit
def test_deliver_handler_persists_retryable_transport_failure_with_attempt() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    llm = StubLLMClient()

    class _FailingTelegramClient:
        sent_texts: list[tuple[str, str]] = []

        def send_text(self, *, chat_id: str, message: str) -> str | None:
            del chat_id, message
            raise TelegramRetryableError("transport failed")

        def poll_events(self, *, timeout: int = 30, offset: str | None = None):
            del timeout, offset
            return []

    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=_FailingTelegramClient(),
        llm=llm,
    )

    async def _run() -> None:
        candidate = await repository.get_or_create_candidate_by_source(
            source_type=CandidateSourceType.TELEGRAM_CHAT,
            source_external_id="chat-retry",
            first_name="Retry",
            last_name="Case",
        )
        assignment = await repository.create_assignment(title="D", description="D", language="en", task_schema=_task_schema())
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="deliver-transport-retry",
            initial_status="evaluated",
        )
        await repository.persist_evaluation(
            submission_id=created.submission_id,
            score_1_10=8,
            score_breakdown=_score_breakdown(),
            organizer_feedback=OrganizerFeedback(strengths=(), issues=(), recommendations=()),
            candidate_feedback=CandidateFeedback(summary="ok", what_went_well=(), what_to_improve=()),
            ai_assistance_likelihood=0.1,
            ai_assistance_confidence=0.1,
            reproducibility_subset={
                "chain_version": "chain:v1",
                "spec_version": "spec:v1",
                "model": "model:v1",
                "response_language": "en",
            },
        )
        result = await deliver.process_claim(
            deps,
            claim=WorkItemClaim(item_id=created.submission_id, stage="exports", attempt=3),
        )
        assert result.success is False
        assert result.error_code == "delivery_transport_failed"
        assert len(repository.deliveries) == 1
        assert repository.deliveries[0]["status"] == "failed"
        assert repository.deliveries[0]["attempts"] == 3
        assert repository.deliveries[0]["last_error_code"] == "delivery_transport_failed"

    asyncio.run(_run())


@pytest.mark.unit
def test_api_deps_can_be_constructed() -> None:
    storage = StubStorageClient()
    deps = ApiDeps(
        repository=InMemoryWorkRepository(),
        artifact_repository=build_artifact_repository(storage=storage),
        storage=storage,
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
        submissions={},
    )
    assert deps is not None


@pytest.mark.unit
def test_synthetic_pipeline_handler_returns_none_for_missing_submission() -> None:
    storage = StubStorageClient()
    deps = ApiDeps(
        repository=InMemoryWorkRepository(),
        artifact_repository=build_artifact_repository(storage=storage),
        storage=storage,
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
        submissions={},
    )

    async def _run() -> None:
        result = await run_test_pipeline_handler(deps, submission_id="missing")
        assert result is None

    asyncio.run(_run())


@pytest.mark.unit
def test_status_handler_prefers_repository_state_over_stale_uploaded_trace() -> None:
    storage = StubStorageClient()
    repository = InMemoryWorkRepository()
    deps = ApiDeps(
        repository=repository,
        artifact_repository=build_artifact_repository(storage=storage),
        storage=storage,
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
        submissions={},
    )

    async def _run() -> None:
        candidate = await repository.create_candidate(first_name="S", last_name="T")
        assignment = await repository.create_assignment(title="A", description="D", language="en", task_schema=_task_schema())
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="status-stale",
            initial_status="uploaded",
        )
        submission_id = created.submission_id
        deps.submissions[submission_id] = SubmissionRecord(
            submission_id=submission_id,
            state="uploaded",
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            transitions=["uploaded"],
            artifacts={},
        )

        await repository.transition_state(item_id=submission_id, from_state="uploaded", to_state="normalization_in_progress")
        await repository.transition_state(item_id=submission_id, from_state="normalization_in_progress", to_state="normalized")

        payload = await status.get_submission_status_with_trace_handler(deps, submission_id=submission_id)
        assert payload is not None
        assert payload.state == "normalized"

    asyncio.run(_run())


@pytest.mark.unit
def test_status_handler_keeps_repository_state_authoritative_when_trace_is_ahead() -> None:
    storage = StubStorageClient()
    repository = InMemoryWorkRepository()
    deps = ApiDeps(
        repository=repository,
        artifact_repository=build_artifact_repository(storage=storage),
        storage=storage,
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
        submissions={},
    )

    async def _run() -> None:
        candidate = await repository.create_candidate(first_name="S", last_name="T")
        assignment = await repository.create_assignment(title="A", description="D", language="en", task_schema=_task_schema())
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="status-pipeline",
            initial_status="uploaded",
        )
        submission_id = created.submission_id
        deps.submissions[submission_id] = SubmissionRecord(
            submission_id=submission_id,
            state="failed_evaluation",
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            transitions=["uploaded", "normalization_in_progress", "normalized", "evaluation_in_progress", "failed_evaluation"],
            artifacts={"raw": f"raw/{submission_id}/task.txt"},
        )

        payload = await status.get_submission_status_with_trace_handler(deps, submission_id=submission_id)
        assert payload is not None
        assert payload.state == "uploaded"
        assert payload.transitions is not None
        assert payload.transitions[-1] == "failed_evaluation"

    asyncio.run(_run())


@pytest.mark.unit
def test_status_handler_returns_none_when_submission_exists_only_in_trace() -> None:
    storage = StubStorageClient()
    repository = InMemoryWorkRepository()
    deps = ApiDeps(
        repository=repository,
        artifact_repository=build_artifact_repository(storage=storage),
        storage=storage,
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
        submissions={},
    )

    async def _run() -> None:
        submission_id = "sub_trace_only"
        deps.submissions[submission_id] = SubmissionRecord(
            submission_id=submission_id,
            state="failed_evaluation",
            candidate_public_id="cand_01H0000000000000000000000",
            assignment_public_id="asg_01H0000000000000000000000",
            transitions=["uploaded", "failed_evaluation"],
            artifacts={"raw": "s3://raw/sub_trace_only/task.txt"},
        )
        payload = await status.get_submission_status_with_trace_handler(deps, submission_id=submission_id)
        assert payload is None

    asyncio.run(_run())


@pytest.mark.unit
def test_normalize_handler_returns_artifact_missing_without_raw_link() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
    )

    async def _run() -> None:
        result = await normalize.process_claim(
            deps,
            claim=WorkItemClaim(item_id="sub_missing_raw", stage="normalized", attempt=1),
        )
        assert result.success is False
        assert result.error_code == "artifact_missing"

    asyncio.run(_run())


@pytest.mark.unit
def test_normalize_handler_uses_linked_raw_artifact_ref() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
    )

    async def _run() -> None:
        candidate = await repository.create_candidate(first_name="N", last_name="R")
        assignment = await repository.create_assignment(
            title="Normalize",
            description="Normalize plain text",
            language="en",
            task_schema=_task_schema(),
        )
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="norm-1",
            initial_status="uploaded",
            metadata_json={"filename": "task.txt"},
        )
        submission_id = created.submission_id
        raw_ref = storage.put_bytes(key=f"raw/{submission_id}/task.txt", payload=b"print('hello')")
        await repository.link_artifact(
            item_id=submission_id,
            stage="raw",
            artifact_ref=raw_ref,
            artifact_version=None,
        )

        result = await normalize.process_claim(
            deps,
            claim=WorkItemClaim(item_id=submission_id, stage="normalized", attempt=1),
        )
        assert result.success is True
        assert result.artifact_ref == f"normalized/{submission_id}.json"

    asyncio.run(_run())


@pytest.mark.unit
def test_normalize_handler_maps_llm_retryable_errors_as_recoverable() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()

    class _RetryableLLM:
        def evaluate(self, request: object):
            del request
            raise LLMRetryableError("temporary provider timeout")

    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=StubTelegramClient(),
        llm=_RetryableLLM(),
    )

    async def _run() -> None:
        candidate = await repository.create_candidate(first_name="N", last_name="R")
        assignment = await repository.create_assignment(
            title="Normalize",
            description="Normalize plain text",
            language="en",
            task_schema=_task_schema(),
        )
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="norm-llm-retryable",
            initial_status="uploaded",
            metadata_json={"filename": "task.txt"},
        )
        submission_id = created.submission_id
        raw_ref = storage.put_bytes(key=f"raw/{submission_id}/task.txt", payload=b"print('hello')")
        await repository.link_artifact(
            item_id=submission_id,
            stage="raw",
            artifact_ref=raw_ref,
            artifact_version=None,
        )

        result = await normalize.process_claim(
            deps,
            claim=WorkItemClaim(item_id=submission_id, stage="normalized", attempt=1),
        )
        assert result.success is False
        assert result.error_code == "llm_provider_unavailable"
        assert result.retry_classification == "recoverable"

    asyncio.run(_run())


@pytest.mark.unit
def test_normalize_handler_rejects_unsupported_format_before_parser_call() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()

    class _NeverCalledLLM:
        def evaluate(self, request: object):
            del request
            raise AssertionError("LLM must not be called for unsupported format")

    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=StubTelegramClient(),
        llm=_NeverCalledLLM(),
    )

    async def _run() -> None:
        candidate = await repository.create_candidate(first_name="N", last_name="R")
        assignment = await repository.create_assignment(
            title="Normalize",
            description="Normalize plain text",
            language="en",
            task_schema=_task_schema(),
        )
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="norm-unsupported",
            initial_status="uploaded",
            metadata_json={"filename": "payload.bin"},
        )
        submission_id = created.submission_id
        raw_ref = storage.put_bytes(key=f"raw/{submission_id}/payload.bin", payload=b"\x00\x01\x02\x03")
        await repository.link_artifact(
            item_id=submission_id,
            stage="raw",
            artifact_ref=raw_ref,
            artifact_version=None,
        )

        result = await normalize.process_claim(
            deps,
            claim=WorkItemClaim(item_id=submission_id, stage="normalized", attempt=1),
        )
        assert result.success is False
        assert result.error_code == "unsupported_format"

    asyncio.run(_run())


@pytest.mark.unit
def test_normalize_handler_maps_corrupt_supported_file_to_parse_failed() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
    )

    async def _run() -> None:
        candidate = await repository.create_candidate(first_name="N", last_name="R")
        assignment = await repository.create_assignment(
            title="Normalize",
            description="Normalize plain text",
            language="en",
            task_schema=_task_schema(),
        )
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="norm-corrupt-docx",
            initial_status="uploaded",
            metadata_json={"filename": "input.docx"},
        )
        submission_id = created.submission_id
        payload = (Path("tests/data/normalization/cases/case_015_corrupt_docx_supported") / "input.docx").read_bytes()
        raw_ref = storage.put_bytes(key=f"raw/{submission_id}/input.docx", payload=payload)
        await repository.link_artifact(
            item_id=submission_id,
            stage="raw",
            artifact_ref=raw_ref,
            artifact_version=None,
        )

        result = await normalize.process_claim(
            deps,
            claim=WorkItemClaim(item_id=submission_id, stage="normalized", attempt=1),
        )
        assert result.success is False
        assert result.error_code == "file_parse_failed"

    asyncio.run(_run())


@pytest.mark.unit
def test_evaluate_handler_returns_artifact_missing_when_assignment_absent() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=StubTelegramClient(),
        llm=StubLLMClient(),
    )

    async def _run() -> None:
        submission_id = "sub_missing_assignment"
        artifact_repository.save_normalized(
            submission_id=submission_id,
            artifact=NormalizedArtifact(
                submission_public_id=submission_id,
                assignment_public_id="asg_missing",
                source_type="api_upload",
                submission_text="# normalized",
                task_solutions=[],
                unmapped_text="",
            ),
        )
        await repository.link_artifact(
            item_id=submission_id,
            stage="normalized",
            artifact_ref=f"normalized/{submission_id}.json",
            artifact_version="normalized:v2",
        )
        result = await evaluate.process_claim(
            deps,
            claim=WorkItemClaim(item_id=submission_id, stage="llm-output", attempt=1),
        )
        assert result.success is False
        assert result.error_code == "artifact_missing"

    asyncio.run(_run())


@pytest.mark.unit
def test_evaluate_handler_warns_on_chain_digest_mismatch_and_continues(caplog: pytest.LogCaptureFixture) -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()
    llm = StubLLMClient()
    deps = WorkerDeps(
        repository=repository,
        artifact_repository=artifact_repository,
        storage=storage,
        telegram=StubTelegramClient(),
        llm=llm,
    )

    async def _run() -> None:
        candidate = await repository.create_candidate(first_name="A", last_name="B")
        assignment = await repository.create_assignment(
            title="Title",
            description="Desc",
            language="en",
            task_schema=_task_schema(),
        )
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="s-mismatch",
            initial_status="normalized",
        )
        artifact_repository.save_normalized(
            submission_id=created.submission_id,
            artifact=NormalizedArtifact(
                submission_public_id=created.submission_id,
                assignment_public_id=assignment.assignment_public_id,
                source_type="api_upload",
                submission_text="# normalized",
                task_solutions=[],
                unmapped_text="",
            ),
        )
        await repository.link_artifact(
            item_id=created.submission_id,
            stage="normalized",
            artifact_ref=f"normalized/{created.submission_id}.json",
            artifact_version="normalized:v2",
        )
        await repository.persist_evaluation(
            submission_id=created.submission_id,
            score_1_10=5,
            score_breakdown=_score_breakdown(
                chain_snapshot={
                    "chain_digest": "stale-digest",
                    "resolved_chain_spec": {},
                    "mismatch_policy": "warn-only",
                }
            ),
            organizer_feedback=OrganizerFeedback(strengths=(), issues=(), recommendations=()),
            candidate_feedback=CandidateFeedback(summary="", what_went_well=(), what_to_improve=()),
            ai_assistance_likelihood=0.1,
            ai_assistance_confidence=0.2,
            reproducibility_subset={
                "chain_version": "chain:v1",
                "spec_version": "chain-spec:v1",
                "model": "model:v1",
                "response_language": "ru",
            },
        )

        result = await evaluate.process_claim(
            deps,
            claim=WorkItemClaim(item_id=created.submission_id, stage="llm-output", attempt=2),
        )
        assert result.success is True

    with caplog.at_level("WARNING", logger="runtime"):
        asyncio.run(_run())
    assert "evaluation chain snapshot mismatch; continuing due to warn-only policy" in caplog.text
