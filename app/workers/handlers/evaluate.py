from __future__ import annotations
import os
from pydantic import ValidationError

from app.domain.evaluation_chain import load_chain_spec
from app.domain.error_taxonomy import classify_error, resolve_stage_error
from app.domain.dto import EvaluateSubmissionCommand
from app.domain.models import AssignmentSnapshot, ProcessResult, WorkItemClaim
from app.domain.use_cases.llm_eval import evaluate_submission
from app.workers.handlers.deps import WorkerDeps

COMPONENT_ID = "worker.evaluate.process_claim"
DEFAULT_CHAIN_SPEC_PATH = "app/eval/chains/chain.v1.yaml"


async def process_claim(deps: WorkerDeps, *, claim: WorkItemClaim) -> ProcessResult:
    """Process evaluate stage with schema and reproducibility contracts."""
    try:
        normalized_artifact_ref = await deps.repository.get_artifact_ref(item_id=claim.item_id, stage="normalized")
        normalized_artifact = deps.artifact_repository.load_normalized(artifact_ref=normalized_artifact_ref)
        assignment = await _resolve_assignment(
            deps,
            submission_id=claim.item_id,
            assignment_public_id=normalized_artifact.assignment_public_id,
        )
        
        #chain_spec = load_chain_spec(file_path=DEFAULT_CHAIN_SPEC_PATH)
        # получаем из среды данные о CHAIN_VERSION. Нужно, чтобы можно было выбирать другую версиию
        chain_version = os.getenv("CHAIN_VERSION", "v1")
        
        chain_spec_path = f"app/eval/chains/chain.{chain_version}.yaml"
        chain_spec = load_chain_spec(file_path=chain_spec_path)


        
        result = await evaluate_submission(
            EvaluateSubmissionCommand(
                submission_id=claim.item_id,
                normalized_artifact=normalized_artifact,
                assignment_title=assignment.title,
                assignment_description=assignment.description,
                chain_spec=chain_spec,
            ),
            llm=deps.llm,
        )
        
        # ------------------
        # Save llm_output
        llm_output_key = f"llm_output/{claim.item_id}.json"
        deps.storage.put_bytes(key=llm_output_key, payload=result.raw_output.encode("utf-8"))
        await deps.repository.link_artifact(
            item_id=claim.item_id,
            stage="llm-output",
            artifact_ref=llm_output_key,
            artifact_version=result.chain_version,
        )
       

        # Save feedback
        feedback_text = _format_candidate_feedback(result.candidate_feedback_json)
        feedback_key = f"feedback/{claim.item_id}.txt"
        deps.storage.put_bytes(key=feedback_key, payload=feedback_text.encode("utf-8"))
        await deps.repository.link_artifact(
            item_id=claim.item_id,
            stage="feedback",
            artifact_ref=feedback_key,
            artifact_version=None,
        )
    
        # --------------------
    
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
        api_base="https://example.invalid",
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
        criteria_scores_json=result.criteria_scores_json,
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

    assignments = await deps.repository.list_assignments(active_only=False)
    for candidate_id in assignment_candidates:
        for item in assignments:
            if item.assignment_public_id == candidate_id:
                return item

    joined_ids = ", ".join(assignment_candidates)
    raise KeyError(f"assignment not found: {joined_ids}")

def _format_candidate_feedback(candidate_json: dict) -> str:
    summary = candidate_json.get("summary", "")
    went_well = candidate_json.get("what_went_well", [])
    to_improve = candidate_json.get("what_to_improve", [])
    lines = [summary]
    if went_well:
        lines.append("\n✅ Что получилось хорошо:")
        lines.extend(f"  • {item}" for item in went_well)
    if to_improve:
        lines.append("\n🔧 Что можно улучшить:")
        lines.extend(f"  • {item}" for item in to_improve)
    return "\n".join(lines)