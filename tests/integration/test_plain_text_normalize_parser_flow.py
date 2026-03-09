from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.clients.stub import StubLLMClient, StubStorageClient, StubTelegramClient
from app.domain.evaluation_contracts import parse_task_schema
from app.domain.models import WorkItemClaim
from app.lib.artifacts import build_artifact_repository
from app.repositories.stub import InMemoryWorkRepository
from app.workers.handlers import normalize
from app.workers.handlers.deps import WorkerDeps


CASES_DIR = Path("tests/data/normalization/cases")


def _task_schema() -> dict[str, object]:
    return {
        "schema_version": "task-criteria:v1",
        "tasks": [
            {
                "task_id": "task_1",
                "title": "Task one",
                "weight": 0.5,
                "criteria": [{"criterion_id": "c1", "description": "c1", "weight": 1.0}],
            },
            {
                "task_id": "task_2",
                "title": "Task two",
                "weight": 0.5,
                "criteria": [{"criterion_id": "c2", "description": "c2", "weight": 1.0}],
            },
        ],
    }


async def _run_case(*, case_name: str, input_name: str) -> None:
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

    candidate = await repository.create_candidate(first_name="Norm", last_name="Case")
    assignment = await repository.create_assignment(
        title="Normalization",
        description="Plain text normalization",
        language="en",
        task_schema=parse_task_schema(_task_schema()),
    )
    created = await repository.create_submission_with_source(
        candidate_public_id=candidate.candidate_public_id,
        assignment_public_id=assignment.assignment_public_id,
        source_type="api_upload",
        source_external_id=f"src-{case_name}",
        initial_status="uploaded",
        metadata_json={"filename": input_name},
    )

    payload = (CASES_DIR / case_name / input_name).read_bytes()
    raw_ref = storage.put_bytes(key=f"raw/{created.submission_id}/{input_name}", payload=payload)
    await repository.link_artifact(
        item_id=created.submission_id,
        stage="raw",
        artifact_ref=raw_ref,
        artifact_version=None,
    )

    result = await normalize.process_claim(
        deps,
        claim=WorkItemClaim(item_id=created.submission_id, stage="normalized", attempt=1),
    )
    assert result.success is True
    assert result.artifact_ref == f"normalized/{created.submission_id}.json"
    assert result.artifact_ref is not None
    artifact = artifact_repository.load_normalized(artifact_ref=result.artifact_ref)
    assert artifact.schema_version == "normalized:v2"
    assert len(artifact.task_solutions) == 2
    assert all(isinstance(item.get("answer"), str) for item in artifact.task_solutions)


@pytest.mark.integration
def test_parser_backed_plain_text_normalization_for_repeated_prompts() -> None:
    asyncio.run(
        _run_case(
            case_name="case_002_plain_text_repeated_prompts",
            input_name="input.txt",
        )
    )


@pytest.mark.integration
def test_parser_backed_plain_text_normalization_for_answer_only_markdown() -> None:
    asyncio.run(
        _run_case(
            case_name="case_003_plain_text_answer_only",
            input_name="input.md",
        )
    )
