import asyncio

from fastapi.testclient import TestClient
import pytest

from app.clients.stub import StubTelegramClient
from app.api.http_app import build_app
from app.roles import validate_role
from app.services.bootstrap import build_runtime_container
from app.domain.models import WorkItemClaim
from app.workers.handlers import ingest_telegram
from app.workers.handlers.deps import WorkerDeps
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
        unevaluated_response = client.post(
            "/submissions",
            json={
                "source_external_id": "demo-unevaluated",
                "candidate_public_id": candidate_public_id,
                "assignment_public_id": assignment_public_id,
            },
        )
        assert unevaluated_response.status_code == 200
        asyncio.run(
            container.repository.persist_llm_run(
                submission_id=created_submission_id,
                provider="openai-compatible",
                model="model:v1",
                api_base="https://example.invalid",
                chain_version="chain:v1",
                spec_version="chain-spec:v1",
                response_language="ru",
                temperature=0.1,
                seed=42,
                tokens_input=128,
                tokens_output=256,
                latency_ms=120,
            )
        )
        asyncio.run(
            container.repository.persist_evaluation(
                submission_id=created_submission_id,
                score_1_10=8,
                criteria_scores_json={"items": [{"id": "correctness", "score": 8}]},
                organizer_feedback_json={
                    "strengths": ["Clear structure"],
                    "issues": ["Edge cases"],
                    "recommendations": ["Add coverage"],
                },
                candidate_feedback_json={
                    "summary": "Good baseline",
                    "what_went_well": ["Core logic"],
                    "what_to_improve": ["Edge handling"],
                },
                ai_assistance_likelihood=0.35,
                ai_assistance_confidence=0.55,
                reproducibility_subset={
                    "chain_version": "chain:v1",
                    "spec_version": "chain-spec:v1",
                    "model": "model:v1",
                    "response_language": "ru",
                },
            )
        )
        asyncio.run(
            container.repository.transition_state(
                item_id=created_submission_id,
                from_state="uploaded",
                to_state="normalization_in_progress",
            )
        )
        asyncio.run(
            container.repository.transition_state(
                item_id=created_submission_id,
                from_state="normalization_in_progress",
                to_state="normalized",
            )
        )
        asyncio.run(
            container.repository.transition_state(
                item_id=created_submission_id,
                from_state="normalized",
                to_state="evaluation_in_progress",
            )
        )
        asyncio.run(
            container.repository.transition_state(
                item_id=created_submission_id,
                from_state="evaluation_in_progress",
                to_state="evaluated",
            )
        )
        status_response = client.get(f"/submissions/{created_submission_id}")
        assignments_response = client.get("/assignments")
        feedback_response = client.get("/feedback", params={"submission_id": "demo"})
        export_response = client.post(
            "/exports",
            json={"statuses": ["evaluated"], "limit": 50, "offset": 0},
        )
        export_payload = export_response.json()
        download_response = client.get(export_payload["download_url"])

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
    assert export_payload["rows_count"] == 1
    assert export_payload["export_id"].startswith("exp_")
    assert export_payload["download_url"] == f"/exports/{export_payload['export_id']}/download"
    assert export_payload["export_ref"].startswith("s3://exports/")
    assert download_response.status_code == 200
    assert download_response.headers["content-type"].startswith("text/csv")


@pytest.mark.integration
def test_telegram_polling_ingest_path_is_idempotent_and_links_raw() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-api-telegram-poll",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        candidate_public_id, assignment_public_id = seed_candidate_and_assignment(client=client)

        assert isinstance(container.telegram, StubTelegramClient)
        container.telegram.updates.append(
            {
                "update_id": "upd-42",
                "candidate_public_id": candidate_public_id,
                "assignment_public_id": assignment_public_id,
                "file_id": "tg-file-42",
                "file_name": "task.py",
            }
        )
        container.telegram.files["tg-file-42"] = b"print('telegram')"

        deps = WorkerDeps(
            repository=container.repository,
            artifact_repository=container.artifact_repository,
            storage=container.storage,
            telegram=container.telegram,
            llm=container.llm,
        )
        first = asyncio.run(
            ingest_telegram.process_claim(
                deps,
                claim=WorkItemClaim(item_id="poll-tick-1", stage="raw", attempt=1),
            )
        )
        second = asyncio.run(
            ingest_telegram.process_claim(
                deps,
                claim=WorkItemClaim(item_id="poll-tick-2", stage="raw", attempt=1),
            )
        )
        assert first.success is True
        assert "processed 1 telegram updates" in first.detail
        assert second.success is True
        assert "processed 0 telegram updates" in second.detail

        source = asyncio.run(
            container.repository.find_submission_source(
                source_type="telegram",
                source_external_id="upd-42",
            )
        )
        assert source is not None
        snapshot = asyncio.run(container.repository.get_submission(submission_id=source.submission_id))
        assert snapshot is not None
        assert snapshot.status == "uploaded"
        raw_ref = asyncio.run(container.repository.get_artifact_ref(item_id=source.submission_id, stage="raw"))
        assert raw_ref.startswith("s3://raw/")
