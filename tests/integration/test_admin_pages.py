from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
import pytest

from app.api.http_app import build_app
from app.domain.evaluation_contracts import CandidateFeedback, OrganizerFeedback, ScoreBreakdown, TaskScoreBreakdown, CriterionScore
from app.roles import validate_role
from app.services.bootstrap import build_runtime_container
from tests.integration.api_seed import seed_candidate_and_assignment


@pytest.mark.integration
def test_candidate_result_page_shows_feedback_after_evaluation() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-candidate-result-page",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        candidate_public_id, assignment_public_id = seed_candidate_and_assignment(client=client)
        create_response = client.post(
            "/submissions",
            json={
                "source_external_id": "candidate-result-1",
                "candidate_public_id": candidate_public_id,
                "assignment_public_id": assignment_public_id,
            },
        )
        submission_id = create_response.json()["submission_id"]

        asyncio.run(
            container.repository.persist_evaluation(
                submission_id=submission_id,
                score_1_10=9,
                score_breakdown=ScoreBreakdown(
                    schema_version="task-criteria:v1",
                    tasks=(
                        TaskScoreBreakdown(
                            task_id="task_main",
                            score_1_10=9,
                            weight=1.0,
                            criteria=(
                                CriterionScore(
                                    criterion_id="correctness",
                                    score=9,
                                    reason="good",
                                    weight=1.0,
                                ),
                            ),
                        ),
                    ),
                    overall_score_1_10_derived=9,
                ),
                organizer_feedback=OrganizerFeedback(strengths=("Clear",), issues=(), recommendations=()),
                candidate_feedback=CandidateFeedback(
                    summary="Отличная работа",
                    what_went_well=("Структура",),
                    what_to_improve=("Покрыть edge cases",),
                ),
                ai_assistance_likelihood=0.2,
                ai_assistance_confidence=0.8,
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
                item_id=submission_id,
                from_state="uploaded",
                to_state="normalization_in_progress",
            )
        )
        asyncio.run(
            container.repository.transition_state(
                item_id=submission_id,
                from_state="normalization_in_progress",
                to_state="normalized",
            )
        )
        asyncio.run(
            container.repository.transition_state(
                item_id=submission_id,
                from_state="normalized",
                to_state="evaluation_in_progress",
            )
        )
        asyncio.run(
            container.repository.transition_state(
                item_id=submission_id,
                from_state="evaluation_in_progress",
                to_state="evaluated",
            )
        )

        page_response = client.get(f"/candidate/apply/result/{submission_id}")
        panel_response = client.get(f"/candidate/apply/result/{submission_id}/panel")

    assert page_response.status_code == 200
    assert "Результат проверки" in page_response.text
    assert panel_response.status_code == 200
    assert "Проверка завершена" in panel_response.text
    assert "Отличная работа" in panel_response.text


@pytest.mark.integration
def test_admin_pages_list_detail_and_export_flow() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-admin-pages",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        candidate_public_id, assignment_public_id = seed_candidate_and_assignment(client=client)
        create_response = client.post(
            "/submissions",
            json={
                "source_external_id": "admin-page-1",
                "candidate_public_id": candidate_public_id,
                "assignment_public_id": assignment_public_id,
            },
        )
        submission_id = create_response.json()["submission_id"]
        second_submission_response = client.post(
            "/submissions",
            json={
                "source_external_id": "admin-page-2",
                "candidate_public_id": candidate_public_id,
                "assignment_public_id": assignment_public_id,
            },
        )
        second_submission_id = second_submission_response.json()["submission_id"]

        asyncio.run(
            container.repository.persist_llm_run(
                submission_id=submission_id,
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
                submission_id=submission_id,
                score_1_10=8,
                score_breakdown=ScoreBreakdown(
                    schema_version="task-criteria:v1",
                    tasks=(
                        TaskScoreBreakdown(
                            task_id="task_main",
                            score_1_10=8,
                            weight=1.0,
                            criteria=(
                                CriterionScore(
                                    criterion_id="correctness",
                                    score=8,
                                    reason="good",
                                    weight=1.0,
                                ),
                            ),
                        ),
                    ),
                    overall_score_1_10_derived=8,
                ),
                organizer_feedback=OrganizerFeedback(
                    strengths=("Clear structure",),
                    issues=("Edge cases",),
                    recommendations=("Add coverage",),
                ),
                candidate_feedback=CandidateFeedback(
                    summary="Good baseline",
                    what_went_well=("Core logic",),
                    what_to_improve=("Edge handling",),
                ),
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
                item_id=submission_id,
                from_state="uploaded",
                to_state="normalization_in_progress",
            )
        )
        asyncio.run(
            container.repository.transition_state(
                item_id=submission_id,
                from_state="normalization_in_progress",
                to_state="normalized",
            )
        )
        asyncio.run(
            container.repository.transition_state(
                item_id=submission_id,
                from_state="normalized",
                to_state="evaluation_in_progress",
            )
        )
        asyncio.run(
            container.repository.transition_state(
                item_id=submission_id,
                from_state="evaluation_in_progress",
                to_state="evaluated",
            )
        )

        page_response = client.get("/admin/submissions")
        table_response = client.get("/admin/submissions/table", params={"status": "evaluated"})
        detail_response = client.get(f"/admin/submissions/{submission_id}")
        export_response = client.post(
            "/admin/submissions/export",
            data={
                "status": "evaluated",
                "sort_by": "created_at",
                "sort_order": "desc",
                "limit": "100",
                "offset": "0",
            },
        )

    assert page_response.status_code == 200
    assert "Review Submissions" in page_response.text
    assert submission_id in page_response.text
    assert table_response.status_code == 200
    assert submission_id in table_response.text
    assert second_submission_id not in table_response.text
    assert detail_response.status_code == 200
    assert "AI assistance" in detail_response.text
    assert "Likelihood" in detail_response.text
    assert export_response.status_code == 200
    assert "Download" in export_response.text
    assert "/exports/" in export_response.text
