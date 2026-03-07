from __future__ import annotations

from fastapi.testclient import TestClient


def default_criteria_schema() -> dict[str, object]:
    return {
        "schema_version": "task-criteria:v1",
        "tasks": [
            {
                "task_id": "task_main",
                "title": "Main task",
                "weight": 1.0,
                "criteria": [
                    {
                        "criterion_id": "correctness",
                        "description": "Core correctness",
                        "weight": 0.5,
                    },
                    {
                        "criterion_id": "completeness",
                        "description": "Coverage of requirements",
                        "weight": 0.5,
                    },
                ],
            }
        ],
    }


def seed_candidate_and_assignment(*, client: TestClient) -> tuple[str, str]:
    return seed_candidate_and_assignment_with_source(client=client)


def seed_candidate_and_assignment_with_source(
    *,
    client: TestClient,
    source_type: str | None = None,
    source_external_id: str | None = None,
) -> tuple[str, str]:
    candidate_payload: dict[str, str] = {"first_name": "Seed", "last_name": "Candidate"}
    if source_type is not None and source_external_id is not None:
        candidate_payload["source_type"] = source_type
        candidate_payload["source_external_id"] = source_external_id
    candidate_response = client.post(
        "/candidates",
        json=candidate_payload,
    )
    assert candidate_response.status_code == 200
    candidate_public_id = candidate_response.json()["candidate_public_id"]

    assignment_response = client.post(
        "/assignments",
        json={
            "title": "Seed Assignment",
            "description": "Seed payload",
            "criteria_schema_json": default_criteria_schema(),
        },
    )
    assert assignment_response.status_code == 200
    assignment_public_id = assignment_response.json()["assignment_public_id"]

    return candidate_public_id, assignment_public_id
