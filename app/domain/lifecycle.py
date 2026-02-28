from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageLifecycle:
    stage: str
    source_state: str
    in_progress_state: str
    success_state: str
    failed_state: str
    attempt_field: str
    max_attempts: int = 3


STAGE_LIFECYCLES: dict[str, StageLifecycle] = {
    "raw": StageLifecycle(
        stage="raw",
        source_state="telegram_update_received",
        in_progress_state="telegram_ingest_in_progress",
        success_state="uploaded",
        failed_state="failed_telegram_ingest",
        attempt_field="attempt_telegram_ingest",
    ),
    "normalized": StageLifecycle(
        stage="normalized",
        source_state="uploaded",
        in_progress_state="normalization_in_progress",
        success_state="normalized",
        failed_state="failed_normalization",
        attempt_field="attempt_normalization",
    ),
    "llm-output": StageLifecycle(
        stage="llm-output",
        source_state="normalized",
        in_progress_state="evaluation_in_progress",
        success_state="evaluated",
        failed_state="failed_evaluation",
        attempt_field="attempt_evaluation",
    ),
    "exports": StageLifecycle(
        stage="exports",
        source_state="evaluated",
        in_progress_state="delivery_in_progress",
        success_state="delivered",
        failed_state="failed_delivery",
        attempt_field="attempt_delivery",
    ),
}


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "telegram_update_received": {"telegram_ingest_in_progress"},
    "telegram_ingest_in_progress": {"uploaded", "telegram_update_received", "failed_telegram_ingest", "dead_letter"},
    "uploaded": {"normalization_in_progress"},
    "normalization_in_progress": {"normalized", "uploaded", "failed_normalization", "dead_letter"},
    "normalized": {"evaluation_in_progress"},
    "evaluation_in_progress": {"evaluated", "normalized", "failed_evaluation", "dead_letter"},
    "evaluated": {"delivery_in_progress"},
    "delivery_in_progress": {"delivered", "evaluated", "failed_delivery", "dead_letter"},
    "dead_letter": set(),
}
