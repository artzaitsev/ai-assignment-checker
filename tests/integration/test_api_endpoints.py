from fastapi.testclient import TestClient
import pytest

from app.api.http_app import build_app
from app.roles import validate_role
from app.services.bootstrap import build_runtime_container
from tests.integration.api_seed import seed_candidate_and_assignment


@pytest.mark.integration
def test_skeleton_api_endpoints_are_available() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-api",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        candidate_public_id, assignment_public_id = seed_candidate_and_assignment(client=client)

        create_response = client.post(
            "/submissions",
            json={
                "source_external_id": "demo",
                "candidate_public_id": candidate_public_id,
                "assignment_public_id": assignment_public_id,
            },
        )
        created_submission_id = create_response.json()["submission_id"]
        status_response = client.get(f"/submissions/{created_submission_id}")
        assignments_response = client.get("/assignments")
        feedback_response = client.get("/feedback", params={"submission_id": "demo"})
        export_response = client.post(
            "/exports",
            json={"submission_id": "demo", "feedback_ref": "feedback/demo.json"},
        )

    assert create_response.status_code == 200
    assert create_response.json()["submission_id"].startswith("sub_")
    assert status_response.status_code == 200
    assert status_response.json()["state"] == "uploaded"
    assert status_response.json()["candidate_public_id"] == candidate_public_id
    assert status_response.json()["assignment_public_id"] == assignment_public_id
    assert assignments_response.status_code == 200
    assert len(assignments_response.json()["items"]) >= 1
    assert feedback_response.status_code == 200
    assert feedback_response.json()["items"] == []
    assert export_response.status_code == 200
    assert export_response.json()["export_ref"].startswith("stub://exports/")
