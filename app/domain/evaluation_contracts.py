from __future__ import annotations

from dataclasses import dataclass, replace
import re


_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_LANGUAGE_RE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
_WEIGHT_TOLERANCE = 0.001
_TASK_SCHEMA_VERSION = "task-criteria:v1"


@dataclass(frozen=True)
class TaskSchemaCriterion:
    criterion_id: str
    description: str
    weight: float

    def to_dict(self) -> dict[str, object]:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "weight": self.weight,
        }


@dataclass(frozen=True)
class TaskSchemaTask:
    task_id: str
    title: str
    weight: float
    criteria: tuple[TaskSchemaCriterion, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "weight": self.weight,
            "criteria": [criterion.to_dict() for criterion in self.criteria],
        }


@dataclass(frozen=True)
class TaskSchema:
    schema_version: str
    tasks: tuple[TaskSchemaTask, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "tasks": [task.to_dict() for task in self.tasks],
        }


@dataclass(frozen=True)
class OrganizerFeedback:
    strengths: tuple[str, ...]
    issues: tuple[str, ...]
    recommendations: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "strengths": list(self.strengths),
            "issues": list(self.issues),
            "recommendations": list(self.recommendations),
        }


@dataclass(frozen=True)
class CandidateFeedback:
    summary: str
    what_went_well: tuple[str, ...]
    what_to_improve: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "what_went_well": list(self.what_went_well),
            "what_to_improve": list(self.what_to_improve),
        }


@dataclass(frozen=True)
class CriterionScore:
    criterion_id: str
    score: int
    reason: str
    weight: float

    def to_dict(self) -> dict[str, object]:
        return {
            "criterion_id": self.criterion_id,
            "score": self.score,
            "reason": self.reason,
            "weight": self.weight,
        }


@dataclass(frozen=True)
class CriterionScoreItem:
    task_id: str
    criterion_id: str
    score: int
    reason: str
    weight: float

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "criterion_id": self.criterion_id,
            "score": self.score,
            "reason": self.reason,
            "weight": self.weight,
        }


@dataclass(frozen=True)
class TaskScoreBreakdown:
    task_id: str
    score_1_10: int
    weight: float
    criteria: tuple[CriterionScore, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "score_1_10": self.score_1_10,
            "weight": self.weight,
            "criteria": [criterion.to_dict() for criterion in self.criteria],
        }


@dataclass(frozen=True)
class ScoreBreakdown:
    schema_version: str
    tasks: tuple[TaskScoreBreakdown, ...]
    overall_score_1_10_derived: int
    reproducibility_subset: dict[str, str] | None = None
    chain_snapshot: dict[str, object] | None = None

    def with_reproducibility(self, subset: dict[str, str]) -> ScoreBreakdown:
        return replace(self, reproducibility_subset=dict(subset))

    def with_chain_snapshot(self, snapshot: dict[str, object]) -> ScoreBreakdown:
        return replace(self, chain_snapshot=dict(snapshot))

    def task_scores(self) -> dict[str, int]:
        return {task.task_id: task.score_1_10 for task in self.tasks}

    def task_weights(self) -> dict[str, float]:
        return {task.task_id: task.weight for task in self.tasks}

    def task_order(self) -> list[str]:
        return [task.task_id for task in self.tasks]

    def criterion_items(self) -> tuple[CriterionScoreItem, ...]:
        items: list[CriterionScoreItem] = []
        for task in self.tasks:
            for criterion in task.criteria:
                items.append(
                    CriterionScoreItem(
                        task_id=task.task_id,
                        criterion_id=criterion.criterion_id,
                        score=criterion.score,
                        reason=criterion.reason,
                        weight=criterion.weight,
                    )
                )
        return tuple(items)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "tasks": [task.to_dict() for task in self.tasks],
            "items": [item.to_dict() for item in self.criterion_items()],
            "task_order": self.task_order(),
            "task_scores": self.task_scores(),
            "task_weights": self.task_weights(),
            "overall_score_1_10_derived": self.overall_score_1_10_derived,
        }
        if self.reproducibility_subset is not None:
            payload["_reproducibility"] = dict(self.reproducibility_subset)
        if self.chain_snapshot is not None:
            payload["_chain_snapshot"] = dict(self.chain_snapshot)
        return payload


def parse_task_schema(raw: dict[str, object]) -> TaskSchema:
    schema_version = raw.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise ValueError("task_schema.schema_version is required")
    if schema_version != _TASK_SCHEMA_VERSION:
        raise ValueError(f"task_schema.schema_version must be {_TASK_SCHEMA_VERSION}")

    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ValueError("task_schema.tasks must be a non-empty list")

    seen_task_ids: set[str] = set()
    task_weight_sum = 0.0
    tasks: list[TaskSchemaTask] = []
    for task_item in tasks_raw:
        if not isinstance(task_item, dict):
            raise ValueError("task_schema.tasks must contain objects")
        task_id = _required_ascii_id(task_item, "task_id", "task_schema.tasks[].task_id")
        if task_id in seen_task_ids:
            raise ValueError("task_schema.tasks[].task_id must be unique")
        seen_task_ids.add(task_id)
        title = _required_string(task_item, "title", "task_schema.tasks[].title")
        task_weight = _required_weight(task_item, "weight", "task_schema.tasks[].weight")
        task_weight_sum += task_weight

        criteria_raw = task_item.get("criteria")
        if not isinstance(criteria_raw, list) or not criteria_raw:
            raise ValueError("task_schema.tasks[].criteria must be a non-empty list")

        seen_criterion_ids: set[str] = set()
        criteria_weight_sum = 0.0
        criteria: list[TaskSchemaCriterion] = []
        for criterion_item in criteria_raw:
            if not isinstance(criterion_item, dict):
                raise ValueError("task_schema.tasks[].criteria must contain objects")
            criterion_id = _required_ascii_id(
                criterion_item,
                "criterion_id",
                "task_schema.tasks[].criteria[].criterion_id",
            )
            if criterion_id in seen_criterion_ids:
                raise ValueError("task_schema.tasks[].criteria[].criterion_id must be unique")
            seen_criterion_ids.add(criterion_id)
            description = _required_string(
                criterion_item,
                "description",
                "task_schema.tasks[].criteria[].description",
            )
            criterion_weight = _required_weight(
                criterion_item,
                "weight",
                "task_schema.tasks[].criteria[].weight",
            )
            criteria_weight_sum += criterion_weight
            criteria.append(
                TaskSchemaCriterion(
                    criterion_id=criterion_id,
                    description=description,
                    weight=criterion_weight,
                )
            )

        if abs(criteria_weight_sum - 1.0) > _WEIGHT_TOLERANCE:
            raise ValueError("task_schema.tasks[].criteria weights must sum to 1.0 +/- 0.001")

        tasks.append(
            TaskSchemaTask(
                task_id=task_id,
                title=title,
                weight=task_weight,
                criteria=tuple(criteria),
            )
        )

    if abs(task_weight_sum - 1.0) > _WEIGHT_TOLERANCE:
        raise ValueError("task_schema.tasks weights must sum to 1.0 +/- 0.001")

    return TaskSchema(schema_version=schema_version, tasks=tuple(tasks))


def validate_task_schema_json(raw: dict[str, object]) -> None:
    parse_task_schema(raw)


def validate_language_code(value: str, *, field_name: str = "language") -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    if not _LANGUAGE_RE.match(value):
        raise ValueError(f"{field_name} must be ISO code, e.g. 'ru' or 'en'")


def parse_organizer_feedback(raw: dict[str, object]) -> OrganizerFeedback:
    return OrganizerFeedback(
        strengths=_parse_string_list(raw, "strengths", "organizer_feedback.strengths"),
        issues=_parse_string_list(raw, "issues", "organizer_feedback.issues"),
        recommendations=_parse_string_list(raw, "recommendations", "organizer_feedback.recommendations"),
    )


def parse_candidate_feedback(raw: dict[str, object]) -> CandidateFeedback:
    return CandidateFeedback(
        summary=_required_string_allow_empty(raw, "summary", "candidate_feedback.summary"),
        what_went_well=_parse_string_list(
            raw,
            "what_went_well",
            "candidate_feedback.what_went_well",
        ),
        what_to_improve=_parse_string_list(
            raw,
            "what_to_improve",
            "candidate_feedback.what_to_improve",
        ),
    )


def parse_score_breakdown(raw: dict[str, object]) -> ScoreBreakdown:
    schema_version = _required_string(raw, "schema_version", "score_breakdown.schema_version")
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ValueError("score_breakdown.tasks must be a non-empty list")

    tasks: list[TaskScoreBreakdown] = []
    seen_task_ids: set[str] = set()
    for task_item in tasks_raw:
        if not isinstance(task_item, dict):
            raise ValueError("score_breakdown.tasks must contain objects")
        task_id = _required_ascii_id(task_item, "task_id", "score_breakdown.tasks[].task_id")
        if task_id in seen_task_ids:
            raise ValueError("score_breakdown.tasks[].task_id must be unique")
        seen_task_ids.add(task_id)
        score_1_10 = _required_score(task_item, "score_1_10", "score_breakdown.tasks[].score_1_10")
        weight = _required_weight(task_item, "weight", "score_breakdown.tasks[].weight")
        criteria_raw = task_item.get("criteria")
        if not isinstance(criteria_raw, list) or not criteria_raw:
            raise ValueError("score_breakdown.tasks[].criteria must be a non-empty list")
        seen_criterion_ids: set[str] = set()
        criteria: list[CriterionScore] = []
        for criterion_item in criteria_raw:
            if not isinstance(criterion_item, dict):
                raise ValueError("score_breakdown.tasks[].criteria must contain objects")
            criterion_id = _required_ascii_id(
                criterion_item,
                "criterion_id",
                "score_breakdown.tasks[].criteria[].criterion_id",
            )
            if criterion_id in seen_criterion_ids:
                raise ValueError("score_breakdown.tasks[].criteria[].criterion_id must be unique")
            seen_criterion_ids.add(criterion_id)
            criteria.append(
                CriterionScore(
                    criterion_id=criterion_id,
                    score=_required_score(
                        criterion_item,
                        "score",
                        "score_breakdown.tasks[].criteria[].score",
                    ),
                    reason=_required_string(
                        criterion_item,
                        "reason",
                        "score_breakdown.tasks[].criteria[].reason",
                    ),
                    weight=_required_weight(
                        criterion_item,
                        "weight",
                        "score_breakdown.tasks[].criteria[].weight",
                    ),
                )
            )
        tasks.append(
            TaskScoreBreakdown(
                task_id=task_id,
                score_1_10=score_1_10,
                weight=weight,
                criteria=tuple(criteria),
            )
        )

    overall_score = _required_score(
        raw,
        "overall_score_1_10_derived",
        "score_breakdown.overall_score_1_10_derived",
    )
    reproducibility_raw = raw.get("_reproducibility")
    reproducibility_subset: dict[str, str] | None = None
    if reproducibility_raw is not None:
        if not isinstance(reproducibility_raw, dict):
            raise ValueError("score_breakdown._reproducibility must be object")
        reproducibility_subset = {
            str(key): _required_string(reproducibility_raw, str(key), f"score_breakdown._reproducibility.{key}")
            for key in reproducibility_raw.keys()
        }

    chain_snapshot_raw = raw.get("_chain_snapshot")
    chain_snapshot: dict[str, object] | None = None
    if chain_snapshot_raw is not None:
        if not isinstance(chain_snapshot_raw, dict):
            raise ValueError("score_breakdown._chain_snapshot must be object")
        chain_snapshot = dict(chain_snapshot_raw)

    return ScoreBreakdown(
        schema_version=schema_version,
        tasks=tuple(tasks),
        overall_score_1_10_derived=overall_score,
        reproducibility_subset=reproducibility_subset,
        chain_snapshot=chain_snapshot,
    )


def _required_string(data: dict[str, object], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} is required")
    return value


def _required_string_allow_empty(data: dict[str, object], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{path} is required")
    return value


def _required_ascii_id(data: dict[str, object], key: str, path: str) -> str:
    value = _required_string(data, key, path)
    if not _ID_RE.match(value):
        raise ValueError(f"{path} must be ASCII id")
    return value


def _required_weight(data: dict[str, object], key: str, path: str) -> float:
    value = data.get(key)
    if not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be numeric")
    weight = float(value)
    if weight <= 0:
        raise ValueError(f"{path} must be > 0")
    return weight


def _required_score(data: dict[str, object], key: str, path: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise ValueError(f"{path} must be integer")
    if value < 1 or value > 10:
        raise ValueError(f"{path} must be between 1 and 10")
    return value


def _parse_string_list(data: dict[str, object], key: str, path: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{path} must contain strings")
        items.append(item)
    return tuple(items)
