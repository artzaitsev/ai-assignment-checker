from __future__ import annotations

from app.domain.contracts import LLMClient, StorageClient
from app.domain.dto import EvaluateSubmissionCommand, EvaluateSubmissionResult

COMPONENT_ID = "domain.llm.evaluate"


def evaluate_submission(
    cmd: EvaluateSubmissionCommand,
    *,
    llm: LLMClient,
    storage: StorageClient,
) -> EvaluateSubmissionResult:
    """Here you can implement production business logic for domain.llm.evaluate."""
    result = llm.evaluate(prompt=cmd.normalized_ref, model_version=cmd.model_version)
    llm_output_ref = storage.put_bytes(
        key=f"llm-output/{cmd.submission_id}.json",
        payload=result.detail.encode("utf-8"),
    )
    feedback_ref = storage.put_bytes(
        key=f"feedback/{cmd.submission_id}.json",
        payload=b"{\"status\":\"skeleton\"}",
    )
    return EvaluateSubmissionResult(
        llm_output_ref=llm_output_ref,
        feedback_ref=feedback_ref,
        model_version=cmd.model_version,
    )
