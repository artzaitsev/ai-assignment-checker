from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkItemClaim:
    item_id: str
    stage: str
    attempt: int


@dataclass(frozen=True)
class ProcessResult:
    success: bool
    detail: str = ""
    artifact_ref: str | None = None
    artifact_version: str | None = None
