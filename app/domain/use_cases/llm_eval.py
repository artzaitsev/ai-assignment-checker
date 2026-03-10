from __future__ import annotations

from dataclasses import dataclass
import json
import logging

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
logger = logging.getLogger("runtime")
_EVALUATION_JSON_REPAIR_SYSTEM_PROMPT = (
    "You repair evaluator output into strict JSON object. "
    "Return only JSON, no markdown, no explanations."
)


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
        except json.JSONDecodeError:
            loaded = _repair_malformed_evaluation_json(
                malformed_output=llm_result.raw_text,
                cmd=cmd,
                llm=llm,
            )
            if loaded is None:
                raise ValueError("llm output is not valid JSON")
        if not isinstance(loaded, dict):
            raise ValueError("llm output root must be JSON object")
        payload = loaded

    typed_payload, evaluation_diagnostics = _parse_with_repair_or_fallback(
        payload=payload,
        cmd=cmd,
        llm=llm,
    )
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
        evaluation_diagnostics=evaluation_diagnostics,
    )


def _parse_with_repair_or_fallback(
    *,
    payload: dict[str, object],
    cmd: EvaluateSubmissionCommand,
    llm: LLMClient,
) -> tuple[LLMEvaluationPayload, dict[str, object]]:
    diagnostics: dict[str, object] = {
        "original_shape": _payload_shape(payload),
        "repair_applied": False,
        "fallback_used": False,
    }

    try:
        validate_llm_response(payload=payload, schema=cmd.chain_spec.llm_response)
        typed_payload = _parse_llm_evaluation_payload(payload)
        aligned_payload, aligned = _align_payload_to_schema(payload=typed_payload, schema=cmd.task_schema)
        diagnostics["repair_applied"] = aligned
        if aligned:
            diagnostics["alignment"] = "schema_alignment"
        return aligned_payload, diagnostics
    except ValueError as exc:
        diagnostics["strict_error"] = str(exc)

    repaired_payload, repair_notes = _repair_llm_payload_shape(payload)
    if repaired_payload is not None:
        diagnostics["repair_applied"] = True
        diagnostics["repair_notes"] = repair_notes
        try:
            validate_llm_response(payload=repaired_payload, schema=cmd.chain_spec.llm_response)
            typed_payload = _parse_llm_evaluation_payload(repaired_payload)
            aligned_payload, aligned = _align_payload_to_schema(payload=typed_payload, schema=cmd.task_schema)
            if aligned:
                diagnostics["alignment"] = "schema_alignment"
            return aligned_payload, diagnostics
        except ValueError as exc:
            diagnostics["repair_error"] = str(exc)

    repaired_by_model = _repair_llm_payload_with_model(payload=payload, cmd=cmd, llm=llm)
    if repaired_by_model is not None:
        diagnostics["repair_applied"] = True
        existing_notes_obj = diagnostics.get("repair_notes")
        existing_notes: list[object] = []
        if isinstance(existing_notes_obj, list):
            existing_notes = [item for item in existing_notes_obj]
        diagnostics["repair_notes"] = [*existing_notes, "llm_json_repair"]
        try:
            validate_llm_response(payload=repaired_by_model, schema=cmd.chain_spec.llm_response)
            typed_payload = _parse_llm_evaluation_payload(repaired_by_model)
            aligned_payload, aligned = _align_payload_to_schema(payload=typed_payload, schema=cmd.task_schema)
            if aligned:
                diagnostics["alignment"] = "schema_alignment"
            return aligned_payload, diagnostics
        except ValueError as exc:
            diagnostics["repair_error"] = str(exc)

    diagnostics["fallback_used"] = False
    logger.error(
        "llm evaluation payload is invalid after bounded repair",
        extra={
            "component": COMPONENT_ID,
            "submission_id": cmd.submission_id,
            "strict_error": diagnostics.get("strict_error"),
            "repair_error": diagnostics.get("repair_error"),
        },
    )
    raise ValueError(
        "llm output failed schema validation after bounded repair"
    )


def _repair_malformed_evaluation_json(
    *,
    malformed_output: str,
    cmd: EvaluateSubmissionCommand,
    llm: LLMClient,
) -> dict[str, object] | None:
    repair_payload = {
        "assignment_language": cmd.assignment_language,
        "task_schema": cmd.task_schema.to_dict(),
        "llm_response_schema": cmd.chain_spec.llm_response,
        "malformed_output": malformed_output,
    }
    repair_result = llm.evaluate(
        LLMClientRequest(
            system_prompt=_EVALUATION_JSON_REPAIR_SYSTEM_PROMPT,
            user_prompt=json.dumps(repair_payload, ensure_ascii=False, sort_keys=True),
            model=cmd.effective_model,
            temperature=0.0,
            seed=42,
            response_language=cmd.assignment_language,
        )
    )
    if isinstance(repair_result.raw_json, dict):
        return repair_result.raw_json
    try:
        decoded = json.loads(repair_result.raw_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


def _repair_llm_payload_with_model(
    *,
    payload: dict[str, object],
    cmd: EvaluateSubmissionCommand,
    llm: LLMClient,
) -> dict[str, object] | None:
    repair_payload = {
        "assignment_language": cmd.assignment_language,
        "task_schema": cmd.task_schema.to_dict(),
        "llm_response_schema": cmd.chain_spec.llm_response,
        "invalid_payload": payload,
    }
    repair_result = llm.evaluate(
        LLMClientRequest(
            system_prompt=_EVALUATION_JSON_REPAIR_SYSTEM_PROMPT,
            user_prompt=json.dumps(repair_payload, ensure_ascii=False, sort_keys=True),
            model=cmd.effective_model,
            temperature=0.0,
            seed=42,
            response_language=cmd.assignment_language,
        )
    )
    if isinstance(repair_result.raw_json, dict):
        return repair_result.raw_json
    try:
        decoded = json.loads(repair_result.raw_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


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


def _align_payload_to_schema(*, payload: LLMEvaluationPayload, schema: TaskSchema) -> tuple[LLMEvaluationPayload, bool]:
    tasks_by_id = _index_llm_tasks_by_id(payload.tasks)
    expected_task_ids = {task.task_id for task in schema.tasks}
    if set(tasks_by_id.keys()) != expected_task_ids:
        raise ValueError("llm tasks payload does not match configured task_ids")

    aligned_tasks: list[LLMTaskPayload] = []
    changed = False
    for task_def in schema.tasks:
        llm_task = tasks_by_id[task_def.task_id]
        criteria_by_id = _index_llm_criteria_by_id(llm_task.criteria)
        expected_criteria_ids = {item.criterion_id for item in task_def.criteria}
        if set(criteria_by_id.keys()) != expected_criteria_ids:
            raise ValueError("llm criteria payload does not match configured criterion_ids")

        aligned_criteria = tuple(criteria_by_id[item.criterion_id] for item in task_def.criteria)
        if tuple(item.criterion_id for item in llm_task.criteria) != tuple(item.criterion_id for item in aligned_criteria):
            changed = True
        aligned_tasks.append(LLMTaskPayload(task_id=task_def.task_id, criteria=aligned_criteria))

    if not changed and tuple(task.task_id for task in payload.tasks) == tuple(task.task_id for task in aligned_tasks):
        return payload, False

    return (
        LLMEvaluationPayload(
            tasks=tuple(aligned_tasks),
            organizer_feedback=payload.organizer_feedback,
            candidate_feedback=payload.candidate_feedback,
            ai_assistance=payload.ai_assistance,
        ),
        True,
    )


def _repair_llm_payload_shape(payload: dict[str, object]) -> tuple[dict[str, object] | None, list[str]]:
    notes: list[str] = []
    root = payload
    for key in ("result", "response", "output", "evaluation", "data"):
        nested = root.get(key)
        if isinstance(nested, dict):
            root = nested
            notes.append(f"unwrapped:{key}")
            break

    required_keys = {"tasks", "organizer_feedback", "candidate_feedback", "ai_assistance"}
    if required_keys.issubset(root.keys()):
        return root, notes

    repaired: dict[str, object] = {}

    tasks_value = _first_present(root, ("tasks", "task_results", "taskScores", "results"))
    if isinstance(tasks_value, list):
        normalized_tasks: list[dict[str, object]] = []
        for item in tasks_value:
            if not isinstance(item, dict):
                continue
            task_id = _first_present(item, ("task_id", "taskId"))
            criteria_value = _first_present(item, ("criteria", "criterion_scores", "criteria_scores"))
            if not isinstance(task_id, str) or not isinstance(criteria_value, list):
                continue
            normalized_criteria: list[dict[str, object]] = []
            for criterion in criteria_value:
                if not isinstance(criterion, dict):
                    continue
                criterion_id = _first_present(criterion, ("criterion_id", "criterionId"))
                score = _coerce_score(_first_present(criterion, ("score", "rating", "value")))
                reason_raw = _first_present(criterion, ("reason", "rationale", "comment"))
                reason = reason_raw if isinstance(reason_raw, str) else ""
                if isinstance(criterion_id, str) and score is not None:
                    normalized_criteria.append(
                        {
                            "criterion_id": criterion_id,
                            "score": score,
                            "reason": reason,
                        }
                    )
            normalized_tasks.append(
                {
                    "task_id": task_id,
                    "criteria": normalized_criteria,
                }
            )
        repaired["tasks"] = normalized_tasks
        notes.append("normalized:tasks")

    organizer_value = _first_present(root, ("organizer_feedback", "organizerFeedback", "reviewer_feedback"))
    if isinstance(organizer_value, dict):
        repaired["organizer_feedback"] = {
            "strengths": _coerce_string_list(_first_present(organizer_value, ("strengths",))),
            "issues": _coerce_string_list(_first_present(organizer_value, ("issues", "weaknesses"))),
            "recommendations": _coerce_string_list(
                _first_present(organizer_value, ("recommendations", "next_steps"))
            ),
        }
        notes.append("normalized:organizer_feedback")

    candidate_value = _first_present(root, ("candidate_feedback", "candidateFeedback", "feedback"))
    if isinstance(candidate_value, dict):
        summary = _first_present(candidate_value, ("summary",))
        repaired["candidate_feedback"] = {
            "summary": summary if isinstance(summary, str) else "",
            "what_went_well": _coerce_string_list(_first_present(candidate_value, ("what_went_well", "strengths"))),
            "what_to_improve": _coerce_string_list(_first_present(candidate_value, ("what_to_improve", "issues"))),
        }
        notes.append("normalized:candidate_feedback")

    ai_value = _first_present(root, ("ai_assistance", "aiAssistance", "ai_usage"))
    if isinstance(ai_value, dict):
        likelihood = _coerce_probability(_first_present(ai_value, ("likelihood", "probability")))
        confidence = _coerce_probability(_first_present(ai_value, ("confidence",)))
        disclaimer = _first_present(ai_value, ("disclaimer", "note"))
        repaired["ai_assistance"] = {
            "likelihood": likelihood if likelihood is not None else 0.0,
            "confidence": confidence if confidence is not None else 0.0,
            "disclaimer": disclaimer if isinstance(disclaimer, str) else "",
        }
        notes.append("normalized:ai_assistance")

    if not repaired:
        return None, notes
    return repaired, notes


def _payload_shape(payload: dict[str, object]) -> dict[str, object]:
    keys = sorted(payload.keys())
    return {
        "keys": keys,
        "has_tasks": "tasks" in payload,
        "has_organizer_feedback": "organizer_feedback" in payload,
        "has_candidate_feedback": "candidate_feedback" in payload,
        "has_ai_assistance": "ai_assistance" in payload,
    }


def _first_present(data: dict[str, object], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _coerce_score(value: object) -> int | None:
    if isinstance(value, int):
        return value if 1 <= value <= 10 else None
    if isinstance(value, float):
        rounded = int(round(value))
        return rounded if 1 <= rounded <= 10 else None
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if 1 <= parsed <= 10 else None
    return None


def _coerce_probability(value: object) -> float | None:
    if isinstance(value, (int, float)):
        number = float(value)
        if number < 0.0:
            return 0.0
        if number > 1.0:
            return 1.0
        return number
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        if parsed < 0.0:
            return 0.0
        if parsed > 1.0:
            return 1.0
        return parsed
    return None


def _coerce_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            if isinstance(item, str):
                items.append(item)
        return items
    return []


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
