from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re

from app.domain.contracts import STORAGE_PREFIXES
from app.domain.dto import LLMClientRequest, LLMClientResult
from app.domain.models import TelegramInboundEvent


@dataclass
class StubStorageClient:
    writes: list[str] = field(default_factory=list)
    objects: dict[str, bytes] = field(default_factory=dict)

    def put_bytes(self, *, key: str, payload: bytes) -> str:
        if not any(key.startswith(prefix) for prefix in STORAGE_PREFIXES):
            raise ValueError("storage key must start with an allowed stage prefix")
        self.writes.append(key)
        self.objects[key] = payload
        return f"s3://{key}"

    def get_bytes(self, *, key: str) -> bytes:
        payload = self.objects.get(key)
        if payload is None:
            raise KeyError(f"storage key not found: {key}")
        return payload


@dataclass
class StubTelegramClient:
    events: list[TelegramInboundEvent] = field(default_factory=list)
    sent_texts: list[tuple[str, str]] = field(default_factory=list)
    _seen_sends: set[tuple[str, str]] = field(default_factory=set)
    last_poll_offset: str | None = None

    def poll_events(self, *, timeout: int = 30, offset: str | None = None) -> list[TelegramInboundEvent]:
        del timeout
        self.last_poll_offset = offset
        if offset is not None:
            try:
                numeric_offset = int(offset)
                return [event for event in self.events if int(event.update_id) >= numeric_offset]
            except ValueError:
                for index, event in enumerate(self.events):
                    if event.update_id == offset:
                        return list(self.events[index + 1 :])
        return list(self.events)

    def send_text(self, *, chat_id: str, message: str) -> str | None:
        key = (chat_id, message)
        if key not in self._seen_sends:
            self._seen_sends.add(key)
            self.sent_texts.append(key)
        digest = sha256(f"{chat_id}:{message}".encode("utf-8")).hexdigest()[:16]
        return f"msg:{digest}"


@dataclass
class StubLLMClient:
    base_url: str = "https://stub-llm.invalid"
    model: str = "stub-model:v1"
    calls: list[LLMClientRequest] = field(default_factory=list)

    def evaluate(self, request: LLMClientRequest) -> LLMClientResult:
        self.calls.append(request)
        if request.system_prompt.startswith("NORMALIZATION_PARSER"):
            parser_output = _build_normalization_parser_output(request.user_prompt)
            return LLMClientResult(
                raw_text=json.dumps(parser_output, ensure_ascii=False),
                raw_json=parser_output,
                tokens_input=64,
                tokens_output=96,
                latency_ms=80,
            )
        task_schema = _extract_task_schema_from_prompt(request.user_prompt)
        tasks = _default_task_scores(task_schema)
        default_json: dict[str, object] = {
            "tasks": tasks,
            "organizer_feedback": {
                "strengths": ["Clear structure", "Reasonable decomposition"],
                "issues": ["Edge-case handling can be expanded"],
                "recommendations": ["Add failure-path tests for malformed inputs"],
            },
            "candidate_feedback": {
                "summary": "Good baseline with room for hardening.",
                "what_went_well": ["You solved the core task"],
                "what_to_improve": ["Cover more edge cases and retries"],
            },
            "ai_assistance": {
                "likelihood": 0.35,
                "confidence": 0.55,
                "disclaimer": "Probabilistic indicator, not proof",
            },
        }
        return LLMClientResult(
            raw_text="stub llm output",
            raw_json=default_json,
            tokens_input=128,
            tokens_output=256,
            latency_ms=120,
        )


def _extract_task_schema_from_prompt(prompt: str) -> dict[str, object] | None:
    marker = '"task_schema":'
    marker_index = prompt.find(marker)
    if marker_index == -1:
        return None
    start = prompt.find("{", marker_index)
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(prompt)):
        char = prompt[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                import json

                snippet = prompt[start : index + 1]
                try:
                    payload = json.loads(snippet)
                except json.JSONDecodeError:
                    return None
                return payload if isinstance(payload, dict) else None
    return None


def _default_task_scores(task_schema: dict[str, object] | None) -> list[dict[str, object]]:
    tasks_raw = task_schema.get("tasks") if isinstance(task_schema, dict) else None
    if not isinstance(tasks_raw, list) or not tasks_raw:
        return [
            {
                "task_id": "task_main",
                "criteria": [
                    {"criterion_id": "correctness", "score": 8, "reason": "Core logic is mostly correct"},
                ],
            }
        ]

    tasks: list[dict[str, object]] = []
    for task in tasks_raw:
        if not isinstance(task, dict):
            continue
        task_id = task.get("task_id")
        criteria_raw = task.get("criteria")
        if not isinstance(task_id, str) or not isinstance(criteria_raw, list):
            continue
        criteria: list[dict[str, object]] = []
        for index, criterion in enumerate(criteria_raw):
            if not isinstance(criterion, dict):
                continue
            criterion_id = criterion.get("criterion_id")
            if not isinstance(criterion_id, str):
                continue
            criteria.append(
                {
                    "criterion_id": criterion_id,
                    "score": 8 if index == 0 else 7,
                    "reason": f"Synthetic review for {criterion_id}",
                }
            )
        if criteria:
            tasks.append({"task_id": task_id, "criteria": criteria})
    return tasks or [
        {
            "task_id": "task_main",
            "criteria": [
                {"criterion_id": "correctness", "score": 8, "reason": "Core logic is mostly correct"},
            ],
        }
    ]


def _build_normalization_parser_output(payload_raw: str) -> dict[str, object]:
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return {"task_solutions": [], "unmapped_text": ""}
    if not isinstance(payload, dict):
        return {"task_solutions": [], "unmapped_text": ""}

    submission_text = payload.get("submission_text")
    tasks = payload.get("assignment_tasks")
    if not isinstance(submission_text, str) or not isinstance(tasks, list):
        return {"task_solutions": [], "unmapped_text": ""}

    task_ids: list[str] = []
    for task in tasks:
        if isinstance(task, dict):
            task_id = task.get("task_id")
            if isinstance(task_id, str) and task_id:
                task_ids.append(task_id)
    segments = _split_by_task_markers(submission_text)
    solutions: list[dict[str, str]] = []
    for index, task_id in enumerate(task_ids, start=1):
        answer = segments.get(index)
        if answer is None:
            answer = _fallback_task_answer(submission_text, task_index=index, total_tasks=len(task_ids))
        solutions.append({"task_id": task_id, "answer": answer})
    return {"task_solutions": solutions, "unmapped_text": ""}


def _split_by_task_markers(submission_text: str) -> dict[int, str]:
    matches = list(re.finditer(r"\bTask\s*(\d+)\s*:\s*", submission_text, flags=re.IGNORECASE))
    segments: dict[int, str] = {}
    for index, match in enumerate(matches):
        task_num = int(match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(submission_text)
        chunk = submission_text[start:end].strip(" .\n\t")
        if task_num in segments:
            merged = f"{segments[task_num]} {chunk}".strip()
            segments[task_num] = merged
        else:
            segments[task_num] = chunk
    return segments


def _fallback_task_answer(submission_text: str, *, task_index: int, total_tasks: int) -> str:
    parts = [item.strip() for item in re.split(r"[.;]\s+", submission_text) if item.strip()]
    if not parts:
        return ""
    position = min(task_index - 1, len(parts) - 1)
    if total_tasks > 1 and len(parts) >= total_tasks:
        position = task_index - 1
    return parts[position]
