from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.contracts import LLMClient, StorageClient, TelegramClient, WorkRepository


@dataclass
class SubmissionRecord:
    submission_id: str
    state: str
    transitions: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ApiDeps:
    repository: WorkRepository
    storage: StorageClient
    telegram: TelegramClient
    llm: LLMClient
    submissions: dict[str, SubmissionRecord]
