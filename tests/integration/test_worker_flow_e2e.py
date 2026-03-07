from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from app.clients.stub import StubLLMClient, StubStorageClient, StubTelegramClient
from app.domain.dto import LLMClientResult
from app.lib.artifacts import build_artifact_repository
from app.repositories.stub import InMemoryWorkRepository
from app.workers.handlers.deps import WorkerDeps
from app.workers.handlers.factory import build_process_handler
from app.workers.loop import WorkerLoop


@pytest.mark.integration
def test_worker_loops_cover_full_backend_flow() -> None:
    async def _run() -> None:
        repository = InMemoryWorkRepository()
        storage = StubStorageClient()
        artifact_repository = build_artifact_repository(storage=storage)
        telegram = StubTelegramClient()
        llm = StubLLMClient()
        deps = WorkerDeps(
            repository=repository,
            artifact_repository=artifact_repository,
            storage=storage,
            telegram=telegram,
            llm=llm,
        )

        candidate = await repository.get_or_create_candidate_by_source(
            source_type="telegram_chat",
            source_external_id="chat-flow-e2e",
            first_name="Flow",
            last_name="Candidate",
        )
        assignment = await repository.create_assignment(title="Flow Assignment", description="Flow Description")
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="flow-e2e-1",
            initial_status="uploaded",
        )
        raw_ref = storage.put_bytes(
            key=f"raw/{created.submission_id}/submission.txt",
            payload=b"print('hello')",
        )
        await repository.link_artifact(
            item_id=created.submission_id,
            stage="raw",
            artifact_ref=raw_ref,
            artifact_version=None,
        )

        normalize_loop = WorkerLoop(
            role="worker-normalize",
            stage="normalized",
            repository=repository,
            process=build_process_handler("worker-normalize", deps),
        )
        evaluate_loop = WorkerLoop(
            role="worker-evaluate",
            stage="llm-output",
            repository=repository,
            process=build_process_handler("worker-evaluate", deps),
        )
        deliver_loop = WorkerLoop(
            role="worker-deliver",
            stage="exports",
            repository=repository,
            process=build_process_handler("worker-deliver", deps),
        )

        assert await normalize_loop.run_once() is True
        assert await evaluate_loop.run_once() is True
        assert await deliver_loop.run_once() is True

        snapshot = await repository.get_submission(submission_id=created.submission_id)
        assert snapshot is not None
        assert snapshot.status == "delivered"
        assert telegram.sent_texts
        assert telegram.sent_texts[0][0] == "chat-flow-e2e"
        assert repository.llm_runs
        assert repository.llm_runs[0]["api_base"] == llm.base_url
        assert repository.evaluations

    asyncio.run(_run())


@dataclass
class _MultitaskLLMClient(StubLLMClient):
    def evaluate(self, request):  # type: ignore[override]
        self.calls.append(request)
        return LLMClientResult(
            raw_text="",
            raw_json={
                "tasks": [
                    {
                        "task_id": "task_1",
                        "criteria": [
                            {"criterion_id": "correctness", "score": 8, "reason": "good"},
                            {"criterion_id": "clarity", "score": 7, "reason": "ok"},
                        ],
                    },
                    {
                        "task_id": "task_2",
                        "criteria": [
                            {"criterion_id": "coverage", "score": 9, "reason": "strong"},
                        ],
                    },
                ],
                "organizer_feedback": {"strengths": ["s"], "issues": ["i"], "recommendations": ["r"]},
                "candidate_feedback": {"summary": "sum", "what_went_well": ["w"], "what_to_improve": ["m"]},
                "ai_assistance": {"likelihood": 0.3, "confidence": 0.6, "disclaimer": "d"},
            },
            tokens_input=128,
            tokens_output=256,
            latency_ms=120,
        )


@pytest.mark.integration
def test_worker_evaluate_supports_multitask_assignment_criteria() -> None:
    async def _run() -> None:
        repository = InMemoryWorkRepository()
        storage = StubStorageClient()
        artifact_repository = build_artifact_repository(storage=storage)
        telegram = StubTelegramClient()
        llm = _MultitaskLLMClient()
        deps = WorkerDeps(
            repository=repository,
            artifact_repository=artifact_repository,
            storage=storage,
            telegram=telegram,
            llm=llm,
        )

        candidate = await repository.get_or_create_candidate_by_source(
            source_type="telegram_chat",
            source_external_id="chat-flow-multi",
            first_name="Flow",
            last_name="Candidate",
        )
        assignment = await repository.create_assignment(
            title="Flow Multitask",
            description="Flow Description",
            criteria_schema_json={
                "schema_version": "task-criteria:v1",
                "tasks": [
                    {
                        "task_id": "task_1",
                        "title": "Task 1",
                        "weight": 0.6,
                        "criteria": [
                            {"criterion_id": "correctness", "description": "c", "weight": 0.7},
                            {"criterion_id": "clarity", "description": "c", "weight": 0.3},
                        ],
                    },
                    {
                        "task_id": "task_2",
                        "title": "Task 2",
                        "weight": 0.4,
                        "criteria": [
                            {"criterion_id": "coverage", "description": "c", "weight": 1.0},
                        ],
                    },
                ],
            },
        )
        created = await repository.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="flow-e2e-multitask",
            initial_status="uploaded",
        )
        raw_ref = storage.put_bytes(
            key=f"raw/{created.submission_id}/submission.txt",
            payload=b"print('hello')",
        )
        await repository.link_artifact(
            item_id=created.submission_id,
            stage="raw",
            artifact_ref=raw_ref,
            artifact_version=None,
        )

        normalize_loop = WorkerLoop(
            role="worker-normalize",
            stage="normalized",
            repository=repository,
            process=build_process_handler("worker-normalize", deps),
        )
        evaluate_loop = WorkerLoop(
            role="worker-evaluate",
            stage="llm-output",
            repository=repository,
            process=build_process_handler("worker-evaluate", deps),
        )

        assert await normalize_loop.run_once() is True
        assert await evaluate_loop.run_once() is True

        assert repository.evaluations
        criteria_payload = repository.evaluations[0]["criteria_scores_json"]
        assert isinstance(criteria_payload, dict)
        assert criteria_payload["task_order"] == ["task_1", "task_2"]
        assert criteria_payload["task_scores"] == {"task_1": 8, "task_2": 9}
        assert repository.evaluations[0]["score_1_10"] == 8

    asyncio.run(_run())
