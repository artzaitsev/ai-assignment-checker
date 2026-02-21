import pytest
from fastapi.testclient import TestClient

from app.api.http_app import build_app
from app.roles import validate_role
from app.services.bootstrap import build_runtime_container


@pytest.mark.integration
def test_file_upload_and_synthetic_pipeline_end_to_end() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-synthetic-e2e",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        upload_response = client.post(
            "/submissions/file",
            files={"file": ("task.txt", b"print('hello')", "text/plain")},
        )
        assert upload_response.status_code == 200

        submission_id = upload_response.json()["submission_id"]
        assert upload_response.json()["state"] == "uploaded"
        assert upload_response.json()["artifacts"]["raw"].startswith("stub://raw/")

        pipeline_response = client.post(
            "/internal/test/run-pipeline",
            json={"submission_id": submission_id},
        )
        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["state"] == "delivered"
        assert payload["artifacts"]["normalized"].startswith("normalized/")
        assert payload["artifacts"]["llm-output"].startswith("stub://llm-output/")
        assert payload["artifacts"]["feedback"].startswith("stub://feedback/")
        assert payload["artifacts"]["exports"].startswith("stub://exports/")

        status_response = client.get(f"/submissions/{submission_id}")
        assert status_response.status_code == 200
        status_payload = status_response.json()
        assert status_payload["state"] == "delivered"
        assert status_payload["transitions"] == [
            "uploaded",
            "normalization_in_progress",
            "normalized",
            "evaluation_in_progress",
            "evaluated",
            "delivery_in_progress",
            "delivered",
        ]
