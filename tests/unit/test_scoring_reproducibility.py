import pytest

from app.domain.scoring import (
    CriteriaScore,
    TaskScore,
    deterministic_score_1_10,
    deterministic_weighted_overall_score_1_10,
)


@pytest.mark.unit
def test_deterministic_score_reproducibility_for_same_inputs() -> None:
    criteria = [
        CriteriaScore(name="correctness", score=8, weight=0.35),
        CriteriaScore(name="completeness", score=7, weight=0.25),
        CriteriaScore(name="quality", score=8, weight=0.20),
        CriteriaScore(name="edges", score=7, weight=0.20),
    ]
    first = deterministic_score_1_10(criteria=criteria)
    second = deterministic_score_1_10(criteria=criteria)
    assert first == second == 8


@pytest.mark.unit
def test_deterministic_weighted_overall_score_is_stable() -> None:
    task_scores = [
        TaskScore(task_id="task_1", score=8, weight=0.6),
        TaskScore(task_id="task_2", score=9, weight=0.4),
    ]
    first = deterministic_weighted_overall_score_1_10(task_scores=task_scores)
    second = deterministic_weighted_overall_score_1_10(task_scores=task_scores)
    assert first == second == 8
