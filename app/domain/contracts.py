from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.models import (
    AssignmentSnapshot,
    CandidateSnapshot,
    ProcessResult,
    SubmissionSnapshot,
    SubmissionSourceSnapshot,
    UpsertSourceResult,
    WorkItemClaim,
)

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

    async def create_candidate(self, *, first_name: str, last_name: str) -> CandidateSnapshot: ...

    async def get_or_create_candidate_by_source(
        self,
        *,
        source_type: str,
        source_external_id: str,
        first_name: str,
        last_name: str,
        metadata_json: dict[str, object] | None = None,
    ) -> CandidateSnapshot: ...

    async def create_assignment(
        self,
        *,
        title: str,
        description: str,
        is_active: bool = True,
    ) -> AssignmentSnapshot: ...

    async def list_assignments(self, *, active_only: bool = True) -> list[AssignmentSnapshot]: ...

    async def create_submission_with_source(
        self,
        *,
        candidate_public_id: str,
        assignment_public_id: str,
        source_type: str,
        source_external_id: str,
        initial_status: str,
        metadata_json: dict[str, object] | None = None,
        source_payload_ref: str | None = None,
    ) -> UpsertSourceResult: ...

    async def find_submission_source(
        self,
        *,
        source_type: str,
        source_external_id: str,
    ) -> SubmissionSourceSnapshot | None: ...

    async def get_submission(self, *, submission_id: str) -> SubmissionSnapshot | None: ...

    async def get_artifact_refs(self, *, item_id: str) -> dict[str, str]: ...

    async def claim_next(self, *, stage: str, worker_id: str, lease_seconds: int = 30) -> WorkItemClaim | None: ...

    async def heartbeat_claim(
        self,
        *,
        item_id: str,
        stage: str,
        worker_id: str,
        lease_seconds: int = 30,
    ) -> bool: ...

    async def reclaim_expired_claims(self, *, stage: str) -> int: ...

    async def transition_state(self, *, item_id: str, from_state: str, to_state: str) -> None: ...

    async def link_artifact(
        self,
        *,
        item_id: str,
        stage: str,
        artifact_ref: str,
        artifact_version: str | None,
    ) -> None: ...

    async def finalize(
        self,
        *,
        item_id: str,
        stage: str,
        worker_id: str,
        success: bool,
        detail: str,
        error_code: str | None = None,
    ) -> None: ...


@runtime_checkable
class StorageClient(Protocol):
    """Storage contract using single-bucket, prefix-scoped paths."""

    def put_bytes(self, *, key: str, payload: bytes) -> str: ...

    def get_bytes(self, *, ref: str) -> bytes: ...


@runtime_checkable
class TelegramClient(Protocol):
    def poll_updates(self) -> list[dict[str, str]]: ...


@runtime_checkable
class LLMClient(Protocol):
    def evaluate(self, *, prompt: str, model_version: str) -> ProcessResult: ...
