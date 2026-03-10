from __future__ import annotations

from dataclasses import dataclass
import json

from app.domain.evaluation_contracts import (
    CandidateFeedback,
    CriterionScore,
    OrganizerFeedback,
    ScoreBreakdown,
    TaskSchema,
    TaskSchemaTask,
    TaskScoreBreakdown,
    parse_candidate_feedback,
    parse_organizer_feedback,
)
from app.domain.contracts import LLMClient
from app.domain.dto import EvaluateSubmissionCommand, EvaluateSubmissionResult, LLMClientRequest
from app.domain.evaluation_chain import render_user_prompt, validate_llm_response
from app.domain.scoring import (
    CriteriaScore as WeightedCriterionScore,
    TaskScore,
    deterministic_score_1_10,
    deterministic_weighted_overall_score_1_10,
)

COMPONENT_ID = "domain.llm.evaluate"


@dataclass(frozen=True)
class LLMTaskCriterionPayload:
    criterion_id: str
    score: int
    reason: str


@dataclass(frozen=True)
class LLMTaskPayload:
    task_id: str
    criteria: tuple[LLMTaskCriterionPayload, ...]


@dataclass(frozen=True)
class LLMAIAssistancePayload:
    likelihood: float
    confidence: float
    raw_fields: dict[str, object]


@dataclass(frozen=True)
class LLMEvaluationPayload:
    tasks: tuple[LLMTaskPayload, ...]
    organizer_feedback: OrganizerFeedback
    candidate_feedback: CandidateFeedback
    ai_assistance: LLMAIAssistancePayload


def evaluate_submission(
    cmd: EvaluateSubmissionCommand,
    *,
    llm: LLMClient,
) -> EvaluateSubmissionResult:
    """Evaluate normalized content using chain spec and deterministic scoring."""
    normalized_payload = _build_normalized_prompt_payload(
        task_solutions=cmd.normalized_artifact.task_solutions,
        submission_text=cmd.normalized_artifact.submission_text,
        unmapped_text=cmd.normalized_artifact.unmapped_text,
    )
    prompt_inputs: dict[str, object] = {
        "assignment": {
            "title": cmd.assignment_title,
            "description": cmd.assignment_description,
            "language": cmd.assignment_language,
            "task_schema": cmd.task_schema.to_dict(),
        },
        "normalized": normalized_payload,
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
            model=cmd.effective_model,
            temperature=cmd.chain_spec.runtime.temperature,
            seed=cmd.chain_spec.runtime.seed,
            response_language=cmd.assignment_language,
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
    typed_payload = _parse_llm_evaluation_payload(payload)
    score_1_10, score_breakdown = _parse_task_scores(payload=typed_payload, schema=cmd.task_schema)

    parsed_organizer_feedback = typed_payload.organizer_feedback
    parsed_candidate_feedback = typed_payload.candidate_feedback

    required_ai_fields = cmd.chain_spec.rubric.ai_assistance_policy.require_fields
    for field in required_ai_fields:
        if field not in typed_payload.ai_assistance.raw_fields:
            raise ValueError(f"ai_assistance.{field} is required by chain policy")

    return EvaluateSubmissionResult(
        model=cmd.effective_model,
        chain_version=cmd.chain_spec.chain_version,
        response_language=cmd.assignment_language,
        temperature=cmd.chain_spec.runtime.temperature,
        seed=cmd.chain_spec.runtime.seed,
        tokens_input=llm_result.tokens_input,
        tokens_output=llm_result.tokens_output,
        latency_ms=llm_result.latency_ms,
        score_1_10=score_1_10,
        score_breakdown=score_breakdown,
        organizer_feedback=parsed_organizer_feedback,
        candidate_feedback=parsed_candidate_feedback,
        ai_assistance_likelihood=typed_payload.ai_assistance.likelihood,
        ai_assistance_confidence=typed_payload.ai_assistance.confidence,
        reproducibility_subset={
            "chain_version": cmd.chain_spec.chain_version,
            "spec_version": cmd.chain_spec.spec_version,
            "model": cmd.effective_model,
            "response_language": cmd.assignment_language,
        },
    )


def _parse_task_scores(
    *,
    payload: LLMEvaluationPayload,
    schema: TaskSchema,
) -> tuple[int, ScoreBreakdown]:
    task_scores_for_overall: list[TaskScore] = []
    scored_tasks: list[TaskScoreBreakdown] = []
    llm_tasks_by_id = _index_llm_tasks_by_id(payload.tasks)
    _validate_task_ids(llm_task_ids=set(llm_tasks_by_id.keys()), schema=schema)

    for task_def in schema.tasks:
        scored_task, task_score = _score_task(task_def=task_def, llm_task=llm_tasks_by_id[task_def.task_id])
        task_scores_for_overall.append(TaskScore(task_id=task_def.task_id, score=task_score, weight=task_def.weight))
        scored_tasks.append(scored_task)

    overall_score_1_10 = deterministic_weighted_overall_score_1_10(task_scores=task_scores_for_overall)
    return overall_score_1_10, ScoreBreakdown(
        schema_version=schema.schema_version,
        tasks=tuple(scored_tasks),
        overall_score_1_10_derived=overall_score_1_10,
    )


def _score_task(
    *,
    task_def: TaskSchemaTask,
    llm_task: LLMTaskPayload,
) -> tuple[TaskScoreBreakdown, int]:
    llm_criteria_by_id = _index_llm_criteria_by_id(llm_task.criteria)
    _validate_criterion_ids(llm_criterion_ids=set(llm_criteria_by_id.keys()), task_def=task_def)

    criteria_for_task: list[WeightedCriterionScore] = []
    scored_criteria: list[CriterionScore] = []
    for criterion_def in task_def.criteria:
        llm_criterion = llm_criteria_by_id[criterion_def.criterion_id]
        criteria_for_task.append(
            WeightedCriterionScore(
                name=criterion_def.criterion_id,
                score=llm_criterion.score,
                weight=criterion_def.weight,
            )
        )
        scored_criteria.append(
            CriterionScore(
                criterion_id=criterion_def.criterion_id,
                score=llm_criterion.score,
                reason=llm_criterion.reason,
                weight=criterion_def.weight,
            )
        )

    task_score = deterministic_score_1_10(criteria=criteria_for_task)
    return (
        TaskScoreBreakdown(
            task_id=task_def.task_id,
            score_1_10=task_score,
            weight=task_def.weight,
            criteria=tuple(scored_criteria),
        ),
        task_score,
    )


def _index_llm_tasks_by_id(tasks: tuple[LLMTaskPayload, ...]) -> dict[str, LLMTaskPayload]:
    return {task.task_id: task for task in tasks}


def _validate_task_ids(*, llm_task_ids: set[str], schema: TaskSchema) -> None:
    schema_task_ids = {task.task_id for task in schema.tasks}
    if llm_task_ids != schema_task_ids:
        raise ValueError("llm tasks payload does not match configured task_ids")


def _index_llm_criteria_by_id(
    criteria: tuple[LLMTaskCriterionPayload, ...],
) -> dict[str, LLMTaskCriterionPayload]:
    return {criterion.criterion_id: criterion for criterion in criteria}


def _validate_criterion_ids(*, llm_criterion_ids: set[str], task_def: TaskSchemaTask) -> None:
    expected_criteria_ids = {item.criterion_id for item in task_def.criteria}
    if llm_criterion_ids != expected_criteria_ids:
        raise ValueError("llm criteria payload does not match configured criterion_ids")


# LLM payload parsing helpers.
def _parse_llm_evaluation_payload(payload: dict[str, object]) -> LLMEvaluationPayload:
    return LLMEvaluationPayload(
        tasks=_parse_llm_tasks(payload),
        organizer_feedback=parse_organizer_feedback(_require_object_field(payload, "organizer_feedback")),
        candidate_feedback=parse_candidate_feedback(_require_object_field(payload, "candidate_feedback")),
        ai_assistance=_parse_llm_ai_assistance(_require_object_field(payload, "ai_assistance")),
    )


def _parse_llm_tasks(payload: dict[str, object]) -> tuple[LLMTaskPayload, ...]:
    tasks_raw = _require_list(payload, "tasks", "llm response")
    tasks: list[LLMTaskPayload] = []
    seen_task_ids: set[str] = set()
    for task_item in tasks_raw:
        task = _parse_llm_task(task_item)
        if task.task_id in seen_task_ids:
            raise ValueError("tasks[].task_id must be unique")
        seen_task_ids.add(task.task_id)
        tasks.append(task)
    return tuple(tasks)


def _parse_llm_task(task_item: object) -> LLMTaskPayload:
    task = _require_object(task_item, "tasks entry")
    task_id = _require_non_empty_str(task, "task_id", "tasks[].task_id")
    criteria = _parse_llm_criteria(task)
    return LLMTaskPayload(task_id=task_id, criteria=criteria)


def _parse_llm_criteria(task: dict[str, object]) -> tuple[LLMTaskCriterionPayload, ...]:
    criteria_raw = _require_list(task, "criteria", "tasks[]")
    criteria: list[LLMTaskCriterionPayload] = []
    seen_criterion_ids: set[str] = set()
    for criterion_item in criteria_raw:
        criterion = _parse_llm_criterion(criterion_item)
        if criterion.criterion_id in seen_criterion_ids:
            raise ValueError("tasks[].criteria[].criterion_id must be unique")
        seen_criterion_ids.add(criterion.criterion_id)
        criteria.append(criterion)
    return tuple(criteria)


def _parse_llm_criterion(criterion_item: object) -> LLMTaskCriterionPayload:
    criterion = _require_object(criterion_item, "tasks[].criteria[]")
    criterion_id = _require_non_empty_str(
        criterion,
        "criterion_id",
        "tasks[].criteria[].criterion_id",
    )
    score = _require_score(criterion, "score", "tasks[].criteria[].score")
    reason = _require_str(criterion, "reason", "tasks[].criteria[].reason")
    return LLMTaskCriterionPayload(criterion_id=criterion_id, score=score, reason=reason)


def _parse_llm_ai_assistance(ai_assistance: dict[str, object]) -> LLMAIAssistancePayload:
    likelihood = ai_assistance.get("likelihood")
    confidence = ai_assistance.get("confidence")
    if not isinstance(likelihood, (int, float)):
        raise ValueError("ai_assistance.likelihood must be number")
    if not isinstance(confidence, (int, float)):
        raise ValueError("ai_assistance.confidence must be number")
    return LLMAIAssistancePayload(
        likelihood=float(likelihood),
        confidence=float(confidence),
        raw_fields=dict(ai_assistance),
    )


def _require_object(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be object")
    return value


def _require_object_field(data: dict[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be object")
    return value


def _require_list(data: dict[str, object], key: str, path: str) -> list[object]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{path}.{key} must be array")
    return value


def _require_str(data: dict[str, object], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{path} must be string")
    return value


def _require_non_empty_str(data: dict[str, object], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} is required")
    return value


def _require_score(data: dict[str, object], key: str, path: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise ValueError(f"{path} must be integer")
    if value < 1 or value > 10:
        raise ValueError(f"{path} must be between 1 and 10")
    return value


def _build_normalized_prompt_payload(
    *,
    task_solutions: list[dict[str, str]],
    submission_text: str,
    unmapped_text: str,
) -> dict[str, object]:
    mapped = [item for item in task_solutions if isinstance(item, dict)]
    mapped_non_empty = [
        item
        for item in mapped
        if isinstance(item.get("answer"), str) and item.get("answer", "").strip()
    ]

    fallback_context = submission_text
    if mapped_non_empty and unmapped_text.strip():
        fallback_context = unmapped_text

    return {
        "submission_text": submission_text,
        "task_solutions": mapped,
        "unmapped_text": unmapped_text,
        "fallback_context": fallback_context,
    }
