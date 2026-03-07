from __future__ import annotations

from dataclasses import dataclass
import re


_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_WEIGHT_TOLERANCE = 0.001


@dataclass(frozen=True)
class CriteriaDefinition:
    criterion_id: str
    weight: float


@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    weight: float
    criteria: tuple[CriteriaDefinition, ...]


@dataclass(frozen=True)
class AssignmentCriteriaSchema:
    schema_version: str
    tasks: tuple[TaskDefinition, ...]


def parse_assignment_criteria_schema(raw: dict[str, object]) -> AssignmentCriteriaSchema:
    schema_version = raw.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise ValueError("criteria_schema_json.schema_version is required")
    if schema_version != "task-criteria:v1":
        raise ValueError("criteria_schema_json.schema_version must be task-criteria:v1")

    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ValueError("criteria_schema_json.tasks must be a non-empty list")

    seen_task_ids: set[str] = set()
    tasks: list[TaskDefinition] = []
    task_weight_sum = 0.0
    for task_item in tasks_raw:
        if not isinstance(task_item, dict):
            raise ValueError("criteria_schema_json.tasks must contain objects")
        task_id = task_item.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("criteria_schema_json.tasks[].task_id is required")
        if not _ID_RE.match(task_id):
            raise ValueError("criteria_schema_json.tasks[].task_id must be ASCII id")
        if task_id in seen_task_ids:
            raise ValueError("criteria_schema_json.tasks[].task_id must be unique")
        seen_task_ids.add(task_id)

        task_weight = task_item.get("weight")
        if not isinstance(task_weight, (int, float)):
            raise ValueError("criteria_schema_json.tasks[].weight must be numeric")
        task_weight_float = float(task_weight)
        if task_weight_float <= 0:
            raise ValueError("criteria_schema_json.tasks[].weight must be > 0")
        task_weight_sum += task_weight_float

        criteria_raw = task_item.get("criteria")
        if not isinstance(criteria_raw, list) or not criteria_raw:
            raise ValueError("criteria_schema_json.tasks[].criteria must be a non-empty list")
        seen_criterion_ids: set[str] = set()
        criteria: list[CriteriaDefinition] = []
        criteria_weight_sum = 0.0
        for criterion_item in criteria_raw:
            if not isinstance(criterion_item, dict):
                raise ValueError("criteria_schema_json.tasks[].criteria must contain objects")
            criterion_id = criterion_item.get("criterion_id")
            if not isinstance(criterion_id, str) or not criterion_id.strip():
                raise ValueError("criteria_schema_json.tasks[].criteria[].criterion_id is required")
            if not _ID_RE.match(criterion_id):
                raise ValueError("criteria_schema_json.tasks[].criteria[].criterion_id must be ASCII id")
            if criterion_id in seen_criterion_ids:
                raise ValueError("criteria_schema_json.tasks[].criteria[].criterion_id must be unique")
            seen_criterion_ids.add(criterion_id)

            criterion_weight = criterion_item.get("weight")
            if not isinstance(criterion_weight, (int, float)):
                raise ValueError("criteria_schema_json.tasks[].criteria[].weight must be numeric")
            criterion_weight_float = float(criterion_weight)
            if criterion_weight_float <= 0:
                raise ValueError("criteria_schema_json.tasks[].criteria[].weight must be > 0")
            criteria_weight_sum += criterion_weight_float

            criteria.append(
                CriteriaDefinition(
                    criterion_id=criterion_id,
                    weight=criterion_weight_float,
                )
            )

        if abs(criteria_weight_sum - 1.0) > _WEIGHT_TOLERANCE:
            raise ValueError("criteria_schema_json.tasks[].criteria weights must sum to 1.0 +/- 0.001")

        tasks.append(
            TaskDefinition(
                task_id=task_id,
                weight=task_weight_float,
                criteria=tuple(criteria),
            )
        )

    if abs(task_weight_sum - 1.0) > _WEIGHT_TOLERANCE:
        raise ValueError("criteria_schema_json.tasks weights must sum to 1.0 +/- 0.001")

    return AssignmentCriteriaSchema(schema_version=schema_version, tasks=tuple(tasks))


def validate_assignment_criteria_schema_json(raw: dict[str, object]) -> None:
    parse_assignment_criteria_schema(raw)
