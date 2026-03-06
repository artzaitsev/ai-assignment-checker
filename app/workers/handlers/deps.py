from __future__ import annotations

from dataclasses import dataclass

from app.domain.contracts import ArtifactRepository, LLMClient, StorageClient, TelegramClient, WorkRepository
from app.domain.models import TelegramLinkSettings


@dataclass(frozen=True)
class WorkerDeps:
    repository: WorkRepository
    artifact_repository: ArtifactRepository
    storage: StorageClient
    telegram: TelegramClient
    llm: LLMClient
    telegram_link_settings: TelegramLinkSettings | None = None
