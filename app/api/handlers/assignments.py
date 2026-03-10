from __future__ import annotations

from app.api.handlers.deps import ApiDeps
from app.api.schemas import AssignmentResponse, ListAssignmentsResponse, TaskSchemaPayload
from app.domain.evaluation_contracts import TaskSchema
from app.domain.models import AssignmentSnapshot

COMPONENT_ID_CREATE = "api.create_assignment"
COMPONENT_ID_LIST = "api.list_assignments"
COMPONENT_ID_GET = "api.get_assignment"
COMPONENT_ID_UPDATE = "api.update_assignment"
COMPONENT_ID_DELETE = "api.delete_assignment"


def _assignment_response_from_snapshot(
    assignment: AssignmentSnapshot,
    *,
    include_task_schema: bool,
) -> AssignmentResponse:
    return AssignmentResponse(
        assignment_public_id=assignment.assignment_public_id,
        title=assignment.title,
        description=assignment.description,
        language=assignment.language,
        is_active=assignment.is_active,
        task_schema=(
            TaskSchemaPayload.from_domain(assignment.task_schema)
            if include_task_schema and assignment.task_schema is not None
            else None
        ),
    )


async def create_assignment_handler(
    deps: ApiDeps,
    *,
    title: str,
    description: str,
    language: str,
    task_schema: TaskSchema,
    is_active: bool,
) -> AssignmentResponse:
    assignment = await deps.repository.create_assignment(
        title=title,
        description=description,
        language=language,
        task_schema=task_schema,
        is_active=is_active,
    )
    return _assignment_response_from_snapshot(assignment, include_task_schema=True)


async def list_assignments_handler(
    deps: ApiDeps,
    *,
    active_only: bool,
    include_task_schema: bool,
) -> ListAssignmentsResponse:
    items = await deps.repository.list_assignments(active_only=active_only, include_task_schema=include_task_schema)
    return ListAssignmentsResponse(
        items=[_assignment_response_from_snapshot(item, include_task_schema=include_task_schema) for item in items]
    )


async def get_assignment_handler(
    deps: ApiDeps,
    *,
    assignment_public_id: str,
    include_task_schema: bool,
) -> AssignmentResponse | None:
    assignment = await deps.repository.get_assignment_by_public_id(
        assignment_public_id=assignment_public_id,
        include_task_schema=include_task_schema,
    )
    if assignment is None:
        return None
    return _assignment_response_from_snapshot(assignment, include_task_schema=include_task_schema)


async def update_assignment_handler(
    deps: ApiDeps,
    *,
    assignment_public_id: str,
    title: str,
    description: str,
    language: str,
    task_schema: TaskSchema,
    is_active: bool,
) -> AssignmentResponse | None:
    assignment = await deps.repository.update_assignment(
        assignment_public_id=assignment_public_id,
        title=title,
        description=description,
        language=language,
        task_schema=task_schema,
        is_active=is_active,
    )
    if assignment is None:
        return None
    return _assignment_response_from_snapshot(assignment, include_task_schema=True)


async def delete_assignment_handler(
    deps: ApiDeps,
    *,
    assignment_public_id: str,
) -> bool:
    return await deps.repository.delete_assignment(assignment_public_id=assignment_public_id)
