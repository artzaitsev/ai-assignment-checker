from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest

from app.domain.evaluation_contracts import parse_task_schema
from app.domain.models import AssignmentSnapshot
from app.lib.docx.assignment_template import build_assignment_template_docx


def _assignment() -> AssignmentSnapshot:
    return AssignmentSnapshot(
        assignment_public_id="asg_01H0000000000000000000001",
        title="Проверка данных",
        description="Шаг 1\nШаг 2",
        language="ru",
        is_active=True,
        task_schema=parse_task_schema(
            {
                "schema_version": "task-criteria:v1",
                "tasks": [
                    {
                        "task_id": "task_1",
                        "title": "Опишите модель",
                        "weight": 0.5,
                        "criteria": [
                            {
                                "criterion_id": "correctness",
                                "description": "Описание критерия 1",
                                "weight": 1.0,
                            }
                        ],
                    },
                    {
                        "task_id": "task_2",
                        "title": "Напишите SQL",
                        "weight": 0.5,
                        "criteria": [
                            {
                                "criterion_id": "coverage",
                                "description": "Описание критерия 2",
                                "weight": 1.0,
                            }
                        ],
                    },
                ],
            }
        ),
    )


@pytest.mark.unit
def test_assignment_template_docx_contains_candidate_sections_without_criteria() -> None:
    payload = build_assignment_template_docx(_assignment())
    with ZipFile(BytesIO(payload)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "Шаблон выполнения задания" in document_xml
    assert "Название задания" in document_xml
    assert "Описание" in document_xml
    assert "Список заданий" in document_xml
    assert "Опишите модель" in document_xml
    assert "Напишите SQL" in document_xml
    assert document_xml.count("Ваш ответ:") == 2
    assert "correctness" not in document_xml
    assert "coverage" not in document_xml
    assert "Описание критерия 1" not in document_xml
    assert "Описание критерия 2" not in document_xml


@pytest.mark.unit
def test_assignment_template_docx_generation_is_deterministic_for_same_input() -> None:
    first = build_assignment_template_docx(_assignment())
    second = build_assignment_template_docx(_assignment())
    assert first == second


@pytest.mark.unit
def test_assignment_template_docx_fails_without_task_schema() -> None:
    assignment = AssignmentSnapshot(
        assignment_public_id="asg_01H0000000000000000000002",
        title="No schema",
        description="desc",
        language="ru",
        is_active=True,
        task_schema=None,
    )
    with pytest.raises(ValueError, match="task_schema"):
        build_assignment_template_docx(assignment)
