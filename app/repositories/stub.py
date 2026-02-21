from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.models import WorkItemClaim


@dataclass
class InMemoryWorkRepository:
    """Non-network placeholder repository for skeleton mode."""

    queue: list[WorkItemClaim] = field(default_factory=list)
    transitions: list[tuple[str, str, str]] = field(default_factory=list)
    artifacts: list[tuple[str, str, str, str | None]] = field(default_factory=list)
    finalizations: list[tuple[str, str, bool, str]] = field(default_factory=list)

    def claim_next(self, *, stage: str, worker_id: str) -> WorkItemClaim | None:
        del worker_id
        for index, item in enumerate(self.queue):
            if item.stage == stage:
                return self.queue.pop(index)
        return None

    def transition_state(self, *, item_id: str, from_state: str, to_state: str) -> None:
        self.transitions.append((item_id, from_state, to_state))

    def link_artifact(
        self,
        *,
        item_id: str,
        stage: str,
        artifact_ref: str,
        artifact_version: str | None,
    ) -> None:
        self.artifacts.append((item_id, stage, artifact_ref, artifact_version))

    def finalize(
        self,
        *,
        item_id: str,
        stage: str,
        success: bool,
        detail: str,
    ) -> None:
        self.finalizations.append((item_id, stage, success, detail))
