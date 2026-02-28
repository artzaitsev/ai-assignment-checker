import asyncio

import pytest

from app.api.handlers import assignments, candidates, exports, feedback, status, submissions, telegram_webhook
from app.api.handlers.deps import ApiDeps
from app.api.handlers.pipeline import run_test_pipeline_handler
from app.clients.stub import StubLLMClient, StubStorageClient, StubTelegramClient
from app.lib.artifacts import build_artifact_repository
from app.lib.artifacts.types import NormalizedArtifact
from app.domain.models import SortOrder, SubmissionSortBy, SubmissionStatus, WorkItemClaim
from app.repositories.stub import InMemoryWorkRepository
from app.workers.handlers import deliver, evaluate, ingest_telegram, normalize
from app.workers.handlers.deps import WorkerDeps


@pytest.mark.unit
def test_api_handler_component_ids_are_stable() -> None:
    assert submissions.COMPONENT_ID == "api.create_submission"
    assert status.COMPONENT_ID == "api.get_submission_status"
    assert feedback.COMPONENT_ID == "api.list_feedback"
    assert exports.COMPONENT_ID == "api.export_results"
    assert candidates.COMPONENT_ID == "api.create_candidate"
    assert assignments.COMPONENT_ID_CREATE == "api.create_assignment"
    assert assignments.COMPONENT_ID_LIST == "api.list_assignments"
    assert telegram_webhook.COMPONENT_ID == "api.telegram_webhook"


@pytest.mark.unit
def test_worker_handler_component_ids_are_stable() -> None:
    assert ingest_telegram.COMPONENT_ID == "worker.ingest_telegram.process_claim"
    assert normalize.COMPONENT_ID == "worker.normalize.process_claim"
    assert evaluate.COMPONENT_ID == "worker.evaluate.process_claim"
    assert deliver.COMPONENT_ID == "worker.deliver.process_claim"


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
        candidate = await repository.create_candidate(first_name="A", last_name="B")
        assignment = await repository.create_assignment(title="Title", description="Desc")
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
                content_markdown="# normalized",
                normalization_metadata={"producer": "test"},
            ),
        )
        await repository.link_artifact(
            item_id=created.submission_id,
            stage="normalized",
            artifact_ref=f"normalized/{created.submission_id}.json",
            artifact_version="normalized:v1",
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
        assert repository.evaluations
        assert repository.deliveries
        assert telegram.notifications[created.submission_id]
        assert llm.calls
        prompt = llm.calls[0].user_prompt
        assert "Title" in prompt
        assert "Desc" in prompt
        repro = repository.evaluations[0]["reproducibility_subset"]
        assert isinstance(repro, dict)
        assert repro["chain_version"] == "chain:v1"
        assert repro["spec_version"] == "chain-spec:v1"

    asyncio.run(_run())


@pytest.mark.unit
def test_export_handler_uses_storage_contract() -> None:
    storage = StubStorageClient()
    artifact_repository = build_artifact_repository(storage=storage)
    repository = InMemoryWorkRepository()

    async def _run() -> None:
        candidate = await repository.create_candidate(first_name="A", last_name="B")
        assignment = await repository.create_assignment(title="T", description="D")
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
            criteria_scores_json={"items": [{"name": "correctness", "score": 8}]},
            organizer_feedback_json={"strengths": ["s"], "issues": ["i"], "recommendations": ["r"]},
            candidate_feedback_json={"summary": "ok", "what_went_well": ["w"], "what_to_improve": ["m"]},
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
        submission_id = "sub_with_raw"
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
                content_markdown="# normalized",
                normalization_metadata={"producer": "test"},
            ),
        )
        await repository.link_artifact(
            item_id=submission_id,
            stage="normalized",
            artifact_ref=f"normalized/{submission_id}.json",
            artifact_version="normalized:v1",
        )
        result = await evaluate.process_claim(
            deps,
            claim=WorkItemClaim(item_id=submission_id, stage="llm-output", attempt=1),
        )
        assert result.success is False
        assert result.error_code == "artifact_missing"

    asyncio.run(_run())
