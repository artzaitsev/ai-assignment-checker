from __future__ import annotations

import json

from app.domain.contracts import LLMClient
from app.domain.dto import EvaluateSubmissionCommand, EvaluateSubmissionResult, LLMClientRequest
from app.domain.evaluation_chain import render_user_prompt, validate_llm_response
from app.domain.scoring import CriteriaScore, deterministic_score_1_10

COMPONENT_ID = "domain.llm.evaluate"


def evaluate_submission(
    cmd: EvaluateSubmissionCommand,
    *,
    llm: LLMClient,
) -> EvaluateSubmissionResult:
    """Evaluate normalized content using chain spec and deterministic scoring."""
    prompt_inputs: dict[str, object] = {
        "assignment": {
            "title": cmd.assignment_title,
            "description": cmd.assignment_description,
        },
        "normalized": {
            "content_markdown": cmd.normalized_artifact.content_markdown,
        },
    }
    user_prompt = render_user_prompt(
        template=cmd.chain_spec.prompts.user_template,
        inputs=prompt_inputs,
        spec=cmd.chain_spec,
    )

    llm_result = llm.evaluate(
        LLMClientRequest(
            system_prompt=cmd.chain_spec.prompts.system,
            user_prompt=user_prompt,
            model=cmd.chain_spec.model,
            temperature=cmd.chain_spec.runtime.temperature,
            seed=cmd.chain_spec.runtime.seed,
            response_language=cmd.chain_spec.runtime.response_language,
        )
    )

    payload = llm_result.raw_json
    if payload is None:
        try:
            loaded = json.loads(llm_result.raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError("llm output is not valid JSON") from exc
        if not isinstance(loaded, dict):
            raise ValueError("llm output root must be JSON object")
        payload = loaded

    validate_llm_response(payload=payload, schema=cmd.chain_spec.llm_response)

    criteria_items_raw = payload.get("criteria")
    if not isinstance(criteria_items_raw, list):
        raise ValueError("llm response must include criteria array")
    rubric_weights = {item.id: item.weight for item in cmd.chain_spec.rubric.criteria}
    criteria_for_score: list[CriteriaScore] = []
    criteria_scores_json_items: list[dict[str, object]] = []
    for entry in criteria_items_raw:
        if not isinstance(entry, dict):
            raise ValueError("criteria entry must be object")
        criterion_id = entry.get("id")
        score = entry.get("score")
        reason = entry.get("reason")
        if not isinstance(criterion_id, str) or criterion_id not in rubric_weights:
            raise ValueError("criteria entry id is invalid")
        if not isinstance(score, int):
            raise ValueError("criteria entry score must be integer")
        if not isinstance(reason, str):
            raise ValueError("criteria entry reason must be string")
        criteria_for_score.append(
            CriteriaScore(name=criterion_id, score=score, weight=rubric_weights[criterion_id])
        )
        criteria_scores_json_items.append(
            {
                "id": criterion_id,
                "score": score,
                "reason": reason,
                "weight": rubric_weights[criterion_id],
            }
        )

    score_1_10 = deterministic_score_1_10(criteria=criteria_for_score)

    organizer_feedback = payload.get("organizer_feedback")
    candidate_feedback = payload.get("candidate_feedback")
    ai_assistance = payload.get("ai_assistance")
    if not isinstance(organizer_feedback, dict):
        raise ValueError("organizer_feedback must be object")
    if not isinstance(candidate_feedback, dict):
        raise ValueError("candidate_feedback must be object")
    if not isinstance(ai_assistance, dict):
        raise ValueError("ai_assistance must be object")

    required_ai_fields = cmd.chain_spec.rubric.ai_assistance_policy.require_fields
    for field in required_ai_fields:
        if field not in ai_assistance:
            raise ValueError(f"ai_assistance.{field} is required by chain policy")
    likelihood = ai_assistance.get("likelihood")
    confidence = ai_assistance.get("confidence")
    if not isinstance(likelihood, (int, float)):
        raise ValueError("ai_assistance.likelihood must be number")
    if not isinstance(confidence, (int, float)):
        raise ValueError("ai_assistance.confidence must be number")

    return EvaluateSubmissionResult(
        model=cmd.chain_spec.model,
        chain_version=cmd.chain_spec.chain_version,
        response_language=cmd.chain_spec.runtime.response_language,
        temperature=cmd.chain_spec.runtime.temperature,
        seed=cmd.chain_spec.runtime.seed,
        tokens_input=llm_result.tokens_input,
        tokens_output=llm_result.tokens_output,
        latency_ms=llm_result.latency_ms,
        score_1_10=score_1_10,
        criteria_scores_json={
            "items": criteria_scores_json_items,
        },
        organizer_feedback_json=dict(organizer_feedback),
        candidate_feedback_json=dict(candidate_feedback),
        ai_assistance_likelihood=float(likelihood),
        ai_assistance_confidence=float(confidence),
        reproducibility_subset={
            "chain_version": cmd.chain_spec.chain_version,
            "spec_version": cmd.chain_spec.spec_version,
            "model": cmd.chain_spec.model,
            "response_language": cmd.chain_spec.runtime.response_language,
        },
    )
