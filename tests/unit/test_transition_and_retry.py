from __future__ import annotations

import asyncio

import pytest

from app.domain.errors import DomainInvariantError
from app.domain.lifecycle import ALLOWED_TRANSITIONS
from app.repositories.stub import InMemoryWorkRepository


@pytest.mark.unit
def test_transition_guard_map_rejects_invalid_transition() -> None:
    repo = InMemoryWorkRepository()

    async def _run() -> None:
        with pytest.raises(DomainInvariantError):
            await repo.transition_state(item_id="sub-1", from_state="uploaded", to_state="evaluated")

    asyncio.run(_run())
    assert "normalization_in_progress" in ALLOWED_TRANSITIONS["uploaded"]


@pytest.mark.unit
def test_retry_counter_and_error_code_persisted_on_failure() -> None:
    repo = InMemoryWorkRepository()

    async def _run() -> None:
        candidate = await repo.create_candidate(first_name="Unit", last_name="Candidate")
        assignment = await repo.create_assignment(title="Assignment", description="Unit")
        created = await repo.create_submission_with_source(
            candidate_public_id=candidate.candidate_public_id,
            assignment_public_id=assignment.assignment_public_id,
            source_type="api_upload",
            source_external_id="retry-unit-1",
            initial_status="uploaded",
        )
        claim = await repo.claim_next(stage="normalized", worker_id="worker-normalize")
        assert claim is not None
        await repo.finalize(
            item_id=claim.item_id,
            stage="normalized",
            worker_id="worker-normalize",
            success=False,
            detail="bad payload",
            error_code="schema_validation_failed",
        )
        snapshot = await repo.get_submission(submission_id=created.submission_id)
        assert snapshot is not None
        assert snapshot.attempt_normalization == 1
        assert snapshot.last_error_code == "schema_validation_failed"
        assert snapshot.status == "uploaded"

    asyncio.run(_run())
