from __future__ import annotations

from dataclasses import dataclass

from app.domain.contracts import LLMClient, StorageClient, TelegramClient


@dataclass(frozen=True)
class WorkerDeps:
    storage: StorageClient
    telegram: TelegramClient
    llm: LLMClient
