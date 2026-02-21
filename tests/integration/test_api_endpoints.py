from fastapi.testclient import TestClient
import pytest

from app.api.http_app import build_app
from app.roles import validate_role
from app.services.bootstrap import build_runtime_container


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
        create_response = client.post("/submissions", json={"source_external_id": "demo"})
        status_response = client.get("/submissions/sub-api_upload-demo")
        feedback_response = client.get("/feedback", params={"submission_id": "demo"})
        export_response = client.post(
            "/exports",
            json={"submission_id": "demo", "feedback_ref": "feedback/demo.json"},
        )

    assert create_response.status_code == 200
    assert create_response.json()["submission_id"] == "sub-api_upload-demo"
    assert status_response.status_code == 200
    assert status_response.json()["state"] == "skeleton"
    assert feedback_response.status_code == 200
    assert feedback_response.json()["items"] == []
    assert export_response.status_code == 200
    assert export_response.json()["export_ref"].startswith("stub://exports/")
