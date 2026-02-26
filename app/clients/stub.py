from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.contracts import STORAGE_PREFIXES
from app.domain.models import ProcessResult


@dataclass
class StubStorageClient:
    writes: list[str] = field(default_factory=list)
    _objects: dict[str, bytes] = field(default_factory=dict)

    def put_bytes(self, *, key: str, payload: bytes) -> str:
        if not any(key.startswith(prefix) for prefix in STORAGE_PREFIXES):
            raise ValueError("storage key must start with an allowed stage prefix")
        self.writes.append(key)
        self._objects[key] = payload
        return f"stub://{key}"

    def get_bytes(self, *, ref: str) -> bytes:
        key = ref.removeprefix("stub://")
        value = self._objects.get(key)
        if value is None:
            raise FileNotFoundError(f"artifact not found: {ref}")
        return value


@dataclass
class StubTelegramClient:
    updates: list[dict[str, str]] = field(default_factory=list)

    def poll_updates(self) -> list[dict[str, str]]:
        return list(self.updates)


@dataclass
class StubLLMClient:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def evaluate(self, *, prompt: str, model_version: str) -> ProcessResult:
        self.calls.append((prompt, model_version))
        return ProcessResult(success=True, detail="llm stub", artifact_version=model_version)
