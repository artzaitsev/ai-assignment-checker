from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.contracts import ArtifactRepository, LLMClient, StorageClient, TelegramClient, WorkRepository
from app.domain.models import ApplySessionSettings, TelegramLinkSettings


@dataclass
class SubmissionRecord:
    submission_id: str
    state: str
    candidate_public_id: str | None = None
    assignment_public_id: str | None = None
    transitions: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ApiDeps:
    repository: WorkRepository
    artifact_repository: ArtifactRepository
    storage: StorageClient
    telegram: TelegramClient
    llm: LLMClient
    submissions: dict[str, SubmissionRecord]
    telegram_link_settings: TelegramLinkSettings | None = None
    apply_session_settings: ApplySessionSettings | None = None
