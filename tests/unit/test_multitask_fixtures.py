from __future__ import annotations

import json
from pathlib import Path

import pytest


DATA_DIR = Path("tests/data")


@pytest.mark.unit
def test_multitask_fixture_files_exist_and_have_expected_shape() -> None:
    assignment = json.loads((DATA_DIR / "assignment_multitask_sample.json").read_text(encoding="utf-8"))
    answers = json.loads((DATA_DIR / "submission_answers_multitask_sample.json").read_text(encoding="utf-8"))
    ranges = json.loads((DATA_DIR / "expected_scoring_ranges_multitask.json").read_text(encoding="utf-8"))

    schema = assignment["assignment"]["task_schema"]
    assert schema["schema_version"] == "task-criteria:v1"
    tasks = schema["tasks"]
    assert isinstance(tasks, list) and len(tasks) >= 2
    assert abs(sum(float(task["weight"]) for task in tasks) - 1.0) <= 0.001
    for task in tasks:
        criteria = task["criteria"]
        assert abs(sum(float(item["weight"]) for item in criteria) - 1.0) <= 0.001

    assert set(answers["answers"].keys()) == {"weak", "medium", "strong", "edge"}
    assert set(ranges["ranges"].keys()) == {"weak", "medium", "strong", "edge"}


@pytest.mark.unit
def test_multitask_fixtures_do_not_copy_hidden_source_verbatim() -> None:
    hidden_source = Path("data_hidden/task_with_criteria_example.md")
    if not hidden_source.exists():
        pytest.skip("hidden reference file is unavailable")

    hidden_text = hidden_source.read_text(encoding="utf-8")
    hidden_lines = {line.strip() for line in hidden_text.splitlines() if len(line.strip()) >= 24}

    fixture_text = "\n".join(
        [
            (DATA_DIR / "assignment_multitask_sample.json").read_text(encoding="utf-8"),
            (DATA_DIR / "submission_answers_multitask_sample.json").read_text(encoding="utf-8"),
            (DATA_DIR / "expected_scoring_ranges_multitask.json").read_text(encoding="utf-8"),
        ]
    )

    for line in hidden_lines:
        assert line not in fixture_text
