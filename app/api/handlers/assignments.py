from __future__ import annotations

from app.api.handlers.deps import ApiDeps
from app.api.schemas import AssignmentResponse, ListAssignmentsResponse

COMPONENT_ID_CREATE = "api.create_assignment"
COMPONENT_ID_LIST = "api.list_assignments"


async def create_assignment_handler(
    deps: ApiDeps,
    *,
    title: str,
    description: str,
    criteria_schema_json: dict[str, object],
    is_active: bool,
) -> AssignmentResponse:
    assignment = await deps.repository.create_assignment(
        title=title,
        description=description,
        criteria_schema_json=criteria_schema_json,
        is_active=is_active,
    )
    return AssignmentResponse(
        assignment_public_id=assignment.assignment_public_id,
        title=assignment.title,
        description=assignment.description,
        is_active=assignment.is_active,
        criteria_schema_json=assignment.criteria_schema_json,
    )


async def list_assignments_handler(
    deps: ApiDeps,
    *,
    active_only: bool,
    include_criteria: bool,
) -> ListAssignmentsResponse:
    items = await deps.repository.list_assignments(active_only=active_only, include_criteria=include_criteria)
    return ListAssignmentsResponse(
        items=[
            AssignmentResponse(
                assignment_public_id=item.assignment_public_id,
                title=item.title,
                description=item.description,
                is_active=item.is_active,
                criteria_schema_json=item.criteria_schema_json if include_criteria else None,
            )
            for item in items
        ]
    )
