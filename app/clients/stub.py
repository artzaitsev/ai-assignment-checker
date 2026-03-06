from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

from app.domain.contracts import STORAGE_PREFIXES
from app.domain.dto import LLMClientRequest, LLMClientResult
from app.domain.models import TelegramInboundEvent
import asyncio


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

    def poll_events(self, *, timeout: int = 30, offset: str | None = None) -> list[TelegramInboundEvent]:
        del timeout
        if offset is not None:
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
    calls: list[LLMClientRequest] = field(default_factory=list)

    async def evaluate(self, request: LLMClientRequest) -> LLMClientResult:
        self.calls.append(request)
        await asyncio.sleep(0.1)  # имитация сетевой задержки
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
