"""Synthetic HTTP-level e2e tests for the internal test pipeline endpoint.

How to run only this file:
- `pytest -q tests/integration/test_synthetic_e2e_pipeline.py`

What these tests validate:
- happy path: uploaded submission reaches `delivered`
- failure path: pipeline stops on evaluate error with `failed_evaluation`
"""

import pytest
from fastapi.testclient import TestClient

from app.api.handlers import pipeline as pipeline_handler
from app.api.http_app import build_app
from app.domain.models import ProcessResult
from app.roles import validate_role
from app.services.bootstrap import build_runtime_container
from tests.integration.api_seed import seed_candidate_and_assignment


@pytest.mark.integration
def test_file_upload_and_synthetic_pipeline_end_to_end() -> None:
    # Build real API app + runtime container used by integration tests.
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-synthetic-e2e",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        # Seed required dictionary entities before creating submission.
        candidate_public_id, assignment_public_id = seed_candidate_and_assignment(client=client)

        # Upload file submission (creates `uploaded` state and raw artifact link).
        upload_response = client.post(
            "/submissions/file",
            files={
                "file": ("task.txt", b"print('hello')", "text/plain"),
                "candidate_public_id": (None, candidate_public_id),
                "assignment_public_id": (None, assignment_public_id),
            },
        )
        assert upload_response.status_code == 200

        submission_id = upload_response.json()["submission_id"]
        assert upload_response.json()["state"] == "uploaded"
        assert upload_response.json()["artifacts"]["raw"].startswith("s3://raw/")

        # Trigger synchronous synthetic pipeline endpoint.
        pipeline_response = client.post(
            "/internal/test/run-pipeline",
            json={"submission_id": submission_id},
        )
        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["state"] == "delivered"
        assert payload["artifacts"]["normalized"].startswith("normalized/")
        assert "llm-output" not in payload["artifacts"]
        assert "feedback" not in payload["artifacts"]
        assert "exports" not in payload["artifacts"]

        # Verify that persisted in-memory trace exposes full transition chain.
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


@pytest.mark.integration
def test_pipeline_stops_when_evaluation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Use same app wiring as happy-path test.
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-synthetic-e2e-failure",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    # Inject evaluate failure to verify fail-fast behavior.
    async def _failing_evaluate(*args: object, **kwargs: object) -> ProcessResult:
        return ProcessResult(success=False, detail="forced evaluation failure")

    monkeypatch.setattr(pipeline_handler, "evaluate_process_claim", _failing_evaluate)

    with TestClient(app) as client:
        # Prepare one uploaded submission that can be processed by pipeline.
        candidate_public_id, assignment_public_id = seed_candidate_and_assignment(client=client)

        upload_response = client.post(
            "/submissions/file",
            files={
                "file": ("task.txt", b"print('hello')", "text/plain"),
                "candidate_public_id": (None, candidate_public_id),
                "assignment_public_id": (None, assignment_public_id),
            },
        )
        assert upload_response.status_code == 200

        # Pipeline must stop at evaluation and never reach delivery stage.
        submission_id = upload_response.json()["submission_id"]
        pipeline_response = client.post(
            "/internal/test/run-pipeline",
            json={"submission_id": submission_id},
        )
        assert pipeline_response.status_code == 200
        payload = pipeline_response.json()
        assert payload["state"] == "failed_evaluation"
        assert payload["transitions"] == [
            "uploaded",
            "normalization_in_progress",
            "normalized",
            "evaluation_in_progress",
            "failed_evaluation",
        ]

        # Status endpoint should expose the same terminal failure state.
        status_response = client.get(f"/submissions/{submission_id}")
        assert status_response.status_code == 200
        assert status_response.json()["state"] == "failed_evaluation"
