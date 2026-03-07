from __future__ import annotations

import json

from app.domain.assignment_criteria import AssignmentCriteriaSchema, parse_assignment_criteria_schema
from app.domain.contracts import LLMClient
from app.domain.dto import EvaluateSubmissionCommand, EvaluateSubmissionResult, LLMClientRequest
from app.domain.evaluation_chain import render_user_prompt, validate_llm_response
from app.domain.scoring import (
    CriteriaScore,
    TaskScore,
    deterministic_score_1_10,
    deterministic_weighted_overall_score_1_10,
)

COMPONENT_ID = "domain.llm.evaluate"


def evaluate_submission(
    cmd: EvaluateSubmissionCommand,
    *,
    llm: LLMClient,
) -> EvaluateSubmissionResult:
    """Evaluate normalized content using chain spec and deterministic scoring."""
    criteria_schema = _resolve_criteria_schema(cmd.criteria_schema_json)
    prompt_inputs: dict[str, object] = {
        "assignment": {
            "title": cmd.assignment_title,
            "description": cmd.assignment_description,
            "criteria_schema_json": cmd.criteria_schema_json,
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

    if criteria_schema is None:
        score_1_10, criteria_scores_json = _parse_legacy_scores(payload=payload, cmd=cmd)
    else:
        score_1_10, criteria_scores_json = _parse_multitask_scores(payload=payload, schema=criteria_schema)

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
        criteria_scores_json=criteria_scores_json,
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


def _resolve_criteria_schema(raw: dict[str, object] | None) -> AssignmentCriteriaSchema | None:
    if raw is None:
        return None
    return parse_assignment_criteria_schema(raw)


def _parse_legacy_scores(
    *,
    payload: dict[str, object],
    cmd: EvaluateSubmissionCommand,
) -> tuple[int, dict[str, object]]:
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
    return score_1_10, {"items": criteria_scores_json_items}


def _parse_multitask_scores(
    *,
    payload: dict[str, object],
    schema: AssignmentCriteriaSchema,
) -> tuple[int, dict[str, object]]:
    tasks_raw = payload.get("tasks")
    if not isinstance(tasks_raw, list):
        raise ValueError("llm response must include tasks array")
    llm_tasks_by_id: dict[str, dict[str, object]] = {}
    for task in tasks_raw:
        if not isinstance(task, dict):
            raise ValueError("tasks entry must be object")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("tasks[].task_id is required")
        if task_id in llm_tasks_by_id:
            raise ValueError("tasks[].task_id must be unique")
        llm_tasks_by_id[task_id] = task

    schema_task_ids = {task.task_id for task in schema.tasks}
    if set(llm_tasks_by_id.keys()) != schema_task_ids:
        raise ValueError("llm tasks payload does not match configured task_ids")

    task_scores_json: dict[str, int] = {}
    task_weights_json: dict[str, float] = {}
    task_scores_for_overall: list[TaskScore] = []
    criteria_items: list[dict[str, object]] = []

    for task_def in schema.tasks:
        llm_task = llm_tasks_by_id[task_def.task_id]
        criteria_raw = llm_task.get("criteria")
        if not isinstance(criteria_raw, list):
            raise ValueError("tasks[].criteria must be array")

        llm_criteria_by_id: dict[str, dict[str, object]] = {}
        for criterion in criteria_raw:
            if not isinstance(criterion, dict):
                raise ValueError("tasks[].criteria[] must be object")
            criterion_id = criterion.get("criterion_id")
            if not isinstance(criterion_id, str) or not criterion_id:
                raise ValueError("tasks[].criteria[].criterion_id is required")
            if criterion_id in llm_criteria_by_id:
                raise ValueError("tasks[].criteria[].criterion_id must be unique")
            llm_criteria_by_id[criterion_id] = criterion

        expected_criteria_ids = {item.criterion_id for item in task_def.criteria}
        if set(llm_criteria_by_id.keys()) != expected_criteria_ids:
            raise ValueError("llm criteria payload does not match configured criterion_ids")

        criteria_for_task: list[CriteriaScore] = []
        for criterion_def in task_def.criteria:
            llm_criterion = llm_criteria_by_id[criterion_def.criterion_id]
            score = llm_criterion.get("score")
            reason = llm_criterion.get("reason")
            if not isinstance(score, int):
                raise ValueError("tasks[].criteria[].score must be integer")
            if score < 1 or score > 10:
                raise ValueError("tasks[].criteria[].score must be between 1 and 10")
            if not isinstance(reason, str):
                raise ValueError("tasks[].criteria[].reason must be string")
            criteria_for_task.append(
                CriteriaScore(name=criterion_def.criterion_id, score=score, weight=criterion_def.weight)
            )
            criteria_items.append(
                {
                    "task_id": task_def.task_id,
                    "id": criterion_def.criterion_id,
                    "score": score,
                    "reason": reason,
                    "weight": criterion_def.weight,
                }
            )

        task_score = deterministic_score_1_10(criteria=criteria_for_task)
        task_scores_json[task_def.task_id] = task_score
        task_weights_json[task_def.task_id] = task_def.weight
        task_scores_for_overall.append(
            TaskScore(task_id=task_def.task_id, score=task_score, weight=task_def.weight)
        )

    overall_score_1_10 = deterministic_weighted_overall_score_1_10(task_scores=task_scores_for_overall)
    return overall_score_1_10, {
        "items": criteria_items,
        "task_order": [task.task_id for task in schema.tasks],
        "task_scores": task_scores_json,
        "task_weights": task_weights_json,
        "overall_score_1_10_derived": overall_score_1_10,
        "schema_version": schema.schema_version,
    }
