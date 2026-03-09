from __future__ import annotations

import json

from pydantic import ValidationError

from app.lib.artifacts.types import NormalizedArtifact
from app.domain.contracts import LLMClient
from app.domain.dto import (
    NormalizePayloadCommand,
    NormalizePayloadResult,
    NormalizationParserInput,
    NormalizationParserOutput,
    NormalizationTaskSolution,
)
from app.domain.dto import LLMClientRequest

COMPONENT_ID = "domain.normalize.payload"
_TEXT_MIME_HINTS = ("text/", "application/json", "application/xml")


def normalize_payload(cmd: NormalizePayloadCommand, *, llm: LLMClient) -> NormalizePayloadResult:
    """Build normalized:v2 artifact for supported plain-text submissions."""
    if not _is_supported_plain_text(
        filename=cmd.filename,
        persisted_mime=cmd.persisted_mime,
        payload=cmd.raw_payload,
    ):
        raise ValueError("unsupported plain-text submission")

    submission_text = _normalize_submission_text(_decode_plain_text(cmd.raw_payload))
    parser_input = NormalizationParserInput(
        assignment_public_id=cmd.assignment_public_id,
        language=cmd.assignment_language,
        tasks=cmd.assignment_tasks,
        submission_text=submission_text,
    )
    parser_output = _invoke_normalization_parser(parser_input=parser_input, llm=llm)

    try:
        normalized_artifact = NormalizedArtifact(
            submission_public_id=cmd.submission_id,
            assignment_public_id=cmd.assignment_public_id,
            source_type=cmd.source_type,
            submission_text=submission_text,
            task_solutions=[{"task_id": item.task_id, "answer": item.answer} for item in parser_output.task_solutions],
            unmapped_text=parser_output.unmapped_text,
            schema_version="normalized:v2",
        )
    except ValidationError as exc:
        raise ValueError("normalized artifact schema validation failed") from exc

    # Artifact key convention is fixed by contract; producer may later derive
    # a richer object key (e.g. with attempt/version suffix) without changing prefix.
    return NormalizePayloadResult(
        normalized_artifact=normalized_artifact,
        schema_version="normalized:v2",
    )


def _is_supported_plain_text(*, filename: str, persisted_mime: str | None, payload: bytes) -> bool:
    normalized_name = filename.strip().lower()
    extension = normalized_name.rsplit(".", maxsplit=1)[1] if "." in normalized_name else ""
    suffixless = "." not in normalized_name
    extension_allowed = extension in {"txt", "md"}
    mime_hint_allowed = isinstance(persisted_mime, str) and persisted_mime.strip().lower().startswith(_TEXT_MIME_HINTS)
    candidate = extension_allowed or suffixless or bool(mime_hint_allowed)
    return candidate and _sniff_plain_text_bytes(payload)


def _sniff_plain_text_bytes(payload: bytes) -> bool:
    if not payload:
        return True
    if b"\x00" in payload:
        return False
    control_bytes = 0
    for value in payload:
        if value in (9, 10, 13):
            continue
        if value < 32:
            control_bytes += 1
    if control_bytes / max(1, len(payload)) > 0.02:
        return False
    try:
        _decode_plain_text(payload)
    except ValueError:
        return False
    return True


def _decode_plain_text(payload: bytes) -> str:
    if payload.startswith(b"\xef\xbb\xbf"):
        return payload.decode("utf-8-sig")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return payload.decode("cp1251")
        except UnicodeDecodeError as exc:
            raise ValueError("unsupported plain-text encoding") from exc


def _normalize_submission_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.lstrip("\ufeff")
    return normalized.strip(" \t\n")


def _invoke_normalization_parser(*, parser_input: NormalizationParserInput, llm: LLMClient) -> NormalizationParserOutput:
    payload = {
        "assignment_public_id": parser_input.assignment_public_id,
        "language": parser_input.language,
        "assignment_tasks": [
            {
                "task_id": task.task_id,
                "task_index": task.task_index,
                "task_text": task.task_text,
            }
            for task in parser_input.tasks
        ],
        "submission_text": parser_input.submission_text,
    }
    result = llm.evaluate(
        LLMClientRequest(
            system_prompt="NORMALIZATION_PARSER: return strict JSON with task_solutions[] and unmapped_text",
            user_prompt=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            model="normalization-parser:v1",
            temperature=0.0,
            seed=42,
            response_language=parser_input.language,
        )
    )

    raw_output: object = result.raw_json
    if raw_output is None:
        try:
            raw_output = json.loads(result.raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError("normalization parser output is not valid JSON") from exc
    return _decode_parser_output(raw_output)


def _decode_parser_output(raw_output: object) -> NormalizationParserOutput:
    if not isinstance(raw_output, dict):
        raise ValueError("normalization parser output must be a JSON object")
    task_solutions_raw = raw_output.get("task_solutions")
    unmapped_text = raw_output.get("unmapped_text")
    if not isinstance(task_solutions_raw, list):
        raise ValueError("normalization parser output.task_solutions must be array")
    if not isinstance(unmapped_text, str):
        raise ValueError("normalization parser output.unmapped_text must be string")

    task_solutions: list[NormalizationTaskSolution] = []
    seen_task_ids: set[str] = set()
    for entry in task_solutions_raw:
        if not isinstance(entry, dict):
            raise ValueError("normalization parser output.task_solutions[] must be object")
        task_id = entry.get("task_id")
        answer = entry.get("answer")
        if answer is None:
            answer = entry.get("solution")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("normalization parser output.task_solutions[].task_id is required")
        if not isinstance(answer, str):
            raise ValueError("normalization parser output.task_solutions[].answer must be string")
        if task_id in seen_task_ids:
            raise ValueError("normalization parser output.task_solutions[].task_id must be unique")
        seen_task_ids.add(task_id)
        task_solutions.append(NormalizationTaskSolution(task_id=task_id, answer=answer))

    return NormalizationParserOutput(task_solutions=tuple(task_solutions), unmapped_text=unmapped_text)
