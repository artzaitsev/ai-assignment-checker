from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.http_app import build_app
from app.roles import validate_role
from app.services.bootstrap import build_runtime_container
from app.workers.handlers.deps import WorkerDeps
from app.workers.handlers.normalize import process_claim as normalize_process_claim
from app.workers.loop import WorkerLoop
from tests.integration.api_seed import seed_candidate_and_assignment


@pytest.mark.integration
def test_upload_then_normalize_worker_then_status_has_normalized_artifact() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-normalize-worker",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        candidate_public_id, assignment_public_id = seed_candidate_and_assignment(client=client)
        upload_response = client.post(
            "/submissions/file",
            files={
                "file": ("answer.txt", b"hello normalize", "text/plain"),
                "candidate_public_id": (None, candidate_public_id),
                "assignment_public_id": (None, assignment_public_id),
            },
        )
        assert upload_response.status_code == 200
        submission_id = upload_response.json()["submission_id"]

        deps = WorkerDeps(
            repository=container.repository,
            storage=container.storage,
            telegram=container.telegram,
            llm=container.llm,
        )
        loop = WorkerLoop(
            role="worker-normalize",
            stage="normalized",
            repository=container.repository,
            process=lambda claim: normalize_process_claim(claim, deps),
        )
        did_work = asyncio.run(loop.run_once())
        assert did_work is True

        status_response = client.get(f"/submissions/{submission_id}")
        assert status_response.status_code == 200
        payload = status_response.json()
        assert payload["state"] == "normalized"
        assert payload["artifacts"]["normalized"].startswith("stub://normalized/")

