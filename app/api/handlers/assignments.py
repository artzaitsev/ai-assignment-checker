from __future__ import annotations

from app.api.handlers.deps import ApiDeps
from app.api.schemas import AssignmentResponse, ListAssignmentsResponse

COMPONENT_ID_CREATE = "api.create_assignment"
COMPONENT_ID_LIST = "api.list_assignments"


async def create_assignment_handler(
    *,
    title: str,
    description: str,
    is_active: bool,
    api_deps: ApiDeps,
) -> AssignmentResponse:
    assignment = await api_deps.repository.create_assignment(
        title=title,
        description=description,
        is_active=is_active,
    )
    return AssignmentResponse(
        assignment_public_id=assignment.assignment_public_id,
        title=assignment.title,
        description=assignment.description,
        is_active=assignment.is_active,
    )


async def list_assignments_handler(*, active_only: bool, api_deps: ApiDeps) -> ListAssignmentsResponse:
    items = await api_deps.repository.list_assignments(active_only=active_only)
    return ListAssignmentsResponse(
        items=[
            AssignmentResponse(
                assignment_public_id=item.assignment_public_id,
                title=item.title,
                description=item.description,
                is_active=item.is_active,
            )
            for item in items
        ]
    )
