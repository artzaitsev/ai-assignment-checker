from __future__ import annotations

import logging

from pydantic import ValidationError

from app.domain.evaluation_chain import chain_spec_digest, load_chain_spec, resolved_chain_spec_payload
from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.dto import EvaluateSubmissionCommand
from app.domain.models import AssignmentSnapshot, ProcessResult, SubmissionFieldGroup, SubmissionListQuery, WorkItemClaim
from app.domain.use_cases.llm_eval import evaluate_submission
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.evaluate.process_claim"
DEFAULT_CHAIN_SPEC_PATH = "app/eval/chains/chain.v1.yaml"
CHAIN_MISMATCH_POLICY = "warn-only"
logger = logging.getLogger("runtime")


async def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    """Process evaluate stage with schema and reproducibility contracts."""
    try:
        configured_api_base = _resolve_llm_api_base(llm=deps.llm)
        normalized_artifact_ref = await deps.repository.get_artifact_ref(item_id=claim.item_id, stage="normalized")
        normalized_artifact = deps.artifact_repository.load_normalized(artifact_ref=normalized_artifact_ref)
        assignment = await _resolve_assignment(
            deps,
            submission_id=claim.item_id,
            assignment_public_id=normalized_artifact.assignment_public_id,
        )
        chain_spec = load_chain_spec(file_path=DEFAULT_CHAIN_SPEC_PATH)
        current_chain_digest = chain_spec_digest(spec=chain_spec)
        current_chain_payload = resolved_chain_spec_payload(spec=chain_spec)
        previous_chain_digest = await _load_persisted_chain_digest(deps=deps, submission_id=claim.item_id)
        if previous_chain_digest is not None and previous_chain_digest != current_chain_digest:
            logger.warning(
                "evaluation chain snapshot mismatch; continuing due to warn-only policy",
                extra={
                    "submission_id": claim.item_id,
                    "stage": "llm-output",
                    "mismatch_policy": CHAIN_MISMATCH_POLICY,
                    "previous_chain_digest": previous_chain_digest,
                    "current_chain_digest": current_chain_digest,
                },
            )

        result = evaluate_submission(
            EvaluateSubmissionCommand(
                submission_id=claim.item_id,
                normalized_artifact=normalized_artifact,
                assignment_title=assignment.title,
                assignment_description=assignment.description,
                criteria_schema_json=assignment.criteria_schema_json,
                chain_spec=chain_spec,
            ),
            llm=deps.llm,
        )
    except KeyError as exc:
        error_code = resolve_stage_error(stage="llm-output", code="artifact_missing")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except (ValueError, ValidationError) as exc:
        error_code = resolve_stage_error(stage="llm-output", code="schema_validation_failed")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )
    except Exception as exc:  # pragma: no cover
        error_code = resolve_stage_error(stage="llm-output", code="llm_provider_unavailable")
        return ProcessResult(
            success=False,
            detail=str(exc),
            error_code=error_code,
            retry_classification=classify_error(error_code),
        )

    await deps.repository.persist_llm_run(
        submission_id=claim.item_id,
        provider="openai-compatible",
        model=result.model,
        api_base=configured_api_base,
        chain_version=result.chain_version,
        spec_version=chain_spec.spec_version,
        response_language=result.response_language,
        temperature=result.temperature,
        seed=result.seed,
        tokens_input=result.tokens_input,
        tokens_output=result.tokens_output,
        latency_ms=result.latency_ms,
    )
    await deps.repository.persist_evaluation(
        submission_id=claim.item_id,
        score_1_10=result.score_1_10,
        criteria_scores_json={
            **result.criteria_scores_json,
            "_chain_snapshot": {
                "chain_digest": current_chain_digest,
                "resolved_chain_spec": current_chain_payload,
                "mismatch_policy": CHAIN_MISMATCH_POLICY,
            },
        },
        organizer_feedback_json=result.organizer_feedback_json,
        candidate_feedback_json=result.candidate_feedback_json,
        ai_assistance_likelihood=result.ai_assistance_likelihood,
        ai_assistance_confidence=result.ai_assistance_confidence,
        reproducibility_subset=result.reproducibility_subset,
    )

    return ProcessResult(
        success=True,
        detail="evaluation persisted in relational store",
    )


async def _resolve_assignment(
    deps: WorkerDeps,
    *,
    submission_id: str,
    assignment_public_id: str,
) -> AssignmentSnapshot:
    submission = await deps.repository.get_submission(submission_id=submission_id)
    assignment_candidates = [assignment_public_id]
    if submission is not None and submission.assignment_public_id not in assignment_candidates:
        assignment_candidates.append(submission.assignment_public_id)

    assignments = await deps.repository.list_assignments(active_only=False, include_criteria=True)
    for candidate_id in assignment_candidates:
        for item in assignments:
            if item.assignment_public_id == candidate_id:
                return item

    joined_ids = ", ".join(assignment_candidates)
    raise KeyError(f"assignment not found: {joined_ids}")


async def _load_persisted_chain_digest(deps: WorkerDeps, *, submission_id: str) -> str | None:
    items = await deps.repository.list_submissions(
        query=SubmissionListQuery(
            submission_ids=(submission_id,),
            include=frozenset({SubmissionFieldGroup.EVALUATION}),
            limit=1,
            offset=0,
        )
    )
    if not items:
        return None

    evaluation = items[0].evaluation
    if evaluation is None:
        return None
    criteria = evaluation.criteria_scores_json
    if not isinstance(criteria, dict):
        return None
    snapshot = criteria.get("_chain_snapshot")
    if not isinstance(snapshot, dict):
        return None
    digest = snapshot.get("chain_digest")
    if not isinstance(digest, str) or not digest:
        return None
    return digest


def _resolve_llm_api_base(*, llm: object) -> str:
    api_base = getattr(llm, "base_url", None)
    if isinstance(api_base, str) and api_base.strip():
        return api_base
    raise ValueError("llm client base_url must be configured")
