from __future__ import annotations

from fastapi.testclient import TestClient


def seed_candidate_and_assignment(*, client: TestClient) -> tuple[str, str]:
    candidate_response = client.post(
        "/candidates",
        json={"first_name": "Seed", "last_name": "Candidate"},
    )
    assert candidate_response.status_code == 200
    candidate_public_id = candidate_response.json()["candidate_public_id"]

    assignment_response = client.post(
        "/assignments",
        json={"title": "Seed Assignment", "description": "Seed payload"},
    )
    assert assignment_response.status_code == 200
    assignment_public_id = assignment_response.json()["assignment_public_id"]

    return candidate_public_id, assignment_public_id
