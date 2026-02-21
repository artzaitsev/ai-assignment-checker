from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.domain.contracts import WorkRepository
from app.domain.models import ProcessResult, WorkItemClaim

ProcessHandler = Callable[[WorkItemClaim], ProcessResult]


@dataclass
class WorkerLoop:
    role: str
    stage: str
    repository: WorkRepository
    process: ProcessHandler

    def run_once(self) -> bool:
        claim = self.repository.claim_next(stage=self.stage, worker_id=self.role)
        if claim is None:
            return False

        self.repository.transition_state(
            item_id=claim.item_id,
            from_state=claim.stage,
            to_state=f"{claim.stage}:processing",
        )
        result = self.process(claim)
        if result.artifact_ref:
            self.repository.link_artifact(
                item_id=claim.item_id,
                stage=self.stage,
                artifact_ref=result.artifact_ref,
                artifact_version=result.artifact_version,
            )

        self.repository.finalize(
            item_id=claim.item_id,
            stage=self.stage,
            success=result.success,
            detail=result.detail,
        )
        return True
