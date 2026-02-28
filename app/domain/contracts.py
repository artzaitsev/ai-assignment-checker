from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.dto import LLMClientRequest, LLMClientResult
from app.lib.artifacts.types import ExportRowArtifact, NormalizedArtifact
from app.domain.models import (
    AssignmentSnapshot,
    CandidateSnapshot,
    SubmissionSnapshot,
    SubmissionSourceSnapshot,
    SubmissionListItem,
    SubmissionListQuery,
    UpsertSourceResult,
    WorkItemClaim,
)


# Minimal reproducibility subset persisted with evaluations.
ReproducibilitySubset = dict[str, str]

CLAIM_SQL_CONTRACT = "SELECT ... FOR UPDATE SKIP LOCKED"
STORAGE_PREFIXES = (
    "raw/",
    "normalized/",
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

    async def list_submissions(self, *, query: SubmissionListQuery) -> list[SubmissionListItem]: ...

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

    async def get_artifact_ref(self, *, item_id: str, stage: str) -> str: ...

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

    # Persist evaluated score + structured feedback payloads.
    # Keep reproducibility_subset queryable with score context so export/delivery
    # can trace versions without joining full run metadata.
    async def persist_evaluation(
        self,
        *,
        submission_id: str,
        score_1_10: int,
        criteria_scores_json: dict[str, object],
        organizer_feedback_json: dict[str, object],
        candidate_feedback_json: dict[str, object],
        ai_assistance_likelihood: float,
        ai_assistance_confidence: float,
        reproducibility_subset: ReproducibilitySubset,
    ) -> None: ...

    # Persist authoritative run metadata used for replay and audits.
    async def persist_llm_run(
        self,
        *,
        submission_id: str,
        provider: str,
        model: str,
        api_base: str,
        chain_version: str,
        spec_version: str,
        response_language: str,
        temperature: float,
        seed: int | None,
        tokens_input: int,
        tokens_output: int,
        latency_ms: int,
    ) -> None: ...

    async def persist_delivery(
        self,
        *,
        submission_id: str,
        channel: str,
        status: str,
        external_message_id: str | None = None,
        attempts: int = 0,
        last_error_code: str | None = None,
    ) -> None: ...


@runtime_checkable
class StorageClient(Protocol):
    """Storage contract using single-bucket, prefix-scoped paths."""

    def put_bytes(self, *, key: str, payload: bytes) -> str: ...

    def get_bytes(self, *, key: str) -> bytes: ...


@runtime_checkable
class ArtifactRepository(Protocol):
    """Typed artifact I/O boundary.

    Domain use-cases rely on this contract and never perform raw JSON/S3 work.
    """

    def load_normalized(self, *, artifact_ref: str) -> NormalizedArtifact: ...

    def save_normalized(self, *, submission_id: str, artifact: NormalizedArtifact) -> str: ...

    def save_export_rows(self, *, export_id: str, rows: list[ExportRowArtifact]) -> str: ...


@runtime_checkable
class TelegramClient(Protocol):
    def poll_updates(self) -> list[dict[str, str]]: ...

    def get_file_bytes(self, *, file_id: str) -> bytes: ...

    def send_result_notification(self, *, submission_id: str, message: str) -> str | None: ...


@runtime_checkable
class LLMClient(Protocol):
    def evaluate(self, request: LLMClientRequest) -> LLMClientResult: ...
