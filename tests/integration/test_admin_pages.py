from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
import pytest

from app.api.http_app import build_app
from app.domain.evaluation_contracts import CandidateFeedback, OrganizerFeedback, ScoreBreakdown, TaskScoreBreakdown, CriterionScore
from app.domain.telegram_settings import TELEGRAM_DEFAULT_ASSIGNMENT_STREAM
from app.roles import validate_role
from app.services.bootstrap import build_runtime_container
from tests.integration.api_seed import default_task_schema, seed_candidate_and_assignment


@pytest.mark.integration
def test_root_redirects_to_admin_login_and_login_redirects_to_admin_assignments() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-admin-login",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        root_response = client.get("/", follow_redirects=False)
        assert root_response.status_code == 307
        assert root_response.headers.get("location") == "/admin/login"

        login_page = client.get("/admin/login")
        assert login_page.status_code == 200
        assert "Фиктивная форма входа" in login_page.text

        invalid_login = client.post(
            "/admin/login",
            data={"username": "", "password": ""},
            follow_redirects=False,
        )
        assert invalid_login.status_code == 422

        login_submit = client.post(
            "/admin/login",
            data={"username": "admin", "password": "demo"},
            follow_redirects=False,
        )
        assert login_submit.status_code == 303
        assert login_submit.headers.get("location") == "/admin/assignments"
        assert "admin_session=" in login_submit.headers.get("set-cookie", "")


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
    assert "Решения кандидатов" in page_response.text
    assert submission_id in page_response.text
    assert table_response.status_code == 200
    assert submission_id in table_response.text
    assert second_submission_id not in table_response.text
    assert detail_response.status_code == 200
    assert "Вероятность помощи ИИ" in detail_response.text
    assert "Вероятность" in detail_response.text
    assert export_response.status_code == 200
    assert "Скачать" in export_response.text
    assert "/exports/" in export_response.text


@pytest.mark.integration
def test_admin_assignments_pages_create_edit_and_copy_link() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-admin-assignments-pages",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        _candidate_public_id, existing_assignment_id = seed_candidate_and_assignment(client=client)

        listing = client.get("/admin/assignments")
        assert listing.status_code == 200
        assert "Добавить задачу" in listing.text
        assert existing_assignment_id in listing.text
        assert f"/candidate/assignments/{existing_assignment_id}/apply" in listing.text

        create_response = client.post(
            "/admin/assignments",
            data={
                "title": "Новый Assignment",
                "description": "Описание нового задания",
                "language": "ru",
                "is_active": "on",
                "task_schema_json": json.dumps(default_task_schema(), ensure_ascii=False),
            },
            follow_redirects=False,
        )
        assert create_response.status_code == 303
        location = create_response.headers["location"]
        assert location.startswith("/admin/assignments/asg_")

        edit_page = client.get(location)
        assert edit_page.status_code == 200
        assert "Редактирование задачи" in edit_page.text
        assert "Сохранено." in edit_page.text

        assignment_id = location.split("/")[3]
        update_response = client.post(
            f"/admin/assignments/{assignment_id}",
            data={
                "title": "Обновленное название",
                "description": "Обновленное описание",
                "language": "ru",
                "task_schema_json": json.dumps(default_task_schema(), ensure_ascii=False),
            },
            follow_redirects=False,
        )
        assert update_response.status_code == 303

        listing_after_update = client.get("/admin/assignments")
        assert listing_after_update.status_code == 200
        assert "Обновленное название" in listing_after_update.text

        delete_response = client.post(
            f"/admin/assignments/{assignment_id}/delete",
            follow_redirects=False,
        )
        assert delete_response.status_code == 303
        assert delete_response.headers.get("location") == "/admin/assignments?deleted=1"

        listing_after_delete = client.get("/admin/assignments")
        assert listing_after_delete.status_code == 200
        assert assignment_id not in listing_after_delete.text


@pytest.mark.integration
def test_delete_assignment_is_blocked_when_submission_exists() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-admin-assignments-delete-blocked",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        candidate_public_id, assignment_id = seed_candidate_and_assignment(client=client)
        create_submission = client.post(
            "/submissions",
            json={
                "source_external_id": "delete-blocked-1",
                "candidate_public_id": candidate_public_id,
                "assignment_public_id": assignment_id,
            },
        )
        assert create_submission.status_code == 200

        delete_response = client.post(
            f"/admin/assignments/{assignment_id}/delete",
            follow_redirects=False,
        )
        assert delete_response.status_code == 400
        assert "Нельзя удалить задачу" in delete_response.text


@pytest.mark.integration
def test_public_fixed_assignment_apply_page_and_submit_flow() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-fixed-assignment-apply",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        _candidate_public_id, assignment_id = seed_candidate_and_assignment(client=client)

        page_response = client.get(f"/candidate/assignments/{assignment_id}/apply")
        assert page_response.status_code == 200
        assert "Отправка решения" in page_response.text
        assert "Скачать шаблон задания" in page_response.text
        assert f"/candidate/assignments/{assignment_id}/template.docx" in page_response.text

        submit_response = client.post(
            f"/candidate/assignments/{assignment_id}/submit",
            data={
                "first_name": "Public",
                "last_name": "Candidate",
            },
            files={
                "file": ("solution.py", b"print('ok')", "text/x-python"),
            },
        )
        assert submit_response.status_code == 200
        assert "Работа принята" in submit_response.text
        assert "/candidate/apply/result/sub_" in submit_response.text


@pytest.mark.integration
def test_assignment_template_download_and_not_found_paths() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-assignment-template-download",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )
    missing_assignment_id = "asg_01H0000000000000000000000"

    with TestClient(app) as client:
        _candidate_public_id, assignment_id = seed_candidate_and_assignment(client=client)

        template_response = client.get(f"/candidate/assignments/{assignment_id}/template.docx")
        assert template_response.status_code == 200
        assert template_response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert "Seed Assignment.docx" in template_response.headers.get("content-disposition", "")
        assert template_response.content.startswith(b"PK")

        missing_page = client.get(f"/candidate/assignments/{missing_assignment_id}/apply")
        missing_submit = client.post(
            f"/candidate/assignments/{missing_assignment_id}/submit",
            data={"first_name": "A", "last_name": "B"},
            files={"file": ("x.txt", b"x", "text/plain")},
        )
        missing_template = client.get(f"/candidate/assignments/{missing_assignment_id}/template.docx")

    assert missing_page.status_code == 404
    assert missing_submit.status_code == 404
    assert missing_template.status_code == 404


@pytest.mark.integration
def test_assignment_template_download_handles_unicode_title_filename() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-assignment-template-unicode-filename",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        create_response = client.post(
            "/assignments",
            json={
                "title": "Проверка структуры данных",
                "description": "Описание",
                "language": "ru",
                "task_schema": default_task_schema(),
            },
        )
        assert create_response.status_code == 200
        assignment_id = create_response.json()["assignment_public_id"]

        response = client.get(f"/candidate/assignments/{assignment_id}/template.docx")

    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert "filename*=UTF-8''" in disposition
    assert "%D0%9F" in disposition
    assert response.content.startswith(b"PK")


@pytest.mark.integration
def test_admin_settings_page_updates_telegram_assignment() -> None:
    role = validate_role("api")
    container = build_runtime_container(role)
    app = build_app(
        role=role.name,
        run_id="integration-admin-settings",
        worker_loop=container.worker_loop,
        api_deps=container.api_deps,
    )

    with TestClient(app) as client:
        _candidate_public_id, assignment_id = seed_candidate_and_assignment(client=client)

        settings_page = client.get("/admin/settings")
        assert settings_page.status_code == 200
        assert "Настройки Telegram" in settings_page.text

        save_response = client.post(
            "/admin/settings/telegram-assignment",
            data={"assignment_public_id": assignment_id},
            follow_redirects=False,
        )
        assert save_response.status_code == 303
        assert save_response.headers.get("location") == "/admin/settings?saved=1"

        configured = asyncio.run(
            container.repository.get_stream_cursor(stream=TELEGRAM_DEFAULT_ASSIGNMENT_STREAM)
        )
        assert configured == assignment_id

        clear_response = client.post(
            "/admin/settings/telegram-assignment",
            data={"assignment_public_id": ""},
            follow_redirects=False,
        )
        assert clear_response.status_code == 303

        cleared = asyncio.run(
            container.repository.get_stream_cursor(stream=TELEGRAM_DEFAULT_ASSIGNMENT_STREAM)
        )
        assert cleared == ""
