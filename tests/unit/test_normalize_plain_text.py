from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pytest

from app.domain.dto import LLMClientResult, NormalizePayloadCommand, NormalizationTaskInput
from app.domain.use_cases import normalize as normalize_module
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


class _SequencedParserLLM:
    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def evaluate(self, request: object) -> LLMClientResult:
        del request
        if not self._responses:
            raise AssertionError("No more queued LLM responses")
        self.calls += 1
        payload = self._responses.pop(0)
        return LLMClientResult(
            raw_text=json.dumps(payload, ensure_ascii=False),
            raw_json=dict(payload),
            tokens_input=1,
            tokens_output=1,
            latency_ms=1,
        )


class _SequencedRawLLM:
    def __init__(self, responses: list[LLMClientResult]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def evaluate(self, request: object) -> LLMClientResult:
        del request
        if not self._responses:
            raise AssertionError("No more queued LLM responses")
        self.calls += 1
        return self._responses.pop(0)


class _TrackingParserLLM:
    def __init__(self, raw_json: Mapping[str, object]) -> None:
        self.raw_json = raw_json
        self.requests: list[object] = []

    def evaluate(self, request: object) -> LLMClientResult:
        self.requests.append(request)
        return LLMClientResult(
            raw_text=json.dumps(self.raw_json, ensure_ascii=False),
            raw_json=dict(self.raw_json),
            tokens_input=1,
            tokens_output=1,
            latency_ms=1,
        )


def _command(*, payload: bytes, filename: str, persisted_mime: str | None = "text/plain") -> NormalizePayloadCommand:
    return NormalizePayloadCommand(
        submission_id="sub_test",
        artifact_ref=f"raw/sub_test/{filename}",
        filename=filename,
        source_type="api_upload",
        persisted_mime=persisted_mime,
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
        "task_solutions": "not-an-array",
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
def test_normalize_payload_deduplicates_repeated_task_ids_and_keeps_first(caplog: pytest.LogCaptureFixture) -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "first-task-1"},
            {"task_id": "task_1", "answer": "second-task-1"},
            {"task_id": "task_2", "answer": "task-2"},
        ],
        "unmapped_text": "",
    }
    with caplog.at_level("WARNING", logger="runtime"):
        result = normalize_payload(
            _command(payload=b"Task 1: one\nTask 2: two", filename="input.txt"),
            llm=_ParserLLM(parser_output),
        )

    assert result.normalized_artifact.task_solutions == [
        {"task_id": "task_1", "answer": "first-task-1"},
        {"task_id": "task_2", "answer": "task-2"},
    ]
    assert "normalization parser produced duplicate task_id; keeping first answer" in caplog.text


@pytest.mark.unit
def test_normalize_payload_coerces_non_string_answers_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": {"text": "nested"}},
            {"task_id": "task_2", "answer": 42},
        ],
        "unmapped_text": "",
    }
    with caplog.at_level("WARNING", logger="runtime"):
        result = normalize_payload(
            _command(payload=b"Task 1: one\nTask 2: two", filename="input.txt"),
            llm=_ParserLLM(parser_output),
        )

    assert result.normalized_artifact.task_solutions == [
        {"task_id": "task_1", "answer": '{"text": "nested"}'},
        {"task_id": "task_2", "answer": "42"},
    ]
    assert "normalization parser produced structured answer; coercing to JSON string" in caplog.text
    assert "normalization parser produced non-string answer; coercing to text" in caplog.text


@pytest.mark.unit
def test_normalize_payload_coerces_null_answer_to_empty_string(caplog: pytest.LogCaptureFixture) -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": None},
            {"task_id": "task_2", "answer": "ok"},
        ],
        "unmapped_text": "",
    }
    with caplog.at_level("WARNING", logger="runtime"):
        result = normalize_payload(
            _command(payload=b"Task 1: one\nTask 2: two", filename="input.txt"),
            llm=_ParserLLM(parser_output),
        )

    assert result.normalized_artifact.task_solutions == [
        {"task_id": "task_1", "answer": ""},
        {"task_id": "task_2", "answer": "ok"},
    ]
    assert "normalization parser produced null answer; coercing to empty string" in caplog.text


@pytest.mark.unit
def test_normalize_payload_coerces_missing_answer_to_empty_string(caplog: pytest.LogCaptureFixture) -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1"},
            {"task_id": "task_2", "answer": "ok"},
        ],
        "unmapped_text": "",
    }
    with caplog.at_level("WARNING", logger="runtime"):
        result = normalize_payload(
            _command(payload=b"Task 1: one\nTask 2: two", filename="input.txt"),
            llm=_ParserLLM(parser_output),
        )

    assert result.normalized_artifact.task_solutions == [
        {"task_id": "task_1", "answer": ""},
        {"task_id": "task_2", "answer": "ok"},
    ]
    assert "normalization parser omitted answer; coercing to empty string" in caplog.text


@pytest.mark.unit
def test_normalize_payload_falls_back_to_assignment_order_when_task_id_missing(caplog: pytest.LogCaptureFixture) -> None:
    parser_output = {
        "task_solutions": [
            {"answer": "first"},
            {"task_id": "task_2", "answer": "second"},
        ],
        "unmapped_text": "",
    }
    with caplog.at_level("WARNING", logger="runtime"):
        result = normalize_payload(
            _command(payload=b"Task 1: one\nTask 2: two", filename="input.txt"),
            llm=_ParserLLM(parser_output),
        )

    assert result.normalized_artifact.task_solutions == [
        {"task_id": "task_1", "answer": "first"},
        {"task_id": "task_2", "answer": "second"},
    ]
    assert "normalization parser omitted task_id; using fallback from assignment order" in caplog.text


@pytest.mark.unit
def test_normalize_payload_runs_single_repair_pass_for_malformed_json() -> None:
    llm = _SequencedRawLLM(
        [
            LLMClientResult(raw_text="{broken-json", raw_json=None, tokens_input=1, tokens_output=1, latency_ms=1),
            LLMClientResult(
                raw_text=json.dumps(
                    {
                        "task_solutions": [
                            {"task_id": "task_1", "answer": "a1"},
                        ],
                        "unmapped_text": "leftover",
                    },
                    ensure_ascii=False,
                ),
                raw_json={
                    "task_solutions": [
                        {"task_id": "task_1", "answer": "a1"},
                    ],
                    "unmapped_text": "leftover",
                },
                tokens_input=1,
                tokens_output=1,
                latency_ms=1,
            ),
        ]
    )
    result = normalize_payload(
        _command(payload=b"Task 1: one\nTask 2: two", filename="input.txt"),
        llm=llm,
    )
    assert llm.calls == 2
    assert result.normalized_artifact.task_solutions == [
        {"task_id": "task_1", "answer": "a1"},
    ]
    assert result.normalized_artifact.unmapped_text == "leftover"


@pytest.mark.unit
def test_normalize_payload_fails_when_malformed_json_repair_is_still_invalid() -> None:
    llm = _SequencedRawLLM(
        [
            LLMClientResult(raw_text="{broken-json", raw_json=None, tokens_input=1, tokens_output=1, latency_ms=1),
            LLMClientResult(raw_text="still-broken", raw_json=None, tokens_input=1, tokens_output=1, latency_ms=1),
        ]
    )

    with pytest.raises(ValueError, match="normalization parser output is not valid JSON"):
        normalize_payload(
            _command(payload=b"Task 1: one\nTask 2: two", filename="input.txt"),
            llm=llm,
        )

    assert llm.calls == 2


@pytest.mark.unit
def test_normalize_payload_does_not_repair_schema_invalid_json_output() -> None:
    llm = _SequencedRawLLM(
        [
            LLMClientResult(
                raw_text=json.dumps({"task_solutions": "bad", "unmapped_text": ""}),
                raw_json={"task_solutions": "bad", "unmapped_text": ""},
                tokens_input=1,
                tokens_output=1,
                latency_ms=1,
            ),
        ]
    )

    with pytest.raises(ValueError, match="normalization parser output.task_solutions must be array"):
        normalize_payload(
            _command(payload=b"Task 1: one\nTask 2: two", filename="input.txt"),
            llm=llm,
        )

    assert llm.calls == 1


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
        assert result.office_extraction is None


@pytest.mark.unit
def test_normalize_payload_detects_docx_and_odt_by_signature_and_extracts_text() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "first"},
            {"task_id": "task_2", "answer": "second"},
        ],
        "unmapped_text": "",
    }

    docx_payload = (NORMALIZATION_CASES_DIR / "case_006_docx_text_only" / "input.docx").read_bytes()
    docx_result = normalize_payload(
        _command(payload=docx_payload, filename="submission.bin", persisted_mime="application/octet-stream"),
        llm=_ParserLLM(parser_output),
    )
    assert docx_result.office_extraction is not None
    assert docx_result.office_extraction.detected_format == "docx"
    assert docx_result.office_extraction.embedded_image_count == 0
    assert "Task 1 prompt: identify one schema issue and propose a fix." in docx_result.normalized_artifact.submission_text
    assert "note | value" in docx_result.normalized_artifact.submission_text

    odt_payload = (NORMALIZATION_CASES_DIR / "case_008_odt_text_only" / "input.odt").read_bytes()
    odt_result = normalize_payload(
        _command(payload=odt_payload, filename="notes.tmp", persisted_mime="application/octet-stream"),
        llm=_ParserLLM(parser_output),
    )
    assert odt_result.office_extraction is not None
    assert odt_result.office_extraction.detected_format == "odt"
    assert odt_result.office_extraction.embedded_image_count == 0
    assert "SELECT id, email FROM users WHERE deleted_at = NULL;" in odt_result.normalized_artifact.submission_text
    assert "- Task 1: use IS NULL instead of = NULL." in odt_result.normalized_artifact.submission_text


@pytest.mark.unit
def test_normalize_payload_tracks_embedded_image_handoff_for_docx() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "first"},
            {"task_id": "task_2", "answer": "second"},
        ],
        "unmapped_text": "",
    }
    payload = (NORMALIZATION_CASES_DIR / "case_007_docx_embedded_image_needs_ocr" / "input.docx").read_bytes()
    result = normalize_payload(
        _command(
            payload=payload,
            filename="input.docx",
            persisted_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        llm=_ParserLLM(parser_output),
    )
    assert result.office_extraction is not None
    assert result.office_extraction.detected_format == "docx"
    assert result.office_extraction.embedded_image_count == 1
    assert result.normalized_artifact.schema_version == "normalized:v2"
    assert "embedded_image_count" not in result.normalized_artifact.model_dump()
    assert "The answer was pasted as an image from chat." in result.normalized_artifact.submission_text


@pytest.mark.unit
def test_normalize_payload_fails_deterministically_for_corrupt_office_package() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "first"},
            {"task_id": "task_2", "answer": "second"},
        ],
        "unmapped_text": "",
    }
    payload = (NORMALIZATION_CASES_DIR / "case_015_corrupt_docx_supported" / "input.docx").read_bytes()
    with pytest.raises(ValueError, match="Supported file format could not be parsed"):
        normalize_payload(
            _command(payload=payload, filename="input.docx", persisted_mime="application/octet-stream"),
            llm=_ParserLLM(parser_output),
        )


@pytest.mark.unit
def test_normalize_payload_extracts_native_pdf_text_without_ocr() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "first"},
            {"task_id": "task_2", "answer": "second"},
        ],
        "unmapped_text": "",
    }
    payload = (NORMALIZATION_CASES_DIR / "case_009_pdf_native_text" / "input.pdf").read_bytes()
    result = normalize_payload(
        _command(payload=payload, filename="input.pdf", persisted_mime="application/pdf"),
        llm=_ParserLLM(parser_output),
    )
    assert result.pdf_extraction is not None
    assert result.pdf_extraction.total_pages >= 1
    assert result.pdf_extraction.outcome in {"native_complete", "bounded"}
    assert "SQL debug assignment - PDF export" in result.normalized_artifact.submission_text
    assert result.normalized_artifact.schema_version == "normalized:v2"


@pytest.mark.unit
def test_normalize_payload_merges_pdf_ocr_candidate_pages_deterministically() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "first"},
            {"task_id": "task_2", "answer": "second"},
        ],
        "unmapped_text": "",
    }
    payload = (NORMALIZATION_CASES_DIR / "case_010_pdf_mixed_native_and_scanned" / "input.pdf").read_bytes()
    ocr_text = json.loads(
        (NORMALIZATION_CASES_DIR / "case_010_pdf_mixed_native_and_scanned" / "ocr_stub.json").read_text(encoding="utf-8")
    )["text"]

    def _ocr_provider(_: bytes, page_indexes: tuple[int, ...]) -> dict[int, str]:
        return {page_index: str(ocr_text) for page_index in page_indexes}

    result = normalize_payload(
        _command(payload=payload, filename="input.pdf", persisted_mime="application/pdf"),
        llm=_ParserLLM(parser_output),
        pdf_ocr_provider=_ocr_provider,
    )
    assert result.pdf_extraction is not None
    assert result.pdf_extraction.ocr_candidate_page_indexes
    assert any(page.used_ocr_text for page in result.pdf_extraction.pages)
    assert "Scanned addendum. Photo fragment." in result.normalized_artifact.submission_text


@pytest.mark.unit
def test_normalize_payload_requires_ocr_for_sparse_pdf_without_provider() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "first"},
            {"task_id": "task_2", "answer": "second"},
        ],
        "unmapped_text": "",
    }
    payload = (NORMALIZATION_CASES_DIR / "case_011_ocr_heavy_submission" / "input.pdf").read_bytes()
    with pytest.raises(ValueError, match="OCR required"):
        normalize_payload(
            _command(payload=payload, filename="input.pdf", persisted_mime="application/pdf"),
            llm=_ParserLLM(parser_output),
        )


@pytest.mark.unit
def test_normalize_payload_accepts_sparse_pdf_with_deterministic_ocr_stub() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "first"},
            {"task_id": "task_2", "answer": "second"},
        ],
        "unmapped_text": "",
    }
    payload = (NORMALIZATION_CASES_DIR / "case_011_ocr_heavy_submission" / "input.pdf").read_bytes()
    ocr_text = json.loads(
        (NORMALIZATION_CASES_DIR / "case_011_ocr_heavy_submission" / "ocr_stub.json").read_text(encoding="utf-8")
    )["text"]

    def _ocr_provider(_: bytes, page_indexes: tuple[int, ...]) -> dict[int, str]:
        return {page_index: str(ocr_text) for page_index in page_indexes}

    result = normalize_payload(
        _command(payload=payload, filename="input.pdf", persisted_mime="application/pdf"),
        llm=_ParserLLM(parser_output),
        pdf_ocr_provider=_ocr_provider,
    )
    assert result.pdf_extraction is not None
    assert result.pdf_extraction.outcome == "ocr_partial"
    assert "Forwarded screenshot." in result.normalized_artifact.submission_text
    assert result.normalized_artifact.schema_version == "normalized:v2"


@pytest.mark.unit
def test_normalize_payload_fails_deterministically_for_corrupt_pdf() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "first"},
            {"task_id": "task_2", "answer": "second"},
        ],
        "unmapped_text": "",
    }
    payload = (NORMALIZATION_CASES_DIR / "case_012_corrupt_supported_file" / "input.pdf").read_bytes()
    with pytest.raises(ValueError, match="Supported file format could not be parsed"):
        normalize_payload(
            _command(payload=payload, filename="input.pdf", persisted_mime="application/pdf"),
            llm=_ParserLLM(parser_output),
        )


@pytest.mark.unit
def test_normalize_payload_uses_shared_parser_contract_for_pdf_submission_text() -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "first"},
            {"task_id": "task_2", "answer": "second"},
        ],
        "unmapped_text": "",
    }
    llm = _TrackingParserLLM(parser_output)
    payload = (NORMALIZATION_CASES_DIR / "case_009_pdf_native_text" / "input.pdf").read_bytes()
    result = normalize_payload(
        _command(payload=payload, filename="input.pdf", persisted_mime="application/pdf"),
        llm=llm,
    )
    assert result.normalized_artifact.schema_version == "normalized:v2"
    request = llm.requests[0]
    user_prompt = json.loads(getattr(request, "user_prompt"))
    assert user_prompt["assignment_public_id"] == "asg_test"
    assert isinstance(user_prompt["assignment_tasks"], list)
    assert "submission_text" in user_prompt
    assert "raw_payload" not in user_prompt


@pytest.mark.unit
def test_normalize_payload_records_bounded_pdf_outcome_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    parser_output = {
        "task_solutions": [
            {"task_id": "task_1", "answer": "first"},
            {"task_id": "task_2", "answer": "second"},
        ],
        "unmapped_text": "",
    }
    payload = (NORMALIZATION_CASES_DIR / "case_009_pdf_native_text" / "input.pdf").read_bytes()
    monkeypatch.setattr(normalize_module, "_PDF_MAX_CHARS", 80)
    result = normalize_payload(
        _command(payload=payload, filename="input.pdf", persisted_mime="application/pdf"),
        llm=_ParserLLM(parser_output),
    )
    assert result.pdf_extraction is not None
    assert result.pdf_extraction.bounded is True
    assert result.pdf_extraction.bounded_reason == "char_limit"
    assert result.pdf_extraction.outcome == "bounded"
