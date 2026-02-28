from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.contracts import STORAGE_PREFIXES
from app.domain.dto import LLMClientRequest, LLMClientResult


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
    updates: list[dict[str, str]] = field(default_factory=list)
    notifications: dict[str, str] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)

    def poll_updates(self) -> list[dict[str, str]]:
        return list(self.updates)

    def get_file_bytes(self, *, file_id: str) -> bytes:
        payload = self.files.get(file_id)
        if payload is None:
            raise KeyError(f"telegram file is not found: {file_id}")
        return payload

    def send_result_notification(self, *, submission_id: str, message: str) -> str | None:
        # Idempotent by submission id in stub mode.
        if submission_id in self.notifications:
            return f"msg:{submission_id}"
        self.notifications[submission_id] = message
        return f"msg:{submission_id}"


@dataclass
class StubLLMClient:
    calls: list[LLMClientRequest] = field(default_factory=list)

    def evaluate(self, request: LLMClientRequest) -> LLMClientResult:
        self.calls.append(request)
        default_json: dict[str, object] = {
            "criteria": [
                {"id": "correctness", "score": 8, "reason": "Core logic is mostly correct"},
                {"id": "completeness", "score": 7, "reason": "Most requirements are covered"},
                {"id": "code_quality", "score": 8, "reason": "Readable structure"},
                {"id": "edge_cases", "score": 7, "reason": "Basic edge cases addressed"},
            ],
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
