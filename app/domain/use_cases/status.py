from __future__ import annotations

from app.domain.dto import LinkArtifactCommand, TransitionStateCommand

COMPONENT_ID_TRANSITION = "domain.submission.transition_state"
COMPONENT_ID_ARTIFACT = "domain.artifact.link"


def transition_submission_state(cmd: TransitionStateCommand) -> None:
    """Here you can implement production business logic for domain.submission.transition_state."""
    del cmd


def link_artifact(cmd: LinkArtifactCommand) -> None:
    """Here you can implement production business logic for domain.artifact.link."""
    del cmd
