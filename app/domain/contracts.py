from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models import ProcessResult, WorkItemClaim

CLAIM_SQL_CONTRACT = "SELECT ... FOR UPDATE SKIP LOCKED"
STORAGE_PREFIXES = (
    "raw/",
    "normalized/",
    "llm-output/",
    "feedback/",
    "exports/",
    "eval/",
)


@runtime_checkable
class WorkRepository(Protocol):
    """Repository contract for worker claim/process/finalize flow.

    Claim semantics must remain compatible with Postgres row claims using
    SELECT ... FOR UPDATE SKIP LOCKED.
    """

    def claim_next(self, *, stage: str, worker_id: str) -> WorkItemClaim | None: ...

    def transition_state(self, *, item_id: str, from_state: str, to_state: str) -> None: ...

    def link_artifact(
        self,
        *,
        item_id: str,
        stage: str,
        artifact_ref: str,
        artifact_version: str | None,
    ) -> None: ...

    def finalize(
        self,
        *,
        item_id: str,
        stage: str,
        success: bool,
        detail: str,
    ) -> None: ...


@runtime_checkable
class StorageClient(Protocol):
    """Storage contract using single-bucket, prefix-scoped paths."""

    def put_bytes(self, *, key: str, payload: bytes) -> str: ...


@runtime_checkable
class TelegramClient(Protocol):
    def poll_updates(self) -> list[dict[str, str]]: ...


@runtime_checkable
class LLMClient(Protocol):
    def evaluate(self, *, prompt: str, model_version: str) -> ProcessResult: ...
