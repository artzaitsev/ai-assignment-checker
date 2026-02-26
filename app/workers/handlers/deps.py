from __future__ import annotations

from dataclasses import dataclass

from app.domain.contracts import LLMClient, StorageClient, TelegramClient, WorkRepository


@dataclass(frozen=True)
class WorkerDeps:
    repository: WorkRepository
    storage: StorageClient
    telegram: TelegramClient
    llm: LLMClient
