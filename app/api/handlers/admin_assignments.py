from __future__ import annotations

from dataclasses import dataclass
import json

from app.api.handlers.assignments import create_assignment_handler, update_assignment_handler
from app.api.handlers.deps import ApiDeps
from app.api.schemas import AssignmentResponse, CreateAssignmentRequest, TaskSchemaPayload
from app.domain.models import AssignmentSnapshot


DEFAULT_TASK_SCHEMA_RAW: dict[str, object] = {
    "schema_version": "task-criteria:v1",
    "tasks": [
        {
            "task_id": "task_1",
            "title": "Задание 1",
            "weight": 1.0,
            "criteria": [
                {
                    "criterion_id": "criterion_1",
                    "description": "Критерий",
                    "weight": 1.0,
                }
            ],
        }
    ],
}


@dataclass(frozen=True)
class AdminAssignmentListItem:
    assignment_public_id: str
    title: str
    description_preview: str
    language: str
    is_active: bool
    tasks_count: int
    criteria_count: int
    candidate_apply_url: str


def default_task_schema_json() -> str:
    return json.dumps(DEFAULT_TASK_SCHEMA_RAW, ensure_ascii=False)


def build_candidate_assignment_apply_link(*, public_base_url: str, assignment_public_id: str) -> str:
    base = public_base_url.rstrip("/")
    return f"{base}/candidate/assignments/{assignment_public_id}/apply"


def build_assignment_template_download_link(*, assignment_public_id: str) -> str:
    return f"/candidate/assignments/{assignment_public_id}/template.docx"


def describe_task_schema_counts(assignment: AssignmentSnapshot) -> tuple[int, int]:
    if assignment.task_schema is None:
        return 0, 0
    task_count = len(assignment.task_schema.tasks)
    criteria_count = sum(len(task.criteria) for task in assignment.task_schema.tasks)
    return task_count, criteria_count


def description_preview(text: str, *, max_chars: int = 160) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 1].rstrip()}..."


def parse_admin_assignment_form(
    *,
    title: str,
    description: str,
    language: str,
    is_active: bool,
    task_schema_json: str,
) -> CreateAssignmentRequest:
    title_value = title.strip()
    description_value = description.strip()
    language_value = language.strip()
    if not title_value:
        raise ValueError("title is required")
    if not description_value:
        raise ValueError("description is required")
    if not language_value:
        raise ValueError("language is required")

    try:
        decoded = json.loads(task_schema_json)
    except json.JSONDecodeError as exc:
        raise ValueError("task schema JSON is invalid") from exc
    if not isinstance(decoded, dict):
        raise ValueError("task schema payload must be an object")

    decoded = _populate_schema_ids(decoded)

    task_schema_payload = TaskSchemaPayload.model_validate(decoded)
    return CreateAssignmentRequest(
        title=title_value,
        description=description_value,
        language=language_value,
        task_schema=task_schema_payload,
        is_active=is_active,
    )


def _populate_schema_ids(payload: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    tasks_obj = normalized.get("tasks")
    if not isinstance(tasks_obj, list):
        return normalized

    normalized_tasks: list[dict[str, object]] = []
    for task_index, task_obj in enumerate(tasks_obj, start=1):
        if not isinstance(task_obj, dict):
            continue
        normalized_task = dict(task_obj)
        task_id = normalized_task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            normalized_task["task_id"] = f"task_{task_index}"

        criteria_obj = normalized_task.get("criteria")
        if isinstance(criteria_obj, list):
            normalized_criteria: list[dict[str, object]] = []
            for criterion_index, criterion_obj in enumerate(criteria_obj, start=1):
                if not isinstance(criterion_obj, dict):
                    continue
                normalized_criterion = dict(criterion_obj)
                criterion_id = normalized_criterion.get("criterion_id")
                if not isinstance(criterion_id, str) or not criterion_id.strip():
                    normalized_criterion["criterion_id"] = f"criterion_{criterion_index}"
                normalized_criteria.append(normalized_criterion)
            normalized_task["criteria"] = normalized_criteria

        normalized_tasks.append(normalized_task)

    normalized["tasks"] = normalized_tasks
    return normalized


async def list_admin_assignments_handler(
    deps: ApiDeps,
    *,
    public_base_url: str,
) -> list[AdminAssignmentListItem]:
    assignments = await deps.repository.list_assignments(active_only=False, include_task_schema=True)
    items: list[AdminAssignmentListItem] = []
    for assignment in assignments:
        tasks_count, criteria_count = describe_task_schema_counts(assignment)
        items.append(
            AdminAssignmentListItem(
                assignment_public_id=assignment.assignment_public_id,
                title=assignment.title,
                description_preview=description_preview(assignment.description),
                language=assignment.language,
                is_active=assignment.is_active,
                tasks_count=tasks_count,
                criteria_count=criteria_count,
                candidate_apply_url=build_candidate_assignment_apply_link(
                    public_base_url=public_base_url,
                    assignment_public_id=assignment.assignment_public_id,
                ),
            )
        )
    return items


async def create_admin_assignment_handler(
    deps: ApiDeps,
    *,
    payload: CreateAssignmentRequest,
) -> AssignmentResponse:
    return await create_assignment_handler(
        deps,
        title=payload.title,
        description=payload.description,
        language=payload.language,
        task_schema=payload.task_schema.to_domain(),
        is_active=payload.is_active,
    )


async def update_admin_assignment_handler(
    deps: ApiDeps,
    *,
    assignment_public_id: str,
    payload: CreateAssignmentRequest,
) -> AssignmentResponse | None:
    return await update_assignment_handler(
        deps,
        assignment_public_id=assignment_public_id,
        title=payload.title,
        description=payload.description,
        language=payload.language,
        task_schema=payload.task_schema.to_domain(),
        is_active=payload.is_active,
    )
