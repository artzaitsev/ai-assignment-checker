from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pytest

from app.domain.dto import LLMClientResult, NormalizePayloadCommand, NormalizationTaskInput
from app.domain.use_cases.normalize import normalize_payload


NORMALIZATION_CASES_DIR = Path("tests/data/normalization/cases")


@dataclass(frozen=True)
class _ParserLLM:
    raw_json: Mapping[str, object]

    def evaluate(self, request: object) -> LLMClientResult:
        del request
        return LLMClientResult(
            raw_text=json.dumps(self.raw_json, ensure_ascii=False),
            raw_json=dict(self.raw_json),
            tokens_input=1,
            tokens_output=1,
            latency_ms=1,
        )


def _command(*, payload: bytes, filename: str) -> NormalizePayloadCommand:
    return NormalizePayloadCommand(
        submission_id="sub_test",
        artifact_ref=f"raw/sub_test/{filename}",
        filename=filename,
        source_type="api_upload",
        persisted_mime="text/plain",
        raw_payload=payload,
        assignment_public_id="asg_test",
        assignment_language="en",
        assignment_tasks=(
            NormalizationTaskInput(task_id="task_1", task_index=1, task_text="Task 1"),
            NormalizationTaskInput(task_id="task_2", task_index=2, task_text="Task 2"),
        ),
    )


@pytest.mark.unit
def test_normalize_payload_supports_suffixless_and_plain_text_decoding_paths() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "a1"},
            {"task_id": "task_2", "answer": "a2"},
        ],
        "unmapped_text": "",
    }

    utf8_result = normalize_payload(_command(payload="Task 1: one".encode("utf-8"), filename="submission"), llm=_ParserLLM(parser_output))
    assert utf8_result.normalized_artifact.schema_version == "normalized:v2"
    assert utf8_result.normalized_artifact.submission_text == "Task 1: one"

    utf8_bom_payload = b"\xef\xbb\xbfTask 1: one\r\nTask 2: two"
    utf8_bom_result = normalize_payload(_command(payload=utf8_bom_payload, filename="input.txt"), llm=_ParserLLM(parser_output))
    assert "\ufeff" not in utf8_bom_result.normalized_artifact.submission_text
    assert "\r" not in utf8_bom_result.normalized_artifact.submission_text

    cp1251_payload = "Привет".encode("cp1251")
    cp1251_result = normalize_payload(_command(payload=cp1251_payload, filename="input.md"), llm=_ParserLLM(parser_output))
    assert cp1251_result.normalized_artifact.submission_text == "Привет"


@pytest.mark.unit
def test_normalize_payload_rejects_invalid_parser_schema() -> None:
    malformed_parser_output = {
        "task_solutions": [{"task_id": "task_1"}],
    }
    with pytest.raises(ValueError, match="normalization parser output"):
        normalize_payload(
            _command(payload=b"Task 1: one", filename="input.txt"),
            llm=_ParserLLM(malformed_parser_output),
        )


@pytest.mark.unit
def test_normalize_payload_accepts_solution_alias_from_parser_output() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "solution": "a1"},
            {"task_id": "task_2", "solution": "a2"},
        ],
        "unmapped_text": "leftover",
    }
    result = normalize_payload(
        _command(payload=b"Task 1: one\nTask 2: two", filename="input.txt"),
        llm=_ParserLLM(parser_output),
    )
    assert result.normalized_artifact.task_solutions == [
        {"task_id": "task_1", "answer": "a1"},
        {"task_id": "task_2", "answer": "a2"},
    ]
    assert result.normalized_artifact.unmapped_text == "leftover"


@pytest.mark.unit
def test_all_committed_plain_text_fixtures_normalize_successfully() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "a1"},
            {"task_id": "task_2", "answer": "a2"},
        ],
        "unmapped_text": "",
    }
    case_files = [
        ("case_001_plain_text_ordered", "input.txt"),
        ("case_002_plain_text_repeated_prompts", "input.txt"),
        ("case_003_plain_text_answer_only", "input.md"),
        ("case_004_plain_text_mixed_sql_commentary", "input.txt"),
        ("case_005_suffixless_plain_text", "submission"),
    ]
    for case_name, input_name in case_files:
        payload = (NORMALIZATION_CASES_DIR / case_name / input_name).read_bytes()
        result = normalize_payload(_command(payload=payload, filename=input_name), llm=_ParserLLM(parser_output))
        assert result.normalized_artifact.schema_version == "normalized:v2"
        assert result.normalized_artifact.submission_text
