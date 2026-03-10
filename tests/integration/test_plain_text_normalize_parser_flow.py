from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.clients.stub import StubLLMClient, StubStorageClient, StubTelegramClient
from app.domain.evaluation_contracts import parse_task_schema
from app.domain.dto import LLMClientResult, NormalizePayloadCommand, NormalizationTaskInput
from app.domain.use_cases.normalize import normalize_payload
from app.lib.artifacts.types import NormalizedArtifact
from app.domain.models import WorkItemClaim
from app.lib.artifacts import build_artifact_repository
from app.repositories.stub import InMemoryWorkRepository
from app.workers.handlers import normalize
from app.workers.handlers.deps import WorkerDeps


CASES_DIR = Path("tests/data/normalization/cases")


@dataclass(frozen=True)
class _ParserLLM:
    raw_json: dict[str, object]

    def evaluate(self, request: object) -> LLMClientResult:
        del request
        return LLMClientResult(
            raw_text=json.dumps(self.raw_json, ensure_ascii=False),
            raw_json=dict(self.raw_json),
            tokens_input=1,
            tokens_output=1,
            latency_ms=1,
        )


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


async def _run_case(*, case_name: str, input_name: str) -> tuple[NormalizedArtifact, str]:
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
        description="Shared normalization",
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
    return artifact, created.submission_id


@pytest.mark.integration
def test_parser_backed_plain_text_normalization_for_repeated_prompts() -> None:
    artifact, _ = asyncio.run(
        _run_case(
            case_name="case_002_plain_text_repeated_prompts",
            input_name="input.txt",
        )
    )
    assert artifact.schema_version == "normalized:v2"
    assert len(artifact.task_solutions) == 2
    assert all(isinstance(item.get("answer"), str) for item in artifact.task_solutions)


@pytest.mark.integration
def test_parser_backed_plain_text_normalization_for_answer_only_markdown() -> None:
    artifact, _ = asyncio.run(
        _run_case(
            case_name="case_003_plain_text_answer_only",
            input_name="input.md",
        )
    )
    assert artifact.schema_version == "normalized:v2"
    assert len(artifact.task_solutions) == 2


@pytest.mark.integration
def test_parser_backed_office_normalization_persists_normalized_v2_for_docx_and_odt() -> None:
    docx_artifact, docx_submission_id = asyncio.run(
        _run_case(
            case_name="case_006_docx_text_only",
            input_name="input.docx",
        )
    )
    odt_artifact, odt_submission_id = asyncio.run(
        _run_case(
            case_name="case_008_odt_text_only",
            input_name="input.odt",
        )
    )

    assert docx_artifact.submission_public_id == docx_submission_id
    assert docx_artifact.schema_version == "normalized:v2"
    assert "Task 1 prompt: identify one schema issue and propose a fix." in docx_artifact.submission_text
    assert docx_artifact.task_solutions

    assert odt_artifact.submission_public_id == odt_submission_id
    assert odt_artifact.schema_version == "normalized:v2"
    assert "SELECT id, email FROM users WHERE deleted_at = NULL;" in odt_artifact.submission_text
    assert odt_artifact.task_solutions


@pytest.mark.integration
def test_parser_backed_office_normalization_accepts_misnamed_docx_by_signature() -> None:
    artifact, _ = asyncio.run(
        _run_case(
            case_name="case_014_misnamed_docx_signature",
            input_name="submission.bin",
        )
    )
    assert artifact.schema_version == "normalized:v2"
    assert "Database review submission" in artifact.submission_text


@pytest.mark.integration
def test_parser_backed_pdf_normalization_persists_normalized_v2_for_native_text_case() -> None:
    artifact, _ = asyncio.run(
        _run_case(
            case_name="case_009_pdf_native_text",
            input_name="input.pdf",
        )
    )
    assert artifact.schema_version == "normalized:v2"
    assert "SQL debug assignment - PDF export" in artifact.submission_text
    assert artifact.task_solutions


@pytest.mark.integration
def test_parser_backed_pdf_normalization_merges_ocr_stub_text_before_parser() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "answer-1"},
            {"task_id": "task_2", "answer": "answer-2"},
        ],
        "unmapped_text": "",
    }
    case_dir = CASES_DIR / "case_010_pdf_mixed_native_and_scanned"
    payload = (case_dir / "input.pdf").read_bytes()
    ocr_text = json.loads((case_dir / "ocr_stub.json").read_text(encoding="utf-8"))["text"]

    def _ocr_provider(_: bytes, page_indexes: tuple[int, ...]) -> dict[int, str]:
        return {page_index: str(ocr_text) for page_index in page_indexes}

    result = normalize_payload(
        NormalizePayloadCommand(
            submission_id="sub_case_010",
            artifact_ref="raw/sub_case_010/input.pdf",
            filename="input.pdf",
            source_type="api_upload",
            persisted_mime="application/pdf",
            raw_payload=payload,
            assignment_public_id="asg_test",
            assignment_language="en",
            assignment_tasks=(
                NormalizationTaskInput(task_id="task_1", task_index=1, task_text="Task 1"),
                NormalizationTaskInput(task_id="task_2", task_index=2, task_text="Task 2"),
            ),
        ),
        llm=_ParserLLM(parser_output),
        pdf_ocr_provider=_ocr_provider,
    )

    assert result.normalized_artifact.schema_version == "normalized:v2"
    assert "Scanned addendum. Photo fragment." in result.normalized_artifact.submission_text
    assert result.pdf_extraction is not None
    assert any(page.used_ocr_text for page in result.pdf_extraction.pages)
